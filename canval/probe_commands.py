"""The third piece of evidence: what the operator can actually press.

WHY THIS EXISTS
---------------
Two facts already answer "can this car do door control / car sharing":

    the CAN file      -- the vehicle speaks the right bus
    the XDM script    -- the device is configured to drive the lock

Both are read from the device-management side. Neither says whether
anyone can USE it. A device can carry a lock script and still have no
button in the tracking platform, in which case the customer's answer is
"no" no matter how good the wiring is.

So this asks the platform directly: which commands are configured on the
unit. That is the button the operator sees. Having it is not proof the
lock is wired -- that is what the script says -- but not having it means
the feature is unreachable today.

WHAT COUNTS
-----------
Two families, not one name. The estate spells the same command eleven
different ways:

    CloseDoor&ignitionOFF      1914 units
    CLoseDoor&ignitionOFF        25   <- capital L, typed by hand
    Closedoors&Ignition OFF       2
    close door and Ignition Off   1
    Lock Door                     4   <- a different wording entirely

An exact string match finds the first and misses thirty units, and misses
"Lock Door" completely. So names are folded -- case, punctuation, filler
words, plurals -- and then sorted into a lock family and an unlock
family. A unit needs one from each before the page will claim the feature.

ABSENCE IS RECORDED, NOT ASSUMED
--------------------------------
A device that answered with no commands gets a "(none)" row, the same way
device_script marks a device read with no script. A device that was never
reached gets no row at all. Those are different facts and the table keeps
them apart -- the trap this project has already fallen into twice.

    python -m canval.probe_commands

Missing or expired token: says so and exits cleanly, storing nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict

from .afaqy import BASE_URL
from .probe_live import token_state
from .store import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS device_command (
    imei     TEXT NOT NULL,
    name     TEXT NOT NULL,   -- as typed in the platform; '(none)' = read,
                              -- answered, and carries no command at all
    norm     TEXT,            -- folded key, for counting past the typos
    family   TEXT,            -- 'lock' | 'unlock' | ''
    seen_at  TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (imei, name)
);
CREATE INDEX IF NOT EXISTS ix_devcmd_family ON device_command(family);
"""


# ------------------------------------------------------------------ naming

def norm(name) -> str:
    """Fold a command name to a comparable key.

    Mirrors the browser-side helper exactly, so a name folded here and a
    name folded there always land on the same key.
    """
    s = re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()
    s = " ".join(w for w in s.split() if w not in ("and", "the"))
    s = re.sub(r"\s+", "", s)
    return (s.replace("doors", "door")
             .replace("records", "record")
             .replace("engine", "ignition"))


def family(folded: str) -> str:
    """Which door family a folded name belongs to, if any.

    Unlock is tested first on purpose: "unlockdoor" contains "lockdoor",
    so the other order would file every unlock command as a lock.
    """
    if "unlockdoor" in folded or "opendoor" in folded:
        return "unlock"
    if "lockdoor" in folded or "closedoor" in folded:
        return "lock"
    return ""


# --------------------------------------------------------------- platform

class Pro:
    """Just enough of the platform's API for this one question."""

    def __init__(self, token: str, base_url: str = BASE_URL):
        import requests
        self.token = token
        self.base = base_url.rstrip("/")
        self.s = requests.Session()
        self.s.headers.update({"Accept": "application/json, text/plain, */*",
                               "Content-Type": "application/json"})

    def units(self, imeis: list[str], simplify: int = 0) -> list[dict]:
        # The payload is a JSON string inside a `data` field: encoded
        # twice. The imei filter is not an optimisation -- without it the
        # endpoint answers 200 with an empty list even to an administrator.
        resp = self.s.post(
            f"{self.base}/v1/units",
            params={"token": self.token},
            json={"data": json.dumps({
                "filters": {"imei": {"value": imeis, "op": "in"}},
                "offset": 0, "limit": max(len(imeis), 100),
                "simplify": simplify,
                "projection": ["basic", "commands"],
            })},
            timeout=120,
        )
        resp.raise_for_status()
        data = (resp.json() or {}).get("data")
        if isinstance(data, dict):
            data = data.get("items") or data.get("units") or []
        return [u for u in (data or []) if isinstance(u, dict)]


def imei_of(unit: dict) -> str:
    """IMEI under either shape.

    simplify=1 shortens the field names -- imei becomes `i` -- and the two
    modes are not otherwise interchangeable, so both are accepted rather
    than betting on one.
    """
    return str(unit.get("imei") or unit.get("i") or "").strip()


def names_of(unit: dict) -> list[str]:
    """Command names configured on the unit, in the platform's own order."""
    out = []
    for c in unit.get("commands") or []:
        if isinstance(c, dict):
            n = str(c.get("name") or "").strip()
        else:
            n = str(c or "").strip()
        if n and n not in out:
            out.append(n)
    return out


def carries_commands(units: list[dict]) -> bool:
    """Did the projection actually come back?

    An empty command list on one unit is a real answer. A `commands` key
    missing from every unit means the projection was ignored, which is a
    completely different thing and must never be written down as "no
    device has any commands".
    """
    return any("commands" in u for u in units)


# ------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="canval.probe_commands",
                                 description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batch", type=int, default=100,
                    help="devices per request (default 100)")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after this many devices (0 = all of them)")
    ap.add_argument("--db", default=os.environ.get("CANVAL_DB", "canval.db"))
    args = ap.parse_args(argv)

    token = os.environ.get("AFAQY_TOKEN", "").strip()
    if not token:
        print("  no AFAQY_TOKEN set -- skipping the command check.\n"
              "  the catalogue, the install index and the scripts are "
              "unaffected.")
        return 0

    alive, note = token_state(token)
    print(f"  {note}")
    if not alive:
        print("  skipping the command check. Set a fresh AFAQY_TOKEN secret "
              "to resume it.")
        return 0

    with connect(args.db) as conn:
        conn.executescript(SCHEMA)

        wanted = [r["imei"] for r in conn.execute(
            """SELECT DISTINCT imei FROM device_can
                WHERE imei IS NOT NULL AND imei <> ''
                ORDER BY COALESCE(last_activity, 0) DESC""")]
        if args.limit:
            wanted = wanted[:args.limit]
        print(f"  {len(wanted)} indexed devices to ask about")
        if not wanted:
            print("  nothing indexed yet -- run the sweep first.")
            return 0

        pro = Pro(token)

        # One probe before spending two hundred calls: if the projection
        # is refused there is nothing to collect and pretending otherwise
        # would write "no commands" across the whole estate.
        probe = wanted[:args.batch]
        mode = 0
        try:
            units = pro.units(probe, simplify=0)
            if not carries_commands(units):
                print("    simplify=0 returned no `commands` key -- "
                      "retrying with simplify=1")
                units = pro.units(probe, simplify=1)
                mode = 1
        except Exception as exc:                        # noqa: BLE001
            print(f"  the platform refused the request: "
                  f"{type(exc).__name__}: {exc}")
            print("  nothing written. The other evidence is unaffected.")
            return 0

        if not units:
            print("  the platform knew none of these devices. "
                  "Nothing written.")
            return 0

        if not carries_commands(units):
            print("\n  THE PLATFORM DOES NOT RETURN `commands` ON THIS "
                  "PROJECTION.")
            print(f"  Asked for ['basic','commands'], got: "
                  f"{sorted(units[0].keys())}")
            print("  Nothing written -- an absent field is not the same as "
                  "a device with no commands.")
            return 0

        print(f"  the projection works (simplify={mode}); collecting")

        fetched: dict[str, list[str]] = {}
        for u in units:
            i = imei_of(u)
            if i:
                fetched[i] = names_of(u)

        calls, failed = 1, 0
        rest = wanted[args.batch:]
        for start in range(0, len(rest), args.batch):
            batch = rest[start:start + args.batch]
            try:
                for u in pro.units(batch, simplify=mode):
                    i = imei_of(u)
                    if i:
                        fetched[i] = names_of(u)
                calls += 1
            except Exception as exc:                    # noqa: BLE001
                failed += 1
                if failed <= 3:
                    print(f"    batch failed: {type(exc).__name__}: {exc}")
                if failed > 10:
                    print("    too many failures, stopping early. What was "
                          "already read is kept.")
                    break
            if calls % 20 == 0:
                print(f"    {args.batch + start + len(batch)}/{len(wanted)} "
                      f"devices, {len(fetched)} answered")

        # Write. A device that answered gets its old rows dropped first:
        # a command removed in the platform has to disappear here too, or
        # the page keeps promising a button that no longer exists.
        rows = with_lock = with_unlock = both = empty = 0
        for imei, names in fetched.items():
            conn.execute("DELETE FROM device_command WHERE imei=?", (imei,))
            fams = set()
            if not names:
                conn.execute(
                    """INSERT INTO device_command (imei, name, norm, family,
                                                   seen_at)
                       VALUES (?, '(none)', '', '', datetime('now'))""",
                    (imei,))
                empty += 1
            for n in names:
                f = norm(n)
                fam = family(f)
                fams.add(fam)
                conn.execute(
                    """INSERT INTO device_command (imei, name, norm, family,
                                                   seen_at)
                       VALUES (?,?,?,?, datetime('now'))
                       ON CONFLICT(imei, name) DO UPDATE SET
                         norm=excluded.norm, family=excluded.family,
                         seen_at=datetime('now')""",
                    (imei, n, f, fam))
                rows += 1
            if "lock" in fams:
                with_lock += 1
            if "unlock" in fams:
                with_unlock += 1
            if {"lock", "unlock"} <= fams:
                both += 1

        answered = len(fetched)
        print(f"\n  {answered} of {len(wanted)} devices answered, "
              f"{rows} command rows written")
        print(f"  {empty} answered with no commands at all")
        print(f"  {with_unlock} carry an unlock command, "
              f"{with_lock} carry a lock command")
        print(f"  {both} carry BOTH -- these are the ones the page can "
              f"call car-sharing ready")
        if answered:
            print(f"  {round(100 * both / answered, 1)}% of the devices "
                  f"that answered")
        if failed:
            print(f"  {failed} request(s) failed; those devices kept their "
                  f"previous rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
