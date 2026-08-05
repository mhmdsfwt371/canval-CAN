"""Turn the database into the handful of files the web page reads.

WHY STATIC FILES
----------------
Everything the team asks this tool is a read: "is this vehicle covered,
and is it fitted anywhere?" Nobody types anything back into it. Data that
is written once a night and read all day does not need a database server
behind it -- it needs files, and files are the one thing GitHub Pages
hosts for nothing, with no account to create, no card to add and no
machine left running.

So the nightly job writes JSON here, commits it, and the page fetches it.
The whole catalogue is about a megabyte, which the browser holds in
memory and searches instantly. That is faster than any query round-trip
would have been.

WHAT IS DELIBERATELY LEFT OUT
-----------------------------
IMEIs, SIM numbers and per-device rows never leave the database. The page
needs counts and names to answer its question; publishing a fleet's
device identifiers to a URL would be a different and much worse decision.
Configuration names appear only inside the hint worklists, where the text
is the evidence and stripping it would make the lead unusable -- and only
as one sample per make.

    python -m canval.export_site --out docs/data
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .store import connect


def _round_kb(n) -> int | None:
    return int(n) if n else None


GENERIC = ("j1939", "fms", "obd", "test", "ack")


def _is_generic(name: str) -> bool:
    """The handful of protocol files that fit no particular vehicle."""
    head = (name or "").strip().lower()
    return any(head.startswith(g) for g in GENERIC)


def _active_expr() -> str:
    """last_activity holds epoch seconds or milliseconds depending on the
    sweep that wrote it; treat anything implausibly large as millis."""
    cutoff = "strftime('%s','now','-30 days')"
    return (f"(CASE WHEN last_activity > 100000000000 "
            f"THEN last_activity/1000 ELSE last_activity END) >= {cutoff}")


def export_vehicles(conn) -> list[dict]:
    """One entry per catalogue file, with the numbers the verdict needs:
    devices carrying it, devices heard from this month, and how many
    configurations assign it.

    Keys are short on purpose. Spelled out, the same payload is nearly
    twice the size, and every byte here is downloaded by every person who
    opens the page.
    """
    live = {}
    if conn.execute("SELECT name FROM sqlite_master "
                    "WHERE name='file_live'").fetchone():
        for r in conn.execute(
                """SELECT file_id, sampled, answered, reporting, signals
                     FROM file_live"""):
            try:
                names = list(json.loads(r["signals"] or "{}").keys())
            except ValueError:
                names = []
            live[r["file_id"]] = {"n": r["sampled"], "k": r["answered"],
                                  "r": r["reporting"], "s": names[:24]}

    fitted, active, configs = {}, {}, {}
    for r in conn.execute(
            f"""SELECT file_id, COUNT(DISTINCT imei) n,
                       COUNT(DISTINCT CASE WHEN {_active_expr()}
                                           THEN imei END) a,
                       COUNT(DISTINCT NULLIF(config_name, '')) c
                  FROM device_can WHERE file_id IS NOT NULL
                 GROUP BY file_id"""):
        fitted[r["file_id"]] = r["n"]
        active[r["file_id"]] = r["a"]
        configs[r["file_id"]] = r["c"]

    sensors: dict[int, dict[str, list]] = {}
    for row in conn.execute(
            """SELECT file_id, sensor_name, status FROM can_sensors
                ORDER BY sensor_name"""):
        slot = sensors.setdefault(row["file_id"], {"ok": [], "no": []})
        if row["status"] == "declared":
            slot["ok"].append(row["sensor_name"])
        else:
            slot["no"].append(f"{row['sensor_name']} [{row['status']}]")

    out = []
    for row in conn.execute(
            """SELECT file_id, vmid, name, variant, make, model, revision,
                      year_from, year_to, can_bus, bitrate_kbps, obd_pins,
                      manual_url, raw_model
                 FROM can_files ORDER BY name, can_bus, year_from"""):
        s = sensors.get(row["file_id"]) or {"ok": [], "no": []}
        entry = {
            "i": row["file_id"],
            "n": row["name"] or row["raw_model"],
            "f": fitted.get(row["file_id"], 0),
        }
        if active.get(row["file_id"]):
            entry["a"] = active[row["file_id"]]
        if configs.get(row["file_id"]):
            entry["c"] = configs[row["file_id"]]
        if _is_generic(entry["n"]):
            entry["g"] = 1
        if row["file_id"] in live:
            entry["lv"] = live[row["file_id"]]
        # Only carry what is actually set. Thousands of nulls cost more
        # than the branches needed to skip them.
        for key, value in (("v", row["vmid"]), ("mk", row["make"]),
                           ("md", row["model"]), ("va", row["variant"]),
                           ("y0", row["year_from"]), ("y1", row["year_to"]),
                           ("b", row["can_bus"]), ("r", row["revision"]),
                           ("kb", _round_kb(row["bitrate_kbps"])),
                           ("p", row["obd_pins"]), ("u", row["manual_url"])):
            if value not in (None, ""):
                entry[key] = value
        if s["ok"]:
            entry["s"] = s["ok"]
        if s["no"]:
            entry["x"] = s["no"]
        out.append(entry)
    return out


def export_configs(conn) -> list[dict]:
    """Configuration names running on the generic protocol files.

    This is the second path of the sales flow. When no dedicated file
    exists for a vehicle, the question becomes: has anyone in this fleet
    already run that vehicle on the generic protocol? The evidence is in
    the configuration names -- "Al-Khaldi Mercedes Actros ... " assigned
    to J1939 FMS -- so the page needs those names to search through.

    Only names, counts and the file they point at. No device identifiers.
    """
    out = []
    for r in conn.execute(
            f"""SELECT d.config_name nm, f.name fl,
                       COUNT(DISTINCT d.imei) n,
                       COUNT(DISTINCT CASE WHEN {_active_expr()}
                                           THEN d.imei END) a
                  FROM device_can d JOIN can_files f ON f.file_id = d.file_id
                 WHERE d.config_name IS NOT NULL AND d.config_name != ''
                 GROUP BY d.config_name, f.name
                 ORDER BY n DESC"""):
        # Only names running on the generic protocols are shipped. Names
        # on dedicated files answer no question the page asks, and every
        # configuration name published is a little more of the fleet on a
        # URL -- the export stops at exactly what the second path needs.
        if not _is_generic(r["fl"]):
            continue
        entry = {"nm": r["nm"], "fl": r["fl"], "n": r["n"], "g": 1}
        if r["a"]:
            entry["a"] = r["a"]
        out.append(entry)
    return out


def export_fleet(conn) -> dict:
    """The estate at a glance, plus the three hint worklists."""
    estate = [
        {"state": r["state"], "devices": r["devices"]}
        for r in conn.execute(
            """SELECT CASE WHEN file_id IS NOT NULL THEN 'file assigned'
                           ELSE element_name END AS state,
                      COUNT(DISTINCT imei) AS devices
                 FROM device_can GROUP BY state ORDER BY devices DESC""")]

    top = [{"name": r["name"], "devices": r["n"]} for r in conn.execute(
        """SELECT f.name, COUNT(DISTINCT d.imei) n
             FROM device_can d JOIN can_files f ON f.file_id = d.file_id
            WHERE d.file_id IS NOT NULL AND f.name IS NOT NULL AND f.name != ''
            GROUP BY f.name ORDER BY n DESC LIMIT 40""")]

    hints: dict[str, list] = {}
    has_hints = conn.execute(
        "SELECT name FROM sqlite_master WHERE name='config_hints'").fetchone()
    if has_hints:
        for kind in ("candidate", "upgrade", "mismatch"):
            hints[kind] = [
                {"make": r["hinted_make"], "devices": r["n"],
                 "sample": r["sample"], "now_on": r["now_on"]}
                for r in conn.execute(
                    """SELECT hinted_make, COUNT(*) n, MIN(evidence) sample,
                              MIN(current_name) now_on
                         FROM config_hints WHERE kind = ?
                        GROUP BY hinted_make ORDER BY n DESC LIMIT 25""",
                    (kind,))]

    buses = conn.execute(
        """SELECT COUNT(*) entries,
                  SUM(inherited = 1) inherited,
                  SUM(port_function LIKE '%Sleep%') asleep
             FROM device_can WHERE file_id IS NOT NULL""").fetchone()
    dual = conn.execute(
        """SELECT COUNT(*) n FROM (SELECT imei FROM device_can
             WHERE file_id IS NOT NULL GROUP BY imei HAVING COUNT(*) > 1)"""
    ).fetchone()["n"]

    return {
        "estate": estate, "top": top, "hints": hints,
        "buses": {"entries": buses["entries"] or 0,
                  "inherited": buses["inherited"] or 0,
                  "asleep": buses["asleep"] or 0,
                  "dual_bus_devices": dual},
    }


def export_meta(conn, vehicles: list[dict]) -> dict:
    row = conn.execute(
        """SELECT COUNT(DISTINCT imei) devices,
                  COUNT(DISTINCT CASE WHEN file_id IS NOT NULL THEN imei END) fitted
             FROM device_can""").fetchone()
    last = conn.execute(
        "SELECT ran_at FROM sync_runs ORDER BY id DESC LIMIT 1").fetchone()
    recent = [{"name": r["name"], "since": r["first_seen"]} for r in conn.execute(
        """SELECT name, MIN(first_seen) first_seen FROM file_history
            WHERE gone_at IS NULL AND name IS NOT NULL AND name != ''
              AND first_seen >= datetime('now', '-30 days')
            GROUP BY name ORDER BY first_seen DESC LIMIT 25""")]
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "catalogue_sync": (last or {})["ran_at"] if last else None,
        "files": len(vehicles),
        "fitted_files": sum(1 for v in vehicles if v["f"]),
        "devices": row["devices"] or 0,
        "fitted_devices": row["fitted"] or 0,
        "new_files": recent,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="canval.export_site",
                                     description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="docs/data", help="output directory")
    parser.add_argument("--db", default=os.environ.get("CANVAL_DB", "canval.db"))
    args = parser.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    with connect(args.db) as conn:
        vehicles = export_vehicles(conn)
        configs = export_configs(conn)
        fleet = export_fleet(conn)
        meta = export_meta(conn, vehicles)

    written = []
    for name, payload in (("vehicles.json", vehicles),
                          ("configs.json", configs),
                          ("fleet.json", fleet),
                          ("meta.json", meta)):
        path = out / name
        # separators matter at this size: the default ", " and ": " add
        # about 8% to a megabyte for nothing a browser cares about.
        path.write_text(json.dumps(payload, ensure_ascii=False,
                                   separators=(",", ":")), encoding="utf-8")
        written.append((name, path.stat().st_size))

    print(f"\n  wrote to {out}/")
    total = 0
    for name, size in written:
        total += size
        print(f"    {name:<16} {size/1024:>8.0f} KB")
    print(f"    {'total':<16} {total/1024:>8.0f} KB  "
          f"(~{total/1024/4:.0f} KB over the wire, compressed)")
    print(f"\n  {meta['files']} catalogue files, {meta['fitted_devices']} of "
          f"{meta['devices']} devices carry one\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
