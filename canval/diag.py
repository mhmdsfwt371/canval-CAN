"""What does the `Vehicle model` override actually point at?

The override on a real device came back as:

    {"elementId": 10189719, "name": "Vehicle model", "value": "4633"}

while the console screen showed `SHACMAN H3000S <VMID: 2888>` for the same
device, and 4633 matches neither a file_id nor a vmid in the catalogue.
So there is a third numbering, and the reverse index cannot be built until
we know which one it is.

Run this and send the output:

    python -m canval.diag 869595063350010
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

from .config import Settings
from .store import connect
from .xdm import XdmClient


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="canval.diag")
    p.add_argument("imei", nargs="?", default="869595063350010")
    p.add_argument("--db", default=None)
    args = p.parse_args(argv)

    db = args.db or Settings.from_env().db_path

    # ---------------------------------------------------------- catalogue
    with connect(db) as conn:
        conn.row_factory = sqlite3.Row
        total = conn.execute("SELECT COUNT(*) n FROM can_files").fetchone()["n"]
        vr = conn.execute(
            "SELECT MIN(vmid) lo, MAX(vmid) hi FROM can_files WHERE vmid IS NOT NULL"
        ).fetchone()
        fr = conn.execute(
            "SELECT MIN(file_id) lo, MAX(file_id) hi FROM can_files"
        ).fetchone()

        print(f"\ncatalogue rows : {total}")
        print(f"vmid range     : {vr['lo']} .. {vr['hi']}")
        print(f"file_id range  : {fr['lo']} .. {fr['hi']}")

        print("\nSHACMAN rows in the catalogue:")
        rows = conn.execute(
            "SELECT file_id, vmid, version, raw_model FROM can_files "
            "WHERE raw_model LIKE '%SHACMAN%' ORDER BY vmid"
        ).fetchall()
        for r in rows[:25]:
            print(f"  file_id={r['file_id']:<7} vmid={str(r['vmid']):<7} "
                  f"v={r['version']}  {r['raw_model'][:64]}")
        if len(rows) > 25:
            print(f"  ... and {len(rows) - 25} more")

    # ------------------------------------------------------------ device
    print(f"\nreading overrides for {args.imei} ...")
    client = XdmClient(Settings.from_env())
    overrides = client.device_overrides(args.imei)

    model_rows = [o for o in overrides
                  if "vehicle model" in str(o.get("name", "")).lower()]
    print(f"\n'Vehicle model' overrides found: {len(model_rows)}")
    for o in model_rows:
        print(f"  elementId={o.get('elementId')}  value={o.get('value')!r}")

    # Does the value line up with any column we already hold?
    with connect(db) as conn:
        conn.row_factory = sqlite3.Row
        for o in model_rows:
            raw = str(o.get("value") or "").strip()
            if not raw.isdigit():
                continue
            n = int(raw)
            print(f"\n  looking up {n}:")
            for col in ("file_id", "vmid"):
                hit = conn.execute(
                    f"SELECT file_id, vmid, raw_model FROM can_files WHERE {col}=?",
                    (n,),
                ).fetchone()
                print(f"    as {col:<8} -> "
                      + (hit["raw_model"][:60] if hit else "no match"))

            near = conn.execute(
                "SELECT file_id, vmid, raw_model FROM can_files "
                "WHERE vmid BETWEEN ? AND ? ORDER BY vmid LIMIT 6",
                (n - 3, n + 3),
            ).fetchall()
            print(f"    vmids near {n}:")
            for h in near:
                print(f"      vmid={h['vmid']:<6} {h['raw_model'][:55]}")

    # The console shows a label, so the same element may be readable in a
    # richer form elsewhere in the settings tree.
    print("\nother overrides mentioning CAN or model:")
    for o in overrides:
        name = str(o.get("name", ""))
        if any(w in name.lower() for w in ("can", "model", "vehicle")):
            print(f"  {name:<34} = {o.get('value')!r}")

    print("\nSensor slot mapping on this device:")
    for o in overrides:
        if str(o.get("name", "")).lower().startswith("sensor no"):
            print(f"  {o['name']:<18} -> sensor_{o.get('value')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
