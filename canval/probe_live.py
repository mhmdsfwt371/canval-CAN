"""The second gate, moved into the nightly job.

WHY IT LIVES HERE AND NOT IN THE PAGE
-------------------------------------
Asking the tracking platform whether a device is really reporting needs a
token, and a token cannot go anywhere near a static page: anyone who
opens the page would have it. The usual answer is a small server holding
the secret, and the usual server costs money.

But nothing about this question needs to be answered at the instant it is
asked. "Does this CAN file actually deliver fuel level?" has the same
answer this morning as it did last night. So the nightly job asks on
everyone's behalf, writes the answer down, and the page reads it -- no
token in the browser, no server, no card.

WHAT IT ASKS
------------
For every catalogue file with installs, a handful of devices carrying it,
newest activity first. One filtered request covers a hundred devices, so
the whole estate costs a few dozen calls rather than thousands.

WHAT COUNTS AS EVIDENCE
-----------------------
A sensor is credited when a device carrying the file reports a value for
its parameter. Not whether the value is fresh -- a parked truck's odometer
is stale by definition and that proves nothing either way, which is the
trap this project already learned the hard way. The presence of the
parameter is what says the file decoded something.

    python -m canval.probe_live --sample 6

Missing or expired token: says so and exits cleanly. A nightly job that
fails because a token aged out is a nightly job people stop trusting.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import time
from collections import defaultdict

from .afaqy import BASE_URL, parse_unit_view
from .store import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS file_live (
    file_id   INTEGER PRIMARY KEY,
    sampled   INTEGER,      -- devices we asked about
    answered  INTEGER,      -- devices the platform knew
    reporting INTEGER,      -- devices that returned at least one signal
    signals   TEXT,         -- JSON: {signal name: devices reporting it}
    devices   TEXT,         -- JSON: the sampled devices, one entry each
    checked_at TEXT DEFAULT (datetime('now'))
);
"""


def token_state(token: str) -> tuple[bool, str]:
    """Read the expiry out of the token itself before spending calls."""
    try:
        body = token.split(".")[1]
        body += "=" * (-len(body) % 4)
        claims = json.loads(base64.urlsafe_b64decode(body))
        exp = int(claims.get("exp", 0))
    except Exception:                                   # noqa: BLE001
        return True, "token is not readable, trying anyway"
    left = exp - int(time.time())
    when = time.strftime("%Y-%m-%d", time.gmtime(exp))
    if left <= 0:
        return False, f"token expired on {when}"
    return True, f"token valid until {when} ({left // 86400} days left)"


def pick_sample(conn, per_file: int) -> dict[int, dict[str, str]]:
    """A few devices per file, spread across the vehicles sharing it.

    Recently active first, because a device that has not spoken in a
    year tells us nothing about the file and would waste the slot.

    Spread across configuration names, because a file is rarely one
    vehicle's. Sixty-eight files here are shared by more than six, and a
    car fitted under another vehicle's file reads all its evidence from
    that file -- so a sample taken purely by recency filled every slot
    from whichever vehicle happened to have the busiest devices, and the
    borrower's page showed a stranger's plates. One device from each
    vehicle first, then the rest by recency.

    Returns imei -> configuration name, so what comes back can be shown
    against the vehicle it belongs to.
    """
    per_cfg: dict[int, dict[str, list[str]]] = defaultdict(
        lambda: defaultdict(list))
    names: dict[str, str] = {}
    for row in conn.execute(
            """SELECT file_id, imei, COALESCE(config_name, '') cfg
                 FROM device_can
                WHERE file_id IS NOT NULL
                ORDER BY file_id, COALESCE(last_activity, 0) DESC"""):
        slot = per_cfg[row["file_id"]][row["cfg"]]
        if row["imei"] not in slot:
            slot.append(row["imei"])
            names[row["imei"]] = row["cfg"]

    by_file: dict[int, dict[str, str]] = {}
    for fid, groups in per_cfg.items():
        # Round by round: the best device from each vehicle, then the
        # next best from each, until the slots run out. The groups are
        # already in recency order, so a file with one vehicle behaves
        # exactly as it did before.
        picked: list[str] = []
        deepest = max(len(v) for v in groups.values())
        for depth in range(deepest):
            for cfg in groups:
                if len(picked) >= per_file:
                    break
                if depth < len(groups[cfg]):
                    picked.append(groups[cfg][depth])
            if len(picked) >= per_file:
                break
        by_file[fid] = {i: names[i] for i in picked}
    return by_file


class Pro:
    """Just enough of the platform's API for this one question."""

    def __init__(self, token: str, base_url: str = BASE_URL):
        import requests
        self.token = token
        self.base = base_url.rstrip("/")
        self.s = requests.Session()
        self.s.headers.update({"Accept": "application/json, text/plain, */*",
                               "Content-Type": "application/json"})

    def units(self, imeis: list[str]) -> list[dict]:
        # The payload is a JSON string inside a `data` field: encoded
        # twice. Without the imei filter the endpoint answers 200 with an
        # empty list even to an administrator -- learned the hard way.
        resp = self.s.post(
            f"{self.base}/v1/units",
            params={"token": self.token},
            json={"data": json.dumps({
                "filters": {"imei": {"value": imeis, "op": "in"}},
                "offset": 0, "limit": max(len(imeis), 100), "simplify": 1,
                "projection": ["basic", "last_update", "sensors"],
            })},
            timeout=120,
        )
        resp.raise_for_status()
        data = (resp.json() or {}).get("data")
        if isinstance(data, dict):
            data = data.get("items") or data.get("units") or []
        return [u for u in (data or []) if isinstance(u, dict)]


def readings_of(view) -> list[dict]:
    """Every sensor configured on the unit, carrying a value or not.

    A configured sensor with nothing arriving is not noise to be filtered
    out -- it is the most useful line on the page. "Fuel Level is set up
    on this unit and silent" is a different fact from "Fuel Level was
    never set up", and only the first one means somebody should look at
    the install. So the list is the unit's own sensor definitions, and the
    value is attached where one exists.

    Bare parameter keys like sensor_12289 stay out: those are wire-level
    values with no sensor configured against them, so there is nothing for
    a reader to recognise and nobody asked whether sensor_12289 works.

    Values are carried raw. The conversion lives in the payload and
    belongs to the adapter, not here.
    """
    seen = {p.key: p for p in view.parameters if p.value not in (None, "")}
    out = []
    for s in view.specs:
        if not s.name:
            continue
        p = seen.get(s.param) if s.param else None
        entry = {"n": s.name}
        if p is not None:
            entry["v"] = p.value
            entry["t"] = p.changed_at
        out.append(entry)
    # Live ones first, then the silent ones: the eye should land on what
    # is working before it reaches what is not.
    return sorted(out, key=lambda r: ("v" not in r, r["n"]))


def signals_of(view) -> list[str]:
    """Which named sensors this unit is actually carrying values for.

    Named only: a bare parameter key like sensor_12289 is a wire-level
    value with no sensor configured against it, and nobody asked whether
    sensor_12289 works.
    """
    present = {p.key for p in view.parameters if p.value not in (None, "")}
    return sorted({s.name for s in view.specs
                   if s.param and s.param in present and s.name})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="canval.probe_live", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample", type=int, default=6,
                    help="devices to check per catalogue file (default 6)")
    ap.add_argument("--batch", type=int, default=100,
                    help="devices per request (default 100)")
    ap.add_argument("--db", default=os.environ.get("CANVAL_DB", "canval.db"))
    args = ap.parse_args(argv)

    token = os.environ.get("AFAQY_TOKEN", "").strip()
    if not token:
        print("  no AFAQY_TOKEN set -- skipping the live check.\n"
              "  the catalogue and the install index are unaffected.")
        return 0

    alive, note = token_state(token)
    print(f"  {note}")
    if not alive:
        print("  skipping the live check. Set a fresh AFAQY_TOKEN secret to "
              "resume it.")
        return 0

    with connect(args.db) as conn:
        conn.executescript(SCHEMA)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(file_live)")}
        if "devices" not in cols:
            conn.execute("ALTER TABLE file_live ADD COLUMN devices TEXT")
        by_file = pick_sample(conn, args.sample)

        wanted: list[str] = []
        seen: set[str] = set()
        for imeis in by_file.values():
            for i in imeis:
                if i not in seen:
                    seen.add(i)
                    wanted.append(i)

        print(f"  {len(by_file)} files with installs, "
              f"{len(wanted)} devices to ask about")

        pro = Pro(token)
        views: dict[str, object] = {}
        # the unit as the platform sent it, kept beside the parsed view for
        # the handful of fields the adapter has no reason to model
        raws: dict[str, dict] = {}
        calls = failed = 0
        for start in range(0, len(wanted), args.batch):
            batch = wanted[start:start + args.batch]
            try:
                for unit in pro.units(batch):
                    view = parse_unit_view({"data": unit})
                    if view.imei:
                        views[str(view.imei)] = view
                        raws[str(view.imei)] = unit
                calls += 1
            except Exception as exc:                    # noqa: BLE001
                failed += 1
                if failed <= 3:
                    print(f"    batch failed: {type(exc).__name__}: {exc}")
                if failed > 10:
                    print("    too many failures, stopping the live check")
                    break
            if calls and calls % 10 == 0:
                print(f"    {start + len(batch)}/{len(wanted)} devices, "
                      f"{len(views)} found")

        rows = 0
        for file_id, cfg_of in by_file.items():
            imeis = list(cfg_of)
            answered = [views[i] for i in imeis if i in views]
            tally: dict[str, int] = defaultdict(int)
            reporting = 0
            devices = []
            for view in answered:
                readings = readings_of(view)
                live = [r for r in readings if "v" in r]
                if live:
                    reporting += 1
                for r in live:
                    tally[r["n"]] += 1
                raw = raws.get(str(view.imei or "")) or {}
                devices.append({
                    "i": str(view.imei or ""),
                    "u": view.name or "",
                    # The configuration this device carries. A shared file
                    # answers for several vehicles, and without this the
                    # page cannot tell which of them a device belongs to.
                    "c": cfg_of.get(str(view.imei or ""), ""),
                    # Who the device belongs to, so a lead can be taken to
                    # the right customer without a second lookup. The
                    # platform returns these as plain strings alongside the
                    # unit; nothing is inferred here.
                    "o": str(raw.get("owner") or ""),
                    "cb": str(raw.get("creator") or ""),
                    "t": view.last_message or 0,
                    # every configured sensor, live or silent
                    "r": readings[:60],
                })
            # Newest first: the three at the top are the ones worth opening
            # to check a file by hand.
            devices.sort(key=lambda d: -(d["t"] or 0))

            conn.execute(
                """INSERT INTO file_live
                       (file_id, sampled, answered, reporting, signals,
                        devices, checked_at)
                   VALUES (?,?,?,?,?,?, datetime('now'))
                   ON CONFLICT(file_id) DO UPDATE SET
                       sampled=excluded.sampled, answered=excluded.answered,
                       reporting=excluded.reporting, signals=excluded.signals,
                       devices=excluded.devices, checked_at=excluded.checked_at""",
                (file_id, len(imeis), len(answered), reporting,
                 json.dumps(dict(sorted(tally.items(), key=lambda kv: -kv[1])),
                            ensure_ascii=False),
                 json.dumps(devices[:8], ensure_ascii=False)))
            rows += 1

        live = conn.execute(
            "SELECT COUNT(*) n FROM file_live WHERE reporting > 0").fetchone()["n"]

    print(f"\n  {rows} files checked, {live} confirmed reporting on at "
          f"least one device")
    if failed:
        print(f"  {failed} request(s) failed; those files kept their "
              f"previous result")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
