"""Inspect one device on the tracking platform.

Runs on its own -- it needs only AFAQY_TOKEN, not the device-management
credentials. That makes it the half of the tool that works today while the
XDM side is waiting on Xirgo.

    $env:AFAQY_TOKEN='...'
    python -m canval.inspect 869595063350010

Add --json to get the machine-readable form instead of the table.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from .afaqy import AfaqyAdapter, setup_batch, to_definitions
from .monitor import IDLE, LIVE, STALLED, read_snapshot

_BAR = "-" * 78


def _ago(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _when(unix: int | None) -> str:
    if not unix:
        return "-"
    return datetime.fromtimestamp(unix, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def find_unit(adapter: AfaqyAdapter, imei: str, page_size: int, verbose: bool):
    """Walk the fleet listing until the IMEI turns up.

    Only the `basic` block is requested, so this stays light even on a
    large estate, and it stops at the first match rather than reading the
    whole fleet.
    """
    offset = 0
    scanned = 0
    while True:
        raw = adapter._post(
            "/v1/units",
            {"offset": offset, "limit": page_size, "simplify": 1,
             "projection": ["basic"]},
        )
        items = (raw or {}).get("data")
        if isinstance(items, dict):
            items = items.get("items") or items.get("units") or []
        items = items or []

        for unit in items:
            if isinstance(unit, dict) and str(unit.get("i") or "") == str(imei):
                return unit.get("id"), unit.get("n"), scanned + len(items)
        scanned += len(items)
        if verbose:
            print(f"  scanned {scanned} units ...", file=sys.stderr)

        if len(items) < page_size:
            return None, None, scanned
        offset += page_size


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="canval.inspect",
        description="Inspect one device on the tracking platform.",
    )
    p.add_argument("imei", help="device IMEI, e.g. 869595063350010")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--page-size", type=int, default=500)
    p.add_argument("--all", action="store_true",
                   help="show every parameter, not just the named sensors")
    args = p.parse_args(argv)

    token = os.environ.get("AFAQY_TOKEN")
    if not token:
        print("Set AFAQY_TOKEN first:\n"
              "    $env:AFAQY_TOKEN='...'", file=sys.stderr)
        return 2

    adapter = AfaqyAdapter(token=token)

    if not args.json:
        print(f"\nLooking for {args.imei} ...")
    try:
        unit_id, name, scanned = find_unit(
            adapter, args.imei, args.page_size, verbose=not args.json
        )
    except Exception as exc:                                  # noqa: BLE001
        print(f"Fleet lookup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("A 401 usually means the token expired -- grab a fresh one.",
              file=sys.stderr)
        return 1

    if not unit_id:
        print(f"IMEI {args.imei} not found (searched {scanned} units).",
              file=sys.stderr)
        return 1

    try:
        view = adapter.view(unit_id)
    except Exception as exc:                                  # noqa: BLE001
        print(f"Could not read the unit: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1

    frozen = setup_batch(view)
    readings = read_snapshot(view.parameters, view.last_message or 0,
                             to_definitions(view), state=view.state)
    specs = view.spec_by_param

    if args.json:
        print(json.dumps({
            "imei": view.imei,
            "unit_id": view.unit_id,
            "name": view.name,
            "device_model": view.device_model,
            "created_at": view.created_at,
            "last_message": view.last_message,
            "vehicle_state": view.state.state,
            "never_delivered": sorted(frozen),
            "readings": [
                {
                    "param": r.key,
                    "sensor": r.display_name,
                    "raw": r.raw,
                    "rendered": specs[r.key].render(r.raw) if r.key in specs else None,
                    "verdict": "never_delivered" if r.key in frozen else r.verdict,
                    "reason": r.reason,
                    "driver": r.driver.value,
                    "class": r.param_class.value,
                    "age_seconds": r.age_seconds,
                }
                for r in readings
            ],
        }, indent=2, ensure_ascii=False))
        return 0

    # ------------------------------------------------------------ report
    print(f"\n{_BAR}")
    print(f"  {view.name}   ({view.device_model})")
    print(f"  IMEI {view.imei}    unit {view.unit_id}")
    print(f"  installed {_when(view.created_at)}    last report {_when(view.last_message)}")
    print(f"  vehicle is {view.state.describe()}"
          + (f", for {_ago(max(0,(view.last_message or 0) - view.state.since))}"
             if view.state.since else ""))
    print(_BAR)

    named = [r for r in readings if r.display_name]
    print(f"\nCONFIGURED SENSORS ({len(named)})\n")
    for r in named:
        spec = specs.get(r.key)
        shown = spec.render(r.raw) if spec else str(r.raw)
        verdict = "NEVER DELIVERED" if r.key in frozen else r.verdict
        mark = "  <- calibrated" if spec and spec.calibrated else ""
        print(f"  {verdict:<16} {r.display_name:<22} {shown:<16} "
              f"{_ago(r.age_seconds):>5} ago{mark}")
        if verdict != LIVE:
            print(f"  {'':<16} {'':<22} -> {r.reason}")

    if any(specs.get(r.key) and specs[r.key].calibrated for r in named):
        print("\n  calibrated = the engineering value comes from a curve set on the")
        print("  platform, not from the CAN file. A wrong reading there is a")
        print("  calibration problem, not a CAN file problem.")

    if frozen:
        print(f"\nNEVER DELIVERED ({len(frozen)})\n")
        print("  Written once when the unit was set up and never touched since,")
        print("  so the CAN file has never carried them.\n")
        print("  ", ", ".join(sorted(frozen)))

    # A signal that is not in the setup batch did carry real data once. If it
    # then froze for months while the device kept reporting, that is a fault
    # that developed later -- a different thing from a signal the CAN file
    # never delivered, and the only one of the two worth a site visit.
    DIED_AFTER = 30 * 86400
    stale = [
        r for r in readings
        if r.key not in frozen
        and (r.verdict == STALLED or r.age_seconds > DIED_AFTER)
        and r.driver.value != "static"
    ]
    if stale:
        print(f"\nWORKED, THEN STOPPED ({len(stale)})\n")
        print("  These did carry data at some point and then froze. That is a")
        print("  fault to look at, not a gap in the CAN file.\n")
        for r in stale:
            print(f"    {(r.display_name or r.key):<22} last changed "
                  f"{_ago(r.age_seconds)} ago")

    if args.all:
        other = [r for r in readings if not r.display_name and r.key not in frozen]
        print(f"\nUNNAMED PARAMETERS ({len(other)})\n")
        for r in other:
            print(f"  {r.verdict:<13} {r.key:<20} {str(r.raw):<18} "
                  f"{_ago(r.age_seconds):>5} ago")

    live = sum(1 for r in readings if r.verdict == LIVE)
    print(f"\n{_BAR}")
    stale_keys = {r.key for r in stale}
    idle = sum(1 for r in readings
               if r.verdict == IDLE and r.key not in frozen and r.key not in stale_keys)
    print(f"  {len(readings)} parameters:  {live} live   {idle} idle   "
          f"{len(frozen)} never delivered   {len(stale)} stalled")
    print(f"{_BAR}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
