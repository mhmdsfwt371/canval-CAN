"""Expose the incremental sweep on the command line.

Run from D:\\canval with the new effective.py beside it:

    python apply_patch2.py

Same rules as the first patch: back up first, anchor every edit on exact
text, write nothing if an anchor is missing.
"""

from __future__ import annotations

import datetime as _dt
import py_compile
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PKG = ROOT / "canval"

INDEX_OLD_SIG = """def build_device_index(client, db_path: str, hardware_ids=None,
                       active_since_days: int | None = 60,
                       concurrency: int = 6, sample_configs: int = 0,
                       verify_sample: int = 2, limit: int | None = None,
                       progress=None) -> dict:"""

INDEX_NEW_SIG = """def build_device_index(client, db_path: str, hardware_ids=None,
                       active_since_days: int | None = 60,
                       concurrency: int = 6, sample_configs: int = 0,
                       verify_sample: int = 2, limit: int | None = None,
                       refresh_after_days: int = 7, force: bool = False,
                       progress=None, notice=None) -> dict:"""

INDEX_OLD_CALL = """    return sweep_devices(
        client, db_path, hardware_ids=hardware_ids,
        active_since_days=active_since_days, concurrency=concurrency,
        verify_sample=verify_sample, limit=limit, progress=progress)"""

INDEX_NEW_CALL = """    return sweep_devices(
        client, db_path, hardware_ids=hardware_ids,
        active_since_days=active_since_days, concurrency=concurrency,
        verify_sample=verify_sample, limit=limit,
        refresh_after_days=refresh_after_days, force=force,
        progress=progress, notice=notice)"""

CLI_OLD_ARGS = """    pi.add_argument("--verify", type=int, default=2, metavar="N",
                    help="re-read N devices per template the slow way and "
                         "compare, to prove the template cache (0 disables)")"""

CLI_NEW_ARGS = """    pi.add_argument("--verify", type=int, default=2, metavar="N",
                    help="re-read N devices per template the slow way and "
                         "compare, to prove the template cache (0 disables)")
    pi.add_argument("--refresh-after", type=int, default=7, metavar="DAYS",
                    help="re-read a device whose last read is older than "
                         "this, even if its configuration has not changed")
    pi.add_argument("--force", action="store_true",
                    help="re-read every device, ignoring when it was last "
                         "read and whether anything changed")"""

CLI_OLD_CALL = """        verify_sample=args.verify,
        limit=args.limit,
        progress=lambda d, t: print(f"  {d}/{t} devices read", flush=True),
    )"""

CLI_NEW_CALL = """        verify_sample=args.verify,
        limit=args.limit,
        refresh_after_days=args.refresh_after,
        force=args.force,
        progress=lambda d, t: print(f"  {d}/{t} devices read", flush=True),
        notice=lambda m: print(m, flush=True),
    )"""

EDITS = [
    ("canval/index.py", [(INDEX_OLD_SIG, INDEX_NEW_SIG),
                         (INDEX_OLD_CALL, INDEX_NEW_CALL)]),
    ("canval/cli.py", [(CLI_OLD_ARGS, CLI_NEW_ARGS),
                       (CLI_OLD_CALL, CLI_NEW_CALL)]),
]


def main() -> int:
    if not PKG.is_dir():
        print(f"No canval package at {PKG}. Run this from D:\\canval.")
        return 2
    source = ROOT / "effective.py"
    if not source.is_file():
        print("effective.py must sit next to this script.")
        return 2
    if "refresh_after_days" not in source.read_text(encoding="utf-8"):
        print("That effective.py is the older one. Download the new file "
              "before running this.")
        return 2

    planned = []
    for rel, pairs in EDITS:
        path = ROOT / rel
        if not path.is_file():
            print(f"missing: {rel}")
            return 2
        text = new = path.read_text(encoding="utf-8")
        for old, repl in pairs:
            if repl in new:
                continue
            if old not in new:
                print(f"\n{rel}: anchor not found ->\n"
                      f"  {old.splitlines()[0][:70]}\n"
                      "Nothing written. Apply the first patch before this one.")
                return 1
            new = new.replace(old, repl, 1)
        if new != text:
            planned.append((path, new))

    if not planned:
        shutil.copy2(source, PKG / "effective.py")
        print("Command line already wired; effective.py refreshed.")
        return 0

    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = ROOT / f"canval_backup_{stamp}"
    backup.mkdir(exist_ok=True)
    for rel, _ in EDITS:
        shutil.copy2(ROOT / rel, backup / Path(rel).name)

    for path, new in planned:
        path.write_text(new, encoding="utf-8")
        print(f"  patched {path.relative_to(ROOT)}")
    shutil.copy2(source, PKG / "effective.py")
    print("  installed canval\\effective.py")

    ok = True
    for rel in [e[0] for e in EDITS] + ["canval/effective.py"]:
        try:
            py_compile.compile(str(ROOT / rel), doraise=True)
        except Exception as exc:                        # noqa: BLE001
            ok = False
            print(f"  SYNTAX ERROR in {rel}: {exc}")
    if not ok:
        print(f"\nRestore from {backup.name}.")
        return 1

    print(f"\nBacked up to {backup.name}. Everything compiles.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
