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


def export_vehicles(conn) -> list[dict]:
    """One entry per catalogue file, with how many devices carry it.

    Keys are short on purpose. Spelled out, the same payload is nearly
    twice the size, and every byte here is downloaded by every person who
    opens the page.
    """
    fitted = {r["file_id"]: r["n"] for r in conn.execute(
        """SELECT file_id, COUNT(DISTINCT imei) n FROM device_can
            WHERE file_id IS NOT NULL GROUP BY file_id""")}

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
        fleet = export_fleet(conn)
        meta = export_meta(conn, vehicles)

    written = []
    for name, payload in (("vehicles.json", vehicles),
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
