"""Why did most swept devices resolve to no CAN file?

A sweep of 6483 devices produced a file for 247. That is either the truth
(CAN is not fitted on most of the estate) or a blind spot in how the tool
reads settings. Everything downstream depends on which, because under a
blind spot every "never installed" verdict is wrong and every one of them
sends someone out to look at a working install.

This asks the data rather than guessing:

  * how the two groups split by configuration -- if the same config yields
    a file for some devices and not others, the file is a per-device
    override and absence is real; if the groups split cleanly by config,
    the file probably lives in the config and the tool is not seeing it

  * what a device with no file actually returns, in full

  * whether the settings tree exposes an effective value where the
    overrides list is silent

Config *names* are deliberately not used as evidence. They are typed by
hand and look like "Ter3(Temp&Hum)&Buzzer (lx45)" and "actross +19" --
useful as a hint to a human, far too loose to index on.

    python -m canval.whygap
"""

from __future__ import annotations

import argparse
import collections
import json

from .config import Settings
from .store import connect, sweep_coverage
from .xdm import XdmClient

# Makes worth noticing in a config name, purely to flag "worth a look".
_HINTS = ("volvo", "mercedes", "actros", "scania", "man ", "daf", "iveco",
          "shacman", "howo", "hino", "isuzu", "toyota", "hiace", "ford",
          "renault", "kamaz", "foton", "jac", "sinotruk")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="canval.whygap")
    p.add_argument("--sample", type=int, default=5,
                   help="devices with no file to inspect in full")
    args = p.parse_args(argv)

    settings = Settings.from_env()
    client = XdmClient(settings)

    with connect(settings.db_path) as conn:
        cov = sweep_coverage(conn)
        rows = conn.execute(
            "SELECT imei, file_id, config_name, hardware FROM device_can"
        ).fetchall()

    print(f"\nswept devices : {cov['swept']}")
    print(f"  resolved    : {cov['resolved']}  ({cov['pct']}%)")
    print(f"  unresolved  : {cov['unresolved']}")

    if cov["swept"] == 0:
        print("\nNothing swept yet. Run:  python -m canval.cli index --hardware 98 99 108 116")
        return 1
    if cov["unresolved"] == 0:
        print("\nEvery swept device resolved. No gap to explain.")
        return 0

    have = [r for r in rows if r["file_id"] is not None]
    lack = [r for r in rows if r["file_id"] is None]

    def top(group, n=8):
        c = collections.Counter((r["config_name"] or "(none)") for r in group)
        return c.most_common(n)

    print("\nconfigs among devices WITH a file:")
    for name, n in top(have):
        print(f"  {n:>6}  {name}")
    print("\nconfigs among devices WITHOUT one:")
    for name, n in top(lack):
        hint = "  <- names a vehicle" if any(
            h in (name or "").lower() for h in _HINTS) else ""
        print(f"  {n:>6}  {name}{hint}")

    with_cfg = {(r["config_name"] or "") for r in have}
    without_cfg = {(r["config_name"] or "") for r in lack}
    shared = with_cfg & without_cfg

    print(f"\nconfigs in BOTH groups: {len(shared)} "
          f"(of {len(with_cfg)} with, {len(without_cfg)} without)")
    if shared:
        print("  -> the same config gives a file for some devices and not")
        print("     others, so the file is a per-device override and the")
        print("     blanks are real. The index is complete.")
    else:
        print("  -> the split follows config boundaries exactly, which is")
        print("     what you would see if the file were defined in the")
        print("     config rather than overridden per device. The index")
        print("     would then be missing most of the estate.")

    print(f"\ninspecting {args.sample} unresolved devices in full ...\n")
    for r in lack[: args.sample]:
        try:
            ov = client.device_overrides(r["imei"])
        except Exception as exc:                        # noqa: BLE001
            print(f"  {r['imei']}  error: {exc}")
            continue
        print(f"  {r['imei']}   config={r['config_name']!r}")
        print(f"    {len(ov)} override(s)")
        for o in ov[:12]:
            print(f"      {str(o.get('name'))[:40]:<42} = {o.get('value')!r}")
        if len(ov) > 12:
            print(f"      ... {len(ov)-12} more")
        print()

    if lack:
        uid = lack[0]["imei"]
        print(f"settings tree for {uid} "
              "(effective values, not just overrides):")
        try:
            cats = client._request(
                "GET", f"/api/external/v3/settingsOverrides/{uid}")
            body = json.dumps(cats)
            print(f"  {body[:700]}")
            if len(body) > 700:
                print(f"  ... {len(body)-700} more characters")
            print("\n  If this returns a category tree, the effective CAN")
            print("  file can be read from it even with no override set,")
            print("  which would close the gap.")
        except Exception as exc:                        # noqa: BLE001
            print(f"  not available: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
