"""Rewire canval to read effective CAN values, keyed by bus.

Run once, from D:\\canval, with effective.py sitting beside it:

    python apply_patch.py

Everything it touches is copied to canval_backup_<timestamp>\\ first, and
every edit is anchored on an exact block of the current source. If an
anchor is missing the script stops without writing anything, because a
half-applied patch is worse than none.
"""

from __future__ import annotations

import datetime as _dt
import py_compile
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PKG = ROOT / "canval"
HERE = Path(__file__).resolve().parent


# --------------------------------------------------------------- the edits

STORE_OLD_TABLE = """-- One row per device PER BUS. A device carries a separate vehicle-model
-- override for CAN1 and CAN2, and they are often different files: one
-- real device had 3355 on one bus and 1967 on the other. Keying by imei
-- alone silently kept whichever arrived first and dropped the rest.
CREATE TABLE IF NOT EXISTS device_can (
    imei         TEXT NOT NULL,
    element_name TEXT NOT NULL,
    file_id      INTEGER,
    raw_value    TEXT,
    hardware     TEXT,
    config_name  TEXT,
    last_activity INTEGER,
    seen_at      TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (imei, element_name)
);"""

STORE_NEW_TABLE = """-- One row per device PER BUS, keyed by the bus itself.
--
-- The previous key was (imei, element_name), which looked right and was
-- not: CAN1 and CAN2 both call their element "Vehicle model", so the
-- second row overwrote the first and one of the two files disappeared.
-- The whole 6475-device sweep produced zero devices with more than one
-- row, on an estate where dual-bus fitments are ordinary.
--
-- `inherited` records where the value came from. Most devices inherit
-- theirs from the configuration template rather than overriding it, and
-- an earlier reader that only looked at overrides therefore reported 95%
-- of the estate as never fitted.
--
-- `port_function` is context, not a filter. A port set to Sleep can still
-- carry an assigned model, and the question being answered is whether the
-- file is assigned -- not whether the port happens to be awake today.
CREATE TABLE IF NOT EXISTS device_can (
    imei          TEXT    NOT NULL,
    bus           INTEGER NOT NULL,   -- 1 = CAN1, 2 = CAN2, 0 = nothing found
    element_name  TEXT    NOT NULL,
    element_id    INTEGER,
    file_id       INTEGER,
    raw_value     TEXT,
    inherited     INTEGER,            -- 1 = from the template, 0 = set here
    port_function TEXT,
    hardware      TEXT,
    config_name   TEXT,
    template_id   INTEGER,
    last_activity INTEGER,
    seen_at       TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (imei, bus)
);"""

STORE_OLD_CONNECT = """    try:
        conn.executescript(SCHEMA)
        yield conn"""

STORE_NEW_CONNECT = """    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        yield conn"""

STORE_APPEND = '''

# ------------------------------------------------------------- migration

def _migrate(conn) -> None:
    """Move an old device_can aside rather than reshaping it.

    The v1 rows cannot be salvaged: they were keyed by element name, so a
    dual-bus device lost one of its two files, and they only ever recorded
    overrides, so an inherited assignment reads as absent. Converting them
    would carry both errors forward wearing a new schema.

    The old table is kept, not dropped. Diffing it against the next sweep
    is the clearest possible statement of what the tool was missing, and
    that is worth more than the disk it costs.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(device_can)")}
    if not cols or "bus" in cols:
        return
    stamp = conn.execute("SELECT strftime('%Y%m%d_%H%M%S','now')").fetchone()[0]
    conn.execute(f"ALTER TABLE device_can RENAME TO device_can_v1_{stamp}")
    conn.executescript(SCHEMA)


def record_device_bus(conn, imei: str, bus: int, *, element_name: str = "(none)",
                      element_id=None, file_id=None, raw_value=None,
                      inherited=None, port_function=None, hardware=None,
                      config_name=None, template_id=None,
                      last_activity=None) -> None:
    """Record one bus of one device. Bus 0 means nothing was assigned."""
    conn.execute(
        """INSERT INTO device_can
           (imei, bus, element_name, element_id, file_id, raw_value,
            inherited, port_function, hardware, config_name, template_id,
            last_activity, seen_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'))
           ON CONFLICT(imei, bus) DO UPDATE SET
             element_name=excluded.element_name,
             element_id=excluded.element_id, file_id=excluded.file_id,
             raw_value=excluded.raw_value, inherited=excluded.inherited,
             port_function=excluded.port_function,
             hardware=excluded.hardware, config_name=excluded.config_name,
             template_id=excluded.template_id,
             last_activity=excluded.last_activity, seen_at=datetime('now')""",
        (imei, int(bus), element_name or "(none)", element_id, file_id,
         raw_value, inherited, port_function, hardware, config_name,
         template_id, last_activity),
    )


def coverage_detail(conn) -> dict:
    """Coverage split by where the value came from.

    The inherited count is the headline: it is everything the previous
    reader was blind to.
    """
    row = conn.execute(
        """SELECT COUNT(DISTINCT imei) devices,
                  COUNT(DISTINCT CASE WHEN file_id IS NOT NULL THEN imei END) fitted,
                  SUM(file_id IS NOT NULL) buses,
                  SUM(file_id IS NOT NULL AND inherited = 1) inherited,
                  SUM(file_id IS NOT NULL AND inherited = 0) overridden
             FROM device_can""").fetchone()
    dual = conn.execute(
        """SELECT COUNT(*) n FROM (
             SELECT imei FROM device_can WHERE file_id IS NOT NULL
             GROUP BY imei HAVING COUNT(*) > 1)""").fetchone()["n"]
    orphan = conn.execute(
        """SELECT COUNT(*) n FROM device_can d
            WHERE d.file_id IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM can_files f
                               WHERE f.file_id = d.file_id)""").fetchone()["n"]
    total = row["devices"] or 0
    fitted = row["fitted"] or 0
    return {
        "devices": total, "fitted": fitted, "unfitted": total - fitted,
        "pct": round(100 * fitted / total, 1) if total else 0.0,
        "bus_entries": row["buses"] or 0,
        "inherited": row["inherited"] or 0,
        "overridden": row["overridden"] or 0,
        "dual_bus_devices": dual,
        "pointing_at_missing_file": orphan,
    }
'''

INDEX_OLD_DEF = """def build_device_index(
    client,
    db_path: str,"""

INDEX_NEW_DEF = """def _build_device_index_v1(
    client,
    db_path: str,"""

INDEX_APPEND = '''

def build_device_index(client, db_path: str, hardware_ids=None,
                       active_since_days: int | None = 60,
                       concurrency: int = 6, sample_configs: int = 0,
                       verify_sample: int = 2, limit: int | None = None,
                       progress=None) -> dict:
    """Sweep devices and record the CAN file each one effectively runs.

    The v1 reader is kept below as `_build_device_index_v1` for reference.
    It read overrides only, and keyed rows by element name; both were
    wrong in ways that produced confident, plausible, false answers. See
    canval/effective.py for what replaced it and why.

    `sample_configs` is accepted and ignored. Sampling was already shown
    to write off configurations of hundreds of devices on the strength of
    the first three, and the new reader is cheap enough that the shortcut
    has nothing left to buy.
    """
    from .effective import sweep_devices

    return sweep_devices(
        client, db_path, hardware_ids=hardware_ids,
        active_since_days=active_since_days, concurrency=concurrency,
        verify_sample=verify_sample, limit=limit, progress=progress)
'''

XDM_OLD = """            if not resp.content:
                return None
            return resp.json()"""

XDM_NEW = """            if not resp.content:
                return None

            # The gateway answers 200 with the web app's HTML for any path
            # it does not implement, so a wrong path arrives looking like a
            # success and only fails at the JSON parse. That surfaced as
            # "Expecting value: line 1 column 1", which reads like a broken
            # server rather than a typo and cost several hours of chasing
            # the wrong thing. Name it here instead.
            ctype = resp.headers.get("Content-Type", "")
            if "json" not in ctype.lower():
                raise XdmError(
                    f"{method} {path} -> {resp.status_code} but the body is "
                    f"{ctype.split(';')[0] or 'untyped'}, not JSON "
                    f"({len(resp.content)} bytes). This endpoint serves the "
                    f"web app for unknown paths, so the path is almost "
                    f"certainly wrong rather than the server being down."
                )
            return resp.json()"""

CLI_OLD_ARGS = """    pi.add_argument("--active-days", type=int, default=60,
                    help="skip devices with no activity in this many days")"""

CLI_NEW_ARGS = """    pi.add_argument("--active-days", type=int, default=60,
                    help="skip devices with no activity in this many days")
    pi.add_argument("--limit", type=int, default=None, metavar="N",
                    help="stop after N devices -- for a trial run on one "
                         "hardware family before committing to the estate")
    pi.add_argument("--verify", type=int, default=2, metavar="N",
                    help="re-read N devices per template the slow way and "
                         "compare, to prove the template cache (0 disables)")"""

CLI_OLD_CALL = """    stats = build_device_index(
        client,
        _db(settings),
        hardware_ids=hw,
        active_since_days=args.active_days,
        concurrency=settings.concurrency if settings else 6,
        sample_configs=args.sample_configs,
        progress=lambda d, t: print(f"  {d}/{t} devices resolved", flush=True),
    )"""

CLI_NEW_CALL = """    stats = build_device_index(
        client,
        _db(settings),
        hardware_ids=hw,
        active_since_days=args.active_days,
        concurrency=settings.concurrency if settings else 6,
        sample_configs=args.sample_configs,
        verify_sample=args.verify,
        limit=args.limit,
        progress=lambda d, t: print(f"  {d}/{t} devices read", flush=True),
    )
    if stats.get("verify_mismatches"):
        print("\\n  VERIFICATION FAILED -- the template cache disagreed with "
              "the devices:")
        for p_ in stats["verify_problems"]:
            print(f"    - {p_}")
        print("  Treat this index as unreliable until that is explained.\\n")"""


# (file, [(anchor, replacement)], text to append, marker proving it ran)
EDITS = [
    ("canval/store.py", [(STORE_OLD_TABLE, STORE_NEW_TABLE),
                         (STORE_OLD_CONNECT, STORE_NEW_CONNECT)],
     STORE_APPEND, "def record_device_bus("),
    ("canval/index.py", [(INDEX_OLD_DEF, INDEX_NEW_DEF)],
     INDEX_APPEND, "from .effective import sweep_devices"),
    ("canval/xdm.py",   [(XDM_OLD, XDM_NEW)], None, None),
    ("canval/cli.py",   [(CLI_OLD_ARGS, CLI_NEW_ARGS),
                         (CLI_OLD_CALL, CLI_NEW_CALL)], None, None),
]


def main() -> int:
    if not PKG.is_dir():
        print(f"No canval package at {PKG}. Run this from D:\\canval.")
        return 2
    source = HERE / "effective.py"
    if not source.is_file():
        print("effective.py must sit next to this script.")
        return 2

    # ---- check every anchor before touching anything
    planned = []
    for rel, pairs, append, marker in EDITS:
        path = ROOT / rel
        if not path.is_file():
            print(f"missing: {rel}")
            return 2
        text = path.read_text(encoding="utf-8")
        new = text
        for old, repl in pairs:
            if repl in new:
                continue                    # already applied
            if old not in new:
                print(f"\n{rel}: anchor not found ->\n"
                      f"  {old.splitlines()[0][:70]}\n"
                      "Nothing was written. The file differs from the version "
                      "this patch was built against.")
                return 1
            new = new.replace(old, repl, 1)
        if append and marker not in new:
            new = new.rstrip("\n") + "\n" + append
        if new != text:
            planned.append((path, new))

    # ---- back up, then write
    if not planned:
        print("Nothing to do: every edit is already in place.")
        shutil.copy2(source, PKG / "effective.py")
        return 0

    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = ROOT / f"canval_backup_{stamp}"
    backup.mkdir()
    for rel, *_ in EDITS:
        shutil.copy2(ROOT / rel, backup / Path(rel).name)
    print(f"backed up to {backup.name}\\")

    for path, new in planned:
        path.write_text(new, encoding="utf-8")
        print(f"  patched {path.relative_to(ROOT)}")
    shutil.copy2(source, PKG / "effective.py")
    print(f"  installed canval\\effective.py")

    ok = True
    for rel in [e[0] for e in EDITS] + ["canval/effective.py"]:
        try:
            py_compile.compile(str(ROOT / rel), doraise=True)
        except Exception as exc:                        # noqa: BLE001
            ok = False
            print(f"  SYNTAX ERROR in {rel}: {exc}")
    if not ok:
        print(f"\nRestore from {backup.name} before doing anything else.")
        return 1

    print("\nAll four modules compile. Next:\n"
          "  python -m canval.cli index --hardware 98 --limit 400 --active-days 0\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
