"""Command line entry point.

    canval hardware                     list hardware versions and their ids
    canval catalogue                    refresh the CAN file catalogue
    canval index --hardware 12          sweep devices, build the reverse index
    canval check "shacman 7300" --year 2021
    canval devices --vmid 2888

`check` is the answer to the customer's question, in two gates:
  gate 1  is there a CAN file for this vehicle at all
  gate 2  has it ever been installed, and did it actually report
"""

from __future__ import annotations

import argparse
import json
import sys

from .config import Settings
from .index import build_device_index, refresh_catalogue
from .monitor import LIVE, NotConfigured, aggregate, evaluate_device
from .coverage import (ABSENT, NEVER, PROVEN as COV_PROVEN, UNCHECKABLE,
                       UNKNOWN, compare)
from .fallback import CAR, HEAVY, TRIAL_CHECKLIST, advise, normalise
from .store import (SOURCE_API, SOURCE_CONSOLE, connect, devices_for_vmid,
                    revisions_for_vmid, search_can_files, verify)
from .xdm import XdmClient, XdmError
from .xdm_session import SessionExpired, XdmSession


def _db(settings) -> str:
    """The database path works with or without documented credentials."""
    import os
    return settings.db_path if settings else os.environ.get("CANVAL_DB", "canval.db")


def _no_match(conn, args) -> None:
    """A miss is not one answer. For a truck it is 'try the standard first'."""
    label = args.query + (f" (year {args.year})" if args.year else "")
    print(f'GATE 1  NO DEDICATED FILE for "{label}"\n')

    fb = advise(conn, args.query)

    if fb.verdict == HEAVY:
        print("  Assessed as a heavy vehicle.\n")
        print("  RECOMMENDATION  try the generic protocol file before")
        print("                  scheduling a dedicated trial.\n")
    elif fb.verdict == CAR:
        print("  Assessed as a passenger vehicle.\n")
        print("  RECOMMENDATION  field trial required.\n")
    else:
        print("  Vehicle class could not be determined from the query.\n")

    if fb.generic_files and fb.verdict != CAR:
        print("  GENERIC FILES AVAILABLE\n")
        for g in fb.generic_files[:6]:
            vmid = f"VMID {g['vmid']}" if g["vmid"] else "no VMID"
            rate = f"{g['bitrate_kbps']}kbps" if g["bitrate_kbps"] else "default rate"
            print(f"    {g['name']:<28} {vmid:<12} {rate}")
            if g["manual_url"]:
                print(f"        guide: {g['manual_url']}")
        print()

    for note in fb.notes:
        print(f"  - {note}")

    print("\n  RECORD FROM THE TRIAL, so the next request is answered from")
    print("  the desk instead of the road:\n")
    for item in TRIAL_CHECKLIST:
        print(f"    - {item}")
    print()


def _years(row) -> str:
    if row["year_from"] is None:
        return "-"
    return f"{row['year_from']}-{row['year_to'] or 'now'}"


def _client(args, settings):
    """Console session if asked for, documented API otherwise.

    Both expose the same iter_can_files / hardware_versions, so everything
    downstream is unchanged when the official credentials arrive.
    """
    if getattr(args, "console", False):
        return XdmSession()
    return XdmClient(settings)


def cmd_hardware(args, settings):
    client = _client(args, settings)
    for hw in client.hardware_versions():
        name = hw.get("name", "")
        mark = "  <-" if "LX45" in str(name) else ""
        print(f"  id={hw.get('id'):<6} {name}{mark}")


def cmd_catalogue(args, settings):
    client = _client(args, settings)
    print("Fetching CAN file catalogue ...")
    source = SOURCE_CONSOLE if getattr(args, "console", False) else SOURCE_API
    stats = refresh_catalogue(
        client, _db(settings), source=source,
        progress=lambda n: print(f"  {n} files", flush=True),
    )
    print(json.dumps(stats, indent=2))
    if not stats["integrity_ok"]:
        print("\nINTEGRITY CHECK FAILED -- do not trust searches until fixed:")
        for prob in stats["integrity_problems"]:
            print(f"  - {prob}")
    if stats["models_not_parsed"]:
        print(
            f"\n{stats['models_not_parsed']} model strings did not parse. "
            "Inspect them before trusting the search:\n"
            "  sqlite3 %s \"SELECT file_id, raw_model, parse_issues FROM can_files "
            "WHERE parse_issues IS NOT NULL LIMIT 20\"" % _db(settings)
        )


def cmd_index(args, settings):
    client = _client(args, settings)
    hw = [int(x) for x in args.hardware] if args.hardware else None
    print(
        f"Sweeping devices (hardware={hw or 'all'}, "
        f"active in last {args.active_days} days) ..."
    )
    stats = build_device_index(
        client,
        _db(settings),
        hardware_ids=hw,
        active_since_days=args.active_days,
        concurrency=settings.concurrency if settings else 6,
        sample_configs=args.sample_configs,
        verify_sample=args.verify,
        limit=args.limit,
        refresh_after_days=args.refresh_after,
        force=args.force,
        progress=lambda d, t: print(f"  {d}/{t} devices read", flush=True),
        notice=lambda m: print(m, flush=True),
    )
    if stats.get("verify_mismatches"):
        print("\n  VERIFICATION FAILED -- the template cache disagreed with "
              "the devices:")
        for p_ in stats["verify_problems"]:
            print(f"    - {p_}")
        print("  Treat this index as unreliable until that is explained.\n")
    print(json.dumps(stats, indent=2))


def _monitor_adapter():
    """Real adapter when AFAQY_TOKEN is set, placeholder otherwise.

    Gate 2 is optional by design: gate 1 answers instantly from the local
    catalogue and needs no credentials at all, so a missing token degrades
    the answer rather than blocking it.
    """
    import os

    token = os.environ.get("AFAQY_TOKEN")
    if not token:
        return NotConfigured(), False
    try:
        from .afaqy import AfaqyAdapter

        return AfaqyAdapter(token=token), True
    except Exception:                                  # noqa: BLE001
        return NotConfigured(), False


def cmd_check(args, settings):
    adapter, live = _monitor_adapter()

    with connect(_db(settings)) as conn:
        rows = search_can_files(conn, normalise(args.query), args.year)

        if not rows:
            _no_match(conn, args)
            return

        fitted = [r for r in rows if (r["installs"] or 0) > 0]
        header = f'\nGATE 1  {len(rows)} catalogue match(es) for "{args.query}"'
        if args.year:
            header += f" (year {args.year})"
        print(header)
        if fitted:
            print(f"        {len(fitted)} of them are already fitted somewhere "
                  "-- shown first\n")
        elif len(rows) > args.limit:
            # Hundreds of rows with nothing fitted means the query was too
            # broad to be useful. Say so instead of printing page one of ten.
            print(f"        none are fitted anywhere. Narrow the query -- add "
                  f"the model and year\n")
        else:
            print()

        if not live:
            print("  (gate 2 needs AFAQY_TOKEN; showing install history only)\n")

        for r in rows[: args.limit]:
            revs = revisions_for_vmid(conn, r["vmid"]) if r["vmid"] else []
            newest = max((x["revision"] or 0) for x in revs) if revs else None
            rev_note = f"   rev {r['revision']} of {len(revs)}" if len(revs) > 1 else ""

            fitted_note = (f"   [{r['installs']} fitted]"
                           if (r["installs"] or 0) else "")
            print(f"  VMID {r['vmid']:<6} {r['name']}   years {_years(r)}"
                  f"   bus {r['can_bus'] or '-'}"
                  f"   {r['bitrate_kbps'] or '-'}kbps"
                  f"   obd {r['obd_pins'] or '-'}{rev_note}{fitted_note}")

            sensors = conn.execute(
                """SELECT sensor_name, status FROM can_sensors
                   WHERE file_id=? AND source=? ORDER BY status, sensor_name""",
                (r["file_id"], r["source"]),
            ).fetchall()
            usable = [x["sensor_name"] for x in sensors if x["status"] == "declared"]
            other = [x for x in sensors if x["status"] != "declared"]
            print(f"    declared usable ({len(usable)}): "
                  + ", ".join(usable[:10]) + (" ..." if len(usable) > 10 else ""))
            if other:
                print(f"    not usable ({len(other)}): "
                      + ", ".join(f"{x['sensor_name']} [{x['status']}]"
                                  for x in other[:5]))

            # ------------------------------------------------------ gate 2
            installs = devices_for_vmid(conn, r["vmid"]) if r["vmid"] else []
            if not installs:
                print("    GATE 2  never installed -> field trial required\n")
                continue

            # A device sitting on a superseded revision is worth flagging:
            # updating it is cheaper than sending anyone out.
            stale = [d for d in installs
                     if newest is not None and (d["revision"] or 0) < newest]

            print(f"    GATE 2  installed on {len(installs)} device(s)")
            if stale:
                print(f"            {len(stale)} of them run an older revision -- "
                      "try updating before dispatching")

            evidences = []
            if live:
                sampled = installs[: args.devices]
                for d in sampled:
                    ev = evaluate_device(adapter, d["imei"])
                    evidences.append(ev)
                readable = [e for e in evidences if not e.error]
                blocked = len(evidences) - len(readable)

                for e in readable:
                    n = len([x for x in e.readings if x.verdict == LIVE])
                    print(f"            {e.imei}  {n} live signals")
                if blocked:
                    # These are almost always other customers' devices: the
                    # monitoring token only sees one account. Saying "not
                    # found" without that context reads like a fault.
                    print(f"            {blocked} of {len(sampled)} not visible "
                          "to this monitoring account (other customers)")
            else:
                for d in installs[:5]:
                    print(f"            {d['imei']}  hw={d['hardware'] or '-'}")

            # The answer the customer actually needs: of everything the
            # catalogue promises, what do real installs deliver? Listing
            # only what works leaves the reader assuming the rest does too.
            cmp_ = compare(usable, evidences)
            if cmp_.devices_checked:
                print(f"\n    CHECKED AGAINST {cmp_.devices_checked} READABLE "
                      f"INSTALL(S) OF {len(installs)}:\n")

                works = cmp_.by_status(COV_PROVEN)
                if works:
                    print("      WORKS (seen live):")
                    for v in works:
                        print(f"        {v.declared_name}"
                              f"   [{v.devices_proven}/{v.devices_checked}]")

                dead = cmp_.by_status(ABSENT, NEVER)
                if dead:
                    print("\n      DOES NOT WORK on this file:")
                    for v in dead:
                        print(f"        {v.declared_name}   -- {v.note}")

                unproven = cmp_.by_status(UNKNOWN)
                if unproven:
                    print("\n      NO EVIDENCE EITHER WAY:")
                    print("        " + ", ".join(v.declared_name for v in unproven))

                other = cmp_.by_status(UNCHECKABLE)
                if other:
                    print(f"\n      NOT VERIFIABLE FROM HERE ({len(other)}): "
                          "the platform has no equivalent signal")
                    print("        " + ", ".join(
                        v.declared_name for v in other[:8])
                        + (" ..." if len(other) > 8 else ""))

                print(f"\n      -> {cmp_.summary}")
                if cmp_.devices_checked < 2:
                    print("      (one install only -- indicative, not proven)")
            print()

        hidden = len(rows) - min(len(rows), args.limit)
        if hidden:
            print(f"  ... {hidden} further catalogue match(es) not shown. "
                  f"Use --limit to see more.\n")


def cmd_sync(args, settings):
    """One command for the nightly job.

    Refresh, resweep, then say what changed. The last part is the reason
    it exists: a vehicle that became supported this week is something
    sales can act on, and nobody was going to diff a 3934-row catalogue
    by hand.
    """
    from .store import last_sync, whats_new

    client = _client(args, settings)
    source = SOURCE_CONSOLE if getattr(args, "console", False) else SOURCE_API
    db = _db(settings)

    print("Refreshing the catalogue ...")
    stats = refresh_catalogue(client, db, source=source)
    print(f"  {stats['can_files']} files"
          f"   new {stats['new_since_last_sync']}"
          f"   withdrawn {stats['withdrawn']}")
    if not stats["integrity_ok"]:
        print("\n  INTEGRITY CHECK FAILED -- stopping before the sweep:")
        for p_ in stats["integrity_problems"]:
            print(f"    - {p_}")
        return

    if not args.skip_devices:
        hw = [int(x) for x in args.hardware] if args.hardware else None
        print("\nSweeping devices ...")
        sw = build_device_index(
            client, db, hardware_ids=hw,
            active_since_days=args.active_days,
            concurrency=settings.concurrency if settings else 6)
        print(f"  {sw['devices']} devices"
              f"   {sw['with_can_file']} with a CAN file"
              f"   {sw['unread']} unread"
              f"   {sw['errors']} errors")

    with connect(db) as conn:
        fresh = whats_new(conn, days=14)
        run = last_sync(conn)

    if fresh:
        print(f"\nNEW SINCE THE LAST SYNC ({len(fresh)}):\n")
        for n in fresh:
            years = (f"{n['year_from']}-{n['year_to'] or 'now'}"
                     if n["year_from"] else "any year")
            print(f"  {n['name']:<28} {years:<14} {n['buses']} bus(es)")
    else:
        print("\nNothing new in the catalogue since the last sync.")

    if run:
        print(f"\n  last sync {run['ran_at']}")


def cmd_verify(args, settings):
    with connect(_db(settings)) as conn:
        result = verify(conn)
        print(f"\n  rows by source : {result['counts']}")
        print(f"  distinct VMIDs : {result['vehicles']}")
        print(f"  rows w/o VMID  : {result['no_vmid']}")
        if result["ok"] and not result["problems"]:
            print("\n  OK -- the catalogue is internally consistent.\n")
        else:
            print("\n  PROBLEMS:")
            for p_ in result["problems"]:
                print(f"    - {p_}")
            print()


def cmd_devices(args, settings):
    with connect(_db(settings)) as conn:
        rows = devices_for_vmid(conn, args.vmid)
        if not rows:
            print(f"No indexed device carries VMID {args.vmid}.")
            return
        print(f"{len(rows)} install(s) of VMID {args.vmid}:\n")
        for r in rows:
            print(f"  {r['imei']}  {r['element_name']:<22} file={r['file_id']:<7}"
                  f" rev={r['revision']}  hw={r['hardware'] or '-'}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="canval", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    ph = sub.add_parser("hardware", help="list hardware versions and ids")
    pcat = sub.add_parser("catalogue", help="refresh the CAN file catalogue")

    pi = sub.add_parser("index", help="sweep devices and build the reverse index")
    pi.add_argument("--hardware", nargs="*", help="hardware id(s) to limit the sweep")
    pi.add_argument("--active-days", type=int, default=60,
                    help="skip devices with no activity in this many days")
    pi.add_argument("--limit", type=int, default=None, metavar="N",
                    help="stop after N devices -- for a trial run on one "
                         "hardware family before committing to the estate")
    pi.add_argument("--verify", type=int, default=2, metavar="N",
                    help="re-read N devices per template the slow way and "
                         "compare, to prove the template cache (0 disables)")
    pi.add_argument("--refresh-after", type=int, default=7, metavar="DAYS",
                    help="re-read a device whose last read is older than "
                         "this, even if its configuration has not changed")
    pi.add_argument("--force", action="store_true",
                    help="re-read every device, ignoring when it was last "
                         "read and whether anything changed")
    pi.add_argument("--sample-configs", type=int, default=0, metavar="N",
                    help="approximate mode: read N devices per config and "
                         "extrapolate a consistent positive result. Off by "
                         "default; a sample that finds nothing is never "
                         "extrapolated.")

    pc = sub.add_parser("check", help="answer: is this vehicle supported")
    pc.add_argument("query", help='e.g. "shacman 7300"')
    pc.add_argument("--year", type=int)
    pc.add_argument("--limit", type=int, default=5, help="max candidate files to show")
    pc.add_argument("--devices", type=int, default=10, help="max devices to sample")

    sub.add_parser("verify", help="check the database is internally consistent")

    psy = sub.add_parser(
        "sync", help="daily job: refresh the catalogue, resweep, report changes")
    psy.add_argument("--hardware", nargs="*", help="hardware id(s) for the sweep")
    psy.add_argument("--active-days", type=int, default=60)
    psy.add_argument("--skip-devices", action="store_true",
                     help="catalogue only, leave the device index alone")
    psy.add_argument("--console", action="store_true")

    pd = sub.add_parser("devices", help="list devices carrying a VMID")
    pd.add_argument("--vmid", type=int, required=True)

    for sp in (ph, pcat, pi):
        sp.add_argument("--console", action="store_true",
                        help="use the browser console session (XDM_BEARER) "
                             "instead of the documented API credentials")

    args = p.parse_args(argv)

    # Only the commands that actually call out need credentials. `check` and
    # `devices` read the local database, and asking them for a secret would
    # be a wall in front of the fastest, most-used path.
    NEEDS_NETWORK = {"hardware", "catalogue", "index", "sync"}

    settings = None
    if args.cmd in NEEDS_NETWORK and not getattr(args, "console", False):
        try:
            settings = Settings.from_env()
        except RuntimeError as exc:
            print(f"config error: {exc}\n"
                  f"Or run with --console and XDM_BEARER set.", file=sys.stderr)
            return 2

    handlers = {
        "hardware": cmd_hardware, "catalogue": cmd_catalogue,
        "index": cmd_index, "check": cmd_check, "devices": cmd_devices,
        "verify": cmd_verify, "sync": cmd_sync,
    }
    try:
        handlers[args.cmd](args, settings)
    except SessionExpired as exc:
        print(f"{exc}", file=sys.stderr)
        return 1
    except XdmError as exc:
        print(f"XDM error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
