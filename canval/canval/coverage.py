"""Declared versus delivered.

The catalogue says a file offers sixteen sensors. The installs prove seven.
The whole value of this tool is the difference between those two numbers,
and until now it was never stated -- a reader saw "declared usable (16)"
and reasonably assumed sixteen.

Making the comparison needs a join, because the two systems name the same
thing differently:

    catalogue                     platform type      platform label
    Total Distance (Mileage)  ->  milage         ->  "ODO"
    Fuel Level [%]            ->  fuel_level     ->  "Fuel Level 1st CAL"
    Engine Hours              ->  eng_hour       ->  "Total Engine Hours"

The platform's `t` field is the reliable key -- it is a fixed vocabulary,
unlike the display names, which installers type by hand.

WHAT IS NOT CLAIMED
-------------------
Plenty of catalogue sensors have no counterpart in the platform's
vocabulary: door switches, indicator lamps, warning states. Those are
reported as "cannot be checked from here" rather than being quietly folded
into either column. Counting an uncheckable sensor as failed would invent
a fault; counting it as working would invent a capability.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Catalogue sensor name -> platform sensor type. Left side matched loosely
# (lowercased, punctuation stripped) because the catalogue is inconsistent
# about brackets, units and asterisks.
_CANONICAL = {
    "engine speed": "rpm",
    "rpm": "rpm",
    "vehicle speed": "speed",
    "total distance mileage": "milage",
    "total distance": "milage",
    "total distance high resolution": "milage",
    "mileage": "milage",
    "engine hours": "eng_hour",
    "total engine hours": "eng_hour",
    "engine temperature": "engine_temperature",
    "fuel level": "fuel_level",
    "fuel level 2": "fuel_level",
    "fuel consumption": "total_fuel_can",
    "total fuel used": "total_fuel_can",
    "total fuel used high resolution": "total_fuel_can",
    "total fuel": "total_fuel_can",
    "axle weight": "weight",
    "gross vehicle weight": "weight",
    "cargo weight": "weight",
    "trailer weight": "weight",
    "weight": "weight",
    "ignition": "acc",
    "acc": "acc",
}

# Types the platform exposes. Anything outside this cannot be verified.
PLATFORM_TYPES = {
    "rpm", "speed", "milage", "eng_hour", "engine_temperature",
    "fuel_level", "total_fuel_can", "weight", "acc",
    "ex_battery_volt", "in_battery_volt",
}

_CLEAN = re.compile(r"[\[\]()*%]|j1939|\bl\b|\bkm\b", re.I)


def canonical(sensor_name: str) -> str | None:
    """Catalogue sensor name -> platform type, or None if not comparable."""
    if not sensor_name:
        return None
    text = _CLEAN.sub(" ", str(sensor_name)).lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    if text in _CANONICAL:
        return _CANONICAL[text]
    # try dropping a trailing digit, e.g. "fuel level 2"
    stripped = re.sub(r"\s+\d+$", "", text)
    return _CANONICAL.get(stripped)


PROVEN = "proven"          # seen live on at least one install
NEVER = "never"            # present but frozen since install, on every install
ABSENT = "absent"          # declared, but no such parameter on any install
UNKNOWN = "unknown"        # installs exist but gave no usable evidence yet
UNCHECKABLE = "uncheckable" # the platform has no equivalent to compare against


@dataclass
class SensorVerdict:
    declared_name: str
    canonical_type: str | None
    status: str
    devices_proven: int = 0
    devices_checked: int = 0
    note: str = ""


@dataclass
class Comparison:
    verdicts: list = field(default_factory=list)
    devices_checked: int = 0

    def by_status(self, *wanted: str) -> list:
        return [v for v in self.verdicts if v.status in wanted]

    @property
    def summary(self) -> str:
        n = lambda s: len(self.by_status(s))                      # noqa: E731
        return (f"{n(PROVEN)} proven, {n(ABSENT) + n(NEVER)} not delivered, "
                f"{n(UNKNOWN)} unproven, {n(UNCHECKABLE)} uncheckable")


def compare(declared: list[str], evidences: list) -> Comparison:
    """Line the catalogue's promises up against what the installs show.

    `evidences` are DeviceEvidence objects; only those that were readable
    contribute. With none readable everything lands in UNKNOWN, which is
    the honest answer -- not a list of failures.
    """
    from .monitor import LIVE, PROVEN as LIVE_PROVEN

    readable = [e for e in evidences if not e.error and e.readings]
    out = Comparison(devices_checked=len(readable))

    # What each device actually delivered, keyed by platform type.
    live_types: dict[str, int] = {}
    seen_types: dict[str, int] = {}
    for ev in readable:
        types_live, types_seen = set(), set()
        for r in ev.readings:
            t = getattr(r, "sensor_type", None) or _type_of(r, ev)
            if not t:
                continue
            types_seen.add(t)
            if r.verdict in (LIVE, LIVE_PROVEN):
                types_live.add(t)
        for t in types_seen:
            seen_types[t] = seen_types.get(t, 0) + 1
        for t in types_live:
            live_types[t] = live_types.get(t, 0) + 1

    for name in declared:
        ctype = canonical(name)

        if ctype is None or ctype not in PLATFORM_TYPES:
            out.verdicts.append(SensorVerdict(
                name, ctype, UNCHECKABLE,
                note="no equivalent on the tracking platform"))
            continue

        if not readable:
            out.verdicts.append(SensorVerdict(
                name, ctype, UNKNOWN, devices_checked=0,
                note="no readable install yet"))
            continue

        proven = live_types.get(ctype, 0)
        present = seen_types.get(ctype, 0)

        if proven:
            out.verdicts.append(SensorVerdict(
                name, ctype, PROVEN, proven, len(readable)))
        elif present:
            # It exists on the device but never moved while its driver ran.
            out.verdicts.append(SensorVerdict(
                name, ctype, NEVER, 0, len(readable),
                note="configured but has not carried data"))
        else:
            out.verdicts.append(SensorVerdict(
                name, ctype, ABSENT, 0, len(readable),
                note="not configured on any install checked"))

    return out


def _type_of(reading, evidence) -> str | None:
    """Recover the platform type for a reading.

    Readings carry the display name; the type lives in the unit's sensor
    definitions, so fall back to canonicalising the display name.
    """
    spec = (getattr(evidence, "specs", None) or {}).get(reading.key)
    if spec is not None and getattr(spec, "type", None):
        return spec.type
    return canonical(reading.display_name or "")
