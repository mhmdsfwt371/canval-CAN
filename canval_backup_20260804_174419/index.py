"""Building the two local tables.

`refresh_catalogue` is cheap: about 40 calls for the whole CAN file list.

`build_device_index` is the expensive one, and it exists because of a real
gap in the API. XDM can answer "which CAN file does device X carry?" one
device at a time, but there is no endpoint for the reverse question,
"which devices carry CAN file Y?" -- devicesSdk/filter has no vehicle-model
field. So the reverse index has to be swept once and then kept fresh.

Narrow the sweep with hardware_ids and last_activity_from. Devices that
never reported carry no evidence and are not worth a call.
"""

from __future__ import annotations

import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from .parsers import parse_model, parse_sensors
from .store import (SOURCE_API, SOURCE_CONSOLE, clear_device, clear_source, connect,
                    log_sweep, record_device_can, record_history, record_run,
                    rebuild_sound_index, upsert_can_file, verify)
from .xdm import XdmClient, XdmError

# The "Vehicle model" override holds a CAN file_id, not a VMID. Confirmed
# on a live device: value 4633 resolved to file_id 4633, SHACMAN H3000S,
# whose VMID is 2888. Reading it as a VMID finds nothing.
_VMID_IN_VALUE = re.compile(r"<\s*VMID:\s*(\d+)", re.I)


def refresh_catalogue(client, db_path: str, source: str = SOURCE_API,
                      progress: Callable[[int], None] | None = None) -> dict:
    """Replace one source's catalogue rows, wholesale.

    Clearing first is the point. An earlier version merged runs and ended
    up with 6793 rows for a 3934-row catalogue, which made every count and
    every search quietly wrong.
    """
    total = 0
    unparsed_models = 0
    noteless = 0
    with_manual = 0
    expected = None

    def note_total(seen, reported):
        nonlocal expected
        if reported:
            expected = reported

    with connect(db_path) as conn:
        removed = clear_source(conn, source)

        iter_kwargs = {}
        if "progress" in getattr(client.iter_can_files, "__code__", type("x", (), {"co_varnames": ()})).co_varnames:
            iter_kwargs["progress"] = note_total

        for row in client.iter_can_files(**iter_kwargs):
            model = parse_model(row.get("model") or "")
            sensors = parse_sensors(row.get("notes") or "")

            upsert_can_file(
                conn, model, int(row["id"]), row.get("notes") or "", sensors,
                source=source,
                manual_url=row.get("manualUrl"),
                change_log=row.get("changeLog"),
                created_on=row.get("createdOn"),
            )

            total += 1
            if not model.ok:
                unparsed_models += 1
            if not sensors.sensors:
                noteless += 1
            if row.get("manualUrl"):
                with_manual += 1
            if progress and total % 250 == 0:
                progress(total)

        record_run(conn, source, total, expected)
        # Type-ahead reads this; rebuilding it here keeps it from ever
        # describing a catalogue that no longer exists.
        rebuild_sound_index(conn)
        changes = record_history(conn, source)
        check = verify(conn)

    return {
        "source": source,
        "replaced_rows": removed,
        "can_files": total,
        "server_reported": expected,
        "models_not_parsed": unparsed_models,
        "files_without_sensor_list": noteless,
        "files_with_install_guide": with_manual,
        "new_since_last_sync": changes["added"],
        "withdrawn": changes["removed"],
        "integrity_ok": check["ok"],
        "integrity_problems": check["problems"],
        "distinct_vmids": check["vehicles"],
    }


def _extract_model_entries(overrides: list[dict]) -> list[tuple[str, int | None, str]]:
    """Pull the vehicle-model overrides out of a device's settings.

    Returns (element_name, file_id, raw_value). A device can carry one per
    CAN bus; a bus configured but switched off still records the attempt,
    which is itself evidence worth keeping.
    """
    found = []
    for item in overrides or []:
        name = str(item.get("name") or "")
        if "vehicle model" not in name.lower():
            continue
        raw = str(item.get("value") or "").strip()
        file_id = int(raw) if raw.isdigit() else None
        if file_id is None:
            m = _VMID_IN_VALUE.search(raw)      # console form carries a label
            file_id = int(m.group(1)) if m else None
        found.append((name, file_id, raw))
    return found


def _build_device_index_v1(
    client,
    db_path: str,
    hardware_ids: list[int] | None = None,
    active_since_days: int | None = 60,
    concurrency: int = 6,
    sample_configs: int = 0,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Record which CAN file(s) each device carries.

    READ EVERY DEVICE, BY DEFAULT
    -----------------------------
    An earlier version sampled three devices per configuration and applied
    the answer to the whole config. It was 20x faster and it was wrong: a
    config of 992 devices whose first three happened to carry no override
    had all 992 written off as "no CAN file". The tool then reported real,
    working installs as never fitted -- the exact failure this project
    exists to prevent.

    The premise was also disproved by the data: 65 configurations contain
    both devices with a file and devices without, so the file is a
    per-device override and a sample cannot stand for the group.

    `sample_configs` still enables the shortcut for anyone who wants a
    rough picture fast, but it is off unless asked for, and it now only
    extrapolates a POSITIVE result -- if the sample finds nothing, the
    config is read in full, because "nothing" is precisely the answer that
    was wrong before.

    EVERY BUS IS RECORDED
    ---------------------
    A device holds one vehicle-model override per CAN bus and they are
    often different files. All of them are stored.
    """
    since = (
        int(time.time()) - active_since_days * 86400
        if active_since_days else None
    )

    devices = []
    for dev in client.iter_devices(hardware_ids=hardware_ids,
                                   last_activity_from=since):
        settings = dev.get("settings") or {}
        info = dev.get("information") or {}
        uid = settings.get("uid")
        if not uid:
            continue
        cfg = settings.get("configuration") or {}
        devices.append({
            "imei": uid,
            "hardware": (settings.get("hardware") or {}).get("name"),
            "config_id": cfg.get("currentConfigId"),
            "config": cfg.get("currentConfigName"),
            "last_activity": (info.get("activityUpdate") or {}).get("lastActivity"),
        })

    stats = {
        "devices": len(devices), "configs": 0, "calls": 0,
        "with_can_file": 0, "no_can_file": 0, "bus_entries": 0,
        "multi_bus_devices": 0, "errors": 0, "unread": 0,
        "mode": "sampled" if sample_configs else "full",
    }
    if not devices:
        return stats

    stats["configs"] = len({d["config_id"] for d in devices})
    calls = 0
    lock = threading.Lock()
    results: dict[str, list] = {}
    failed: set = set()          # reads that errored: unknown, not empty

    def read(dev):
        try:
            return dev, client.device_overrides(dev["imei"])
        except Exception as exc:                        # noqa: BLE001
            # Carry the imei on the exception so the caller knows which
            # device is unknown rather than empty.
            exc._imei = dev["imei"]
            raise

    def run(batch) -> dict:
        nonlocal calls
        out = {}
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(read, d) for d in batch]
            done = 0
            for fut in as_completed(futures):
                try:
                    dev, overrides = fut.result()
                except Exception as exc:                # noqa: BLE001
                    with lock:
                        stats["errors"] += 1
                        imei = getattr(exc, "_imei", None)
                        if imei:
                            failed.add(imei)
                    continue
                with lock:
                    calls += 1
                out[dev["imei"]] = _extract_model_entries(overrides)
                done += 1
                if progress and calls % 100 == 0:
                    progress(calls, len(devices))
        return out

    if not sample_configs:
        results = run(devices)
    else:
        by_config: dict = {}
        for d in devices:
            by_config.setdefault(d["config_id"], []).append(d)

        for cfg_id, members in by_config.items():
            sample = members[: max(1, sample_configs)]
            sampled = run(sample)
            results.update(sampled)

            rest = members[len(sample):]
            if not rest:
                continue

            found = [v for v in sampled.values() if v]
            same = {tuple(sorted(x[1] for x in v)) for v in found}
            # Extrapolate only a consistent, POSITIVE finding. An empty
            # sample says nothing about the rest of the config.
            if found and len(found) == len(sampled) and len(same) == 1:
                for d in rest:
                    results.setdefault(d["imei"], sampled[sample[0]["imei"]])
            else:
                results.update(run(rest))

    with connect(db_path) as conn:
        for dev in devices:
            # A read that errored tells us nothing. Storing it as "no CAN
            # file" turns an outage into a fact, and the tool would then
            # answer "never installed" for devices it never managed to
            # look at. Those are left out of device_can entirely and
            # recorded in the sweep log instead.
            if dev["imei"] in failed:
                stats["unread"] = stats.get("unread", 0) + 1
                log_sweep(conn, dev["imei"], "read_failed",
                          "not stored: status unknown")
                continue

            entries = results.get(dev["imei"]) or []
            clear_device(conn, dev["imei"])

            if entries:
                for element_name, file_id, raw in entries:
                    record_device_can(conn, dev["imei"], file_id, element_name,
                                      raw, dev["hardware"], dev["config"],
                                      dev["last_activity"])
                stats["with_can_file"] += 1
                stats["bus_entries"] += len(entries)
                if len({e[1] for e in entries}) > 1:
                    stats["multi_bus_devices"] += 1
                log_sweep(conn, dev["imei"], "indexed",
                          ",".join(str(e[1]) for e in entries))
            else:
                record_device_can(conn, dev["imei"], None, "(none)", None,
                                  dev["hardware"], dev["config"],
                                  dev["last_activity"])
                stats["no_can_file"] += 1
                log_sweep(conn, dev["imei"], "no_can_file")

    stats["calls"] = calls
    return stats


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
