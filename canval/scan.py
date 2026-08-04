"""Collecting evidence from the tracking platform at fleet scale.

WHY TWO PHASES
--------------
`last_update` and `sensors` return every parameter and every sensor
definition for every unit. One sampled device came back at 17 KB, so a
10,000 vehicle estate is roughly 170 MB. Pulled at 1000 per page that is
17 MB in a single response: slow, easy to time out, and a dropped page
loses a thousand units of work.

    phase 1   projection ["basic"] only
              tiny rows, just id / imei / name / model, for the whole fleet

    phase 2   the heavy blocks, for the units that actually matter --
              the ones carrying the CAN file being asked about. That is
              usually dozens, not thousands.

Phase 1 runs once a night. Phase 2 runs against a shortlist, so the
expensive call never touches the whole estate.

RESUMABILITY
------------
Every page is written before the next is requested, and progress is kept
in `scan_progress`. A run that dies at page 47 resumes at page 47 instead
of starting over.

BE A GOOD CITIZEN
-----------------
Fifty heavy requests in a row against a production platform looks like an
attack. `delay` spaces them out, and the default page size is deliberately
small. Confirm the provider's rate limits before running this against a
large customer, and scan one customer at a time rather than all at once.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

from .afaqy import AfaqyAdapter, setup_batch, to_definitions
from .monitor import DeviceEvidence, aggregate, read_snapshot

DEFAULT_PAGE = 200          # small on purpose: retries stay cheap
DEFAULT_DELAY = 0.5         # seconds between pages


@dataclass
class ScanStats:
    pages: int = 0
    units: int = 0
    with_imei: int = 0
    errors: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "pages": self.pages,
            "units": self.units,
            "with_imei": self.with_imei,
            "errors": len(self.errors),
        }


def scan_fleet_basic(
    adapter: AfaqyAdapter,
    on_page: Callable[[list], None],
    page_size: int = DEFAULT_PAGE,
    delay: float = DEFAULT_DELAY,
    start_offset: int = 0,
    max_pages: int | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> ScanStats:
    """Phase 1: the whole fleet, identity fields only.

    `on_page` is handed each page as it arrives and must persist it before
    returning -- that is what makes the scan resumable.
    """
    stats = ScanStats()
    offset = start_offset

    while True:
        if max_pages is not None and stats.pages >= max_pages:
            break

        try:
            raw = adapter._post(
                "/v1/units",
                {
                    "offset": offset,
                    "limit": page_size,
                    "simplify": 1,
                    "projection": ["basic"],
                },
            )
        except Exception as exc:                       # noqa: BLE001
            stats.errors.append(f"offset {offset}: {type(exc).__name__}: {exc}")
            break

        items = (raw or {}).get("data")
        if isinstance(items, dict):
            items = items.get("items") or items.get("units") or []
        items = items or []

        rows = []
        for unit in items:
            if not isinstance(unit, dict):
                continue
            imei = unit.get("i")
            rows.append(
                {
                    "unit_id": unit.get("id"),
                    "imei": str(imei) if imei else None,
                    "name": unit.get("n"),
                    "device_model": unit.get("device_model"),
                    "offset": offset,
                }
            )
            if imei:
                stats.with_imei += 1

        on_page(rows)                                  # persist before continuing

        stats.pages += 1
        stats.units += len(items)
        if progress:
            progress(stats.units, offset)

        if len(items) < page_size:
            break
        offset += page_size
        if delay:
            time.sleep(delay)

    return stats


def collect_evidence(
    adapter: AfaqyAdapter,
    unit_ids: Iterable[str],
    delay: float = DEFAULT_DELAY,
    progress: Callable[[int, int], None] | None = None,
) -> list[DeviceEvidence]:
    """Phase 2: the heavy read, for a shortlist only.

    One /units/view per unit. That is fine at the scale this runs at --
    the shortlist is the devices carrying one CAN file, which is dozens.
    """
    ids = list(unit_ids)
    out: list[DeviceEvidence] = []

    for i, unit_id in enumerate(ids, 1):
        ev = DeviceEvidence(imei="", unit_id=unit_id)
        try:
            view = adapter.view(unit_id)
            ev.imei = view.imei or ""
            ev.last_report = view.last_message or 0
            ev.readings = read_snapshot(
                view.parameters, ev.last_report, to_definitions(view)
            )
            ev.never_delivered = setup_batch(view)
            ev.specs = view.spec_by_param
        except Exception as exc:                       # noqa: BLE001
            ev.error = f"{type(exc).__name__}: {exc}"

        out.append(ev)
        if progress:
            progress(i, len(ids))
        if delay and i < len(ids):
            time.sleep(delay)

    return out


def verdict_for_file(evidences: list[DeviceEvidence]) -> dict:
    """Roll a CAN file's installs into one answer.

    Signals frozen at unit setup on every device are reported separately
    from signals that simply had no reason to move: the first means the
    file does not deliver them, the second means nothing at all.
    """
    summary = aggregate(evidences)

    never = [e.never_delivered for e in evidences
             if not e.error and getattr(e, "never_delivered", None)]
    if never:
        always_dead = set.intersection(*never)
        by_param = {}
        for ev in evidences:
            for r in ev.readings:
                by_param.setdefault(r.key, r.display_name or r.key)
        summary["never_delivered"] = sorted(
            by_param.get(k, k) for k in always_dead
        )
    else:
        summary["never_delivered"] = []

    return summary
