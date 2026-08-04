"""The second gate: proof that a CAN file actually produced readings.

READ THIS BEFORE CHANGING THE CLASSIFIER
----------------------------------------
The platform's per-parameter timestamp is the time the value **last
changed**, not the time it was last received. Confirmed on one device
across two snapshots 22 minutes apart while it idled stationary:

    engine hours  resolution 0.05 h = 3 min   stamp said 2 min ago
    total fuel    resolution 0.5 L  ~ 22 min  stamp said 12 min ago
    odometer      vehicle at 0 km/h           stamp said 19 min ago
                                              = when it last moved
    ACC           on since the shift started  stamp said 4 hours ago
    firmware name never changes               stamp said 199 days ago

So an old timestamp does NOT mean a dead signal. It usually means the
value simply had no reason to change. A parked truck's odometer is stale
by definition, and punishing it would send a technician to a working
install.

The rule that follows: staleness is never evidence of absence. Only
*change* is evidence of life. Everything below is built on that.
"""

from __future__ import annotations

import abc
import time
from dataclasses import dataclass, field
from enum import Enum

# --------------------------------------------------------------- decoding
#
# Raw CAN counters are scaled integers. Each factor here was derived by
# comparing a raw signal param against the value the platform displayed for
# the same device at the same moment, then re-confirmed against a second
# snapshot 22 minutes later. They follow the usual J1939 resolutions.


@dataclass(frozen=True)
class Decoder:
    label: str
    scale: float = 1.0
    offset: float = 0.0
    unit: str = ""
    calibrated: bool = False   # platform applies a per-vehicle curve, see below
    verified_on: str = ""


DECODERS: dict[str, Decoder] = {
    "sensor_16386": Decoder("Total engine hours", 0.05, 0.0, "h",
                            verified_on="66097 -> 3304.85, +0.35h over 22min"),
    "sensor_16385": Decoder("Total fuel used", 0.5, 0.0, "L",
                            verified_on="43651 -> 21825.5, +0.5L over 22min"),
    "sensor_16387": Decoder("Odometer", 0.005, 0.0, "km",
                            verified_on="11694080 -> 58470.4"),
    "sensor_8200": Decoder("Engine temperature", 1.0, -40.0, "C",
                           verified_on="128 -> 88"),
    "sensor_12300": Decoder("Engine speed", 0.125, 0.0, "rpm",
                            verified_on="8199 -> 1024.9"),
    "sensor_12301": Decoder("Weight", 10.0, 0.0, "kg",
                            verified_on="3000 -> 30000, 3009 -> 30090"),

    # Fuel level is different and must not be treated like the others.
    # Raw 92 displayed as 462.5 L gives a ratio of 5.03, which is not a
    # standard resolution. At the J1939 rate of 0.4 %/bit, 92 is 36.8 %,
    # and 462.5 L at 36.8 % implies a ~1257 L tank. The label "1st CAL"
    # says it plainly: the litres come from a per-vehicle calibration
    # curve configured in the platform, not from the CAN file.
    #
    # Consequence for this tool: the CAN file can only be credited with
    # delivering the raw percentage. Litres are a platform feature and
    # depend on someone having done the calibration.
    "sensor_8219": Decoder("Fuel level", 0.4, 0.0, "%", calibrated=True,
                           verified_on="92 -> 36.8% raw; 462.5 L is calibrated"),
}


def decode(key: str, raw_value) -> tuple[float | None, str, bool]:
    """Return (engineering value, unit, needs_calibration)."""
    try:
        raw = float(raw_value)
    except (TypeError, ValueError):
        return None, "", False
    dec = DECODERS.get(key)
    if not dec:
        return raw, "", False
    return raw * dec.scale + dec.offset, dec.unit, dec.calibrated


# ------------------------------------------------------- parameter classes
#
# How fast a signal is *expected* to change decides how to read its
# timestamp. Mixing these up is what makes a naive age threshold wrong.


class ParamClass(str, Enum):
    CONTINUOUS = "continuous"   # rpm, speed, voltage, temperature
    COUNTER = "counter"         # odometer, engine hours, total fuel
    STATE = "state"             # ignition, doors, acc
    STATIC = "static"           # firmware name, protocol, function id
    UNKNOWN = "unknown"


_CLASS_BY_KEY: dict[str, ParamClass] = {
    "sensor_12300": ParamClass.CONTINUOUS,
    "sensor_8200": ParamClass.CONTINUOUS,
    "sensor_12301": ParamClass.CONTINUOUS,
    "sensor_8219": ParamClass.CONTINUOUS,
    "ePwrV": ParamClass.CONTINUOUS,
    "spd": ParamClass.CONTINUOUS,
    "sensor_16385": ParamClass.COUNTER,
    "sensor_16386": ParamClass.COUNTER,
    "sensor_16387": ParamClass.COUNTER,
    "acc": ParamClass.STATE,
    "fw": ParamClass.STATIC,
    "fw_name": ParamClass.STATIC,
    "protocol": ParamClass.STATIC,
    "functionID": ParamClass.STATIC,
    "fun_id": ParamClass.STATIC,
}


def classify_param(key: str) -> ParamClass:
    return _CLASS_BY_KEY.get(key, ParamClass.UNKNOWN)


# ---------------------------------------------------------------- adapter

@dataclass
class Parameter:
    key: str                  # e.g. "sensor_12300"
    value: object
    changed_at: int           # unix seconds, when the value last CHANGED


@dataclass
class SensorDefinition:
    display_name: str         # e.g. "Total fuel"
    sensor_type: str          # e.g. "Total Fuel Can"
    parameter_key: str        # e.g. "sensor_16385"


class MonitorAdapter(abc.ABC):
    """Implement these three against the tracking platform."""

    @abc.abstractmethod
    def find_by_imei(self, imei: str) -> str | None:
        """Platform-side unit id for an IMEI, or None if unknown."""

    @abc.abstractmethod
    def parameters(self, unit_id: str) -> tuple[list[Parameter], int]:
        """All signal params for a unit, plus the unit's last report time."""

    @abc.abstractmethod
    def sensor_definitions(self, unit_id: str) -> list[SensorDefinition]:
        """The name-to-parameter mapping configured on the unit."""


class NotConfigured(MonitorAdapter):
    """Placeholder until the tracking platform's API docs arrive."""

    _MSG = (
        "No monitoring adapter configured. Implement MonitorAdapter with the "
        "tracking platform's API and pass it to evaluate_device()."
    )

    def find_by_imei(self, imei): raise NotImplementedError(self._MSG)
    def parameters(self, unit_id): raise NotImplementedError(self._MSG)
    def sensor_definitions(self, unit_id): raise NotImplementedError(self._MSG)


# ------------------------------------------------------------- verdicts

PROVEN = "proven"        # observed changing across two polls
LIVE = "live"            # changed while its driver was active -> working
IDLE = "idle"            # had no reason to change; says nothing either way
STALLED = "stalled"      # its driver ran for ages and it still did not move
ABSENT = "absent"        # never appears at all

# Backwards-compatible aliases for the earlier names.
LIKELY = LIVE
INCONCLUSIVE = IDLE
SUSPECT = STALLED


class Driver(str, Enum):
    """What has to be happening for a signal to have any reason to change.

    Judging staleness without this is the trap: a parked truck's odometer
    is frozen by definition, and calling that suspicious sends a technician
    to a working install. A signal is only doubtful once the thing that
    drives it has been running for a long time and it still has not moved.
    """
    MOTION = "motion"    # only while the vehicle is actually moving
    ENGINE = "engine"    # only while the engine is running
    ALWAYS = "always"    # free-running, e.g. supply voltage, GPS
    EVENT = "event"      # discrete events only: ignition, doors, loading
    STATIC = "static"    # firmware names and the like


# Sensor types come straight from the platform's own `t` field.
_DRIVER_BY_TYPE = {
    "milage": Driver.MOTION,
    "rpm": Driver.ENGINE,
    "eng_hour": Driver.ENGINE,
    "engine_temperature": Driver.ENGINE,
    "total_fuel_can": Driver.ENGINE,
    "fuel_level": Driver.ENGINE,
    "ex_battery_volt": Driver.ALWAYS,
    "in_battery_volt": Driver.ALWAYS,
    "weight": Driver.EVENT,
    "acc": Driver.EVENT,
}

_DRIVER_BY_KEY = {
    "spd": Driver.MOTION, "sensor_16387": Driver.MOTION,
    "sensor_12300": Driver.ENGINE, "sensor_8200": Driver.ENGINE,
    "sensor_16385": Driver.ENGINE, "sensor_16386": Driver.ENGINE,
    "sensor_8219": Driver.ENGINE,
    "ePwrV": Driver.ALWAYS, "sat": Driver.ALWAYS, "hdop": Driver.ALWAYS,
    "sensor_12292": Driver.ALWAYS,
    "acc": Driver.EVENT, "sensor_12301": Driver.EVENT,
    "fw": Driver.STATIC, "fw_name": Driver.STATIC, "protocol": Driver.STATIC,
    "functionID": Driver.STATIC, "fun_id": Driver.STATIC,
    "warningID": Driver.STATIC, "war_id": Driver.STATIC,
}

# How long a driver may run before silence becomes suspicious. Generous:
# a wrong "stalled" costs the wasted trip this tool exists to prevent.
# Fuel and volume counters get hours because their resolution is coarse --
# 0.5 L per step at 1.4 L/h idle is one step every twenty minutes.
_PATIENCE = {
    Driver.MOTION: 30 * 60,
    Driver.ENGINE: 6 * 3600,
    Driver.ALWAYS: 30 * 60,
    Driver.EVENT: None,
    Driver.STATIC: None,
}


@dataclass
class VehicleState:
    """Read from lu.unit_state.motion, which carries a state and its cdt."""
    state: str = ""
    since: int = 0

    @property
    def engine_on(self) -> bool:
        return "engine_on" in self.state

    @property
    def moving(self) -> bool:
        return self.state.startswith("moving")

    def enables(self, driver: "Driver") -> bool:
        if driver is Driver.MOTION:
            return self.moving
        if driver is Driver.ENGINE:
            return self.engine_on
        if driver is Driver.ALWAYS:
            return True
        return False       # EVENT and STATIC are never time-judged

    def describe(self) -> str:
        return {
            "moving_engine_on": "moving",
            "stationary_engine_on": "stopped, engine running",
            "stationary_engine_off": "parked",
        }.get(self.state, self.state or "unknown")


def driver_for(key: str, sensor_type: str | None = None) -> Driver:
    if sensor_type and sensor_type in _DRIVER_BY_TYPE:
        return _DRIVER_BY_TYPE[sensor_type]
    return _DRIVER_BY_KEY.get(key, Driver.EVENT)


@dataclass
class Reading:
    key: str
    display_name: str | None
    param_class: ParamClass
    verdict: str
    raw: object
    value: float | None
    unit: str
    needs_calibration: bool
    age_seconds: int
    driver: Driver = Driver.EVENT
    reason: str = ""            # plain-language why, for the report
    sensor_type: str = ""       # platform type, the join key to the catalogue


# How long a class may sit unchanged before it looks wrong. Generous on
# purpose: a false "suspect" costs a wasted trip, the same thing this tool
# exists to prevent.
_SUSPECT_AFTER = {
    ParamClass.CONTINUOUS: 6 * 3600,
    ParamClass.COUNTER: 7 * 86400,
    ParamClass.STATE: 30 * 86400,
    ParamClass.STATIC: None,      # never suspect
    ParamClass.UNKNOWN: 30 * 86400,
}

_LIKELY_WITHIN = {
    ParamClass.CONTINUOUS: 600,
    ParamClass.COUNTER: 3600,
    ParamClass.STATE: 6 * 3600,
    ParamClass.STATIC: None,
    ParamClass.UNKNOWN: 3600,
}


def read_snapshot(
    params: list[Parameter],
    last_report: int,
    definitions: list[SensorDefinition] | None = None,
    state: VehicleState | None = None,
) -> list[Reading]:
    """Judge one snapshot, in the light of what the vehicle was doing.

    Age alone is not evidence. A signal is only doubtful once the thing
    that drives it has been running long enough that silence cannot be
    explained, so the window is measured from whichever is later: the
    parameter's own last change, or the moment the vehicle entered its
    current state.
    """
    names = {d.parameter_key: d.display_name for d in (definitions or [])}
    types = {d.parameter_key: d.sensor_type for d in (definitions or [])}
    state = state or VehicleState()
    out = []

    for p in params:
        cls = classify_param(p.key)
        drv = driver_for(p.key, types.get(p.key))
        age = max(0, int(last_report) - int(p.changed_at))

        # how long the driver has been active without this moving
        in_state = max(0, int(last_report) - int(state.since)) if state.since else 0
        silent_while_active = min(age, in_state) if state.enables(drv) else 0
        patience = _PATIENCE[drv]

        if age <= 300:
            verdict, reason = LIVE, "updating now"
        elif not state.enables(drv):
            verdict = IDLE
            reason = (f"needs {drv.value}; vehicle is {state.describe()}")
        elif patience is None:
            verdict = IDLE
            reason = ("changes only when something happens"
                      if drv is Driver.EVENT else "fixed value")
        elif silent_while_active > patience:
            verdict = STALLED
            reason = (f"{drv.value} active {_dur(in_state)} with no change")
        else:
            verdict = LIVE if age <= patience else IDLE
            reason = f"within normal interval for {drv.value}"

        value, unit, calibrated = decode(p.key, p.value)
        out.append(
            Reading(
                key=p.key, display_name=names.get(p.key), param_class=cls,
                verdict=verdict, raw=p.value, value=value, unit=unit,
                needs_calibration=calibrated, age_seconds=age,
                driver=drv, reason=reason,
                sensor_type=types.get(p.key, ""),
            )
        )

    order = {LIVE: 0, IDLE: 1, STALLED: 2}
    out.sort(key=lambda r: (order.get(r.verdict, 3), r.age_seconds))
    return out


def _dur(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def prove_by_diff(before: list[Parameter], after: list[Parameter]) -> dict[str, str]:
    """The definitive test: poll twice, keep what moved.

    Anything whose value or change-time differs between the two polls is
    delivering live data, whatever its timestamp looked like in isolation.
    Take the second poll after the vehicle has been driven, otherwise the
    counters have nothing to say.
    """
    first = {p.key: p for p in before}
    verdicts: dict[str, str] = {}

    for p in after:
        prev = first.get(p.key)
        if prev is None:
            verdicts[p.key] = LIKELY          # appeared between polls
        elif str(prev.value) != str(p.value) or prev.changed_at != p.changed_at:
            verdicts[p.key] = PROVEN
        else:
            verdicts[p.key] = INCONCLUSIVE

    for key in first:
        verdicts.setdefault(key, INCONCLUSIVE)
    return verdicts


@dataclass
class DeviceEvidence:
    imei: str
    unit_id: str | None = None
    last_report: int = 0
    readings: list[Reading] = field(default_factory=list)
    error: str | None = None
    # parameters frozen at unit setup: declared but never once carrying data
    never_delivered: set = field(default_factory=set)
    # param key -> SensorSpec, so values can be rendered the platform's way
    specs: dict = field(default_factory=dict)

    def by_verdict(self, *wanted: str) -> list[Reading]:
        return [r for r in self.readings if r.verdict in wanted]


def evaluate_device(adapter: MonitorAdapter, imei: str) -> DeviceEvidence:
    ev = DeviceEvidence(imei=imei)
    try:
        unit = adapter.find_by_imei(imei)
        if not unit:
            ev.error = "not found on the monitoring platform"
            return ev
        ev.unit_id = unit
        params, last_report = adapter.parameters(unit)
        ev.last_report = last_report or int(time.time())
        ev.readings = read_snapshot(params, ev.last_report,
                                    adapter.sensor_definitions(unit))
    except NotImplementedError as exc:
        ev.error = str(exc)
    except Exception as exc:                      # noqa: BLE001
        ev.error = f"{type(exc).__name__}: {exc}"
    return ev


def is_named_sensor(reading: "Reading") -> bool:
    """Is this something a customer would recognise?

    A raw key like sensor_12289 is a wire-level parameter with no sensor
    configured against it. Reporting those alongside "Engine Hours" buries
    the answer in noise and implies a precision we do not have -- nobody
    asked whether sensor_12289 works.
    """
    return bool(reading.display_name)


def aggregate(evidences: list[DeviceEvidence], named_only: bool = True) -> dict:
    """Roll several installs of the same CAN file into one verdict per signal.

    Best evidence wins, majority does not. A parked truck contributes no
    evidence either way -- it must not be allowed to vote a signal down.
    One device proving a signal proves the file delivers it; twenty parked
    devices prove nothing at all.

    A signal is only called suspect when it is frozen on every device that
    had the activity to move it.
    """
    usable = [e for e in evidences if not e.error and e.readings]
    if not usable:
        return {"devices": 0, "signals": {}}

    tally: dict[str, dict] = {}
    for ev in usable:
        for r in ev.readings:
            if named_only and not is_named_sensor(r):
                continue
            name = r.display_name or r.key
            slot = tally.setdefault(
                name,
                {"key": r.key, "class": r.param_class.value, "unit": r.unit,
                 "needs_calibration": r.needs_calibration,
                 PROVEN: 0, LIKELY: 0, INCONCLUSIVE: 0, SUSPECT: 0},
            )
            slot[r.verdict] = slot.get(r.verdict, 0) + 1

    n = len(usable)
    signals = {}
    for name, s in tally.items():
        positive = s[PROVEN] + s[LIKELY]
        if positive >= 2:
            verdict = "supported"
        elif positive == 1:
            verdict = "likely supported"
        elif s[SUSPECT] and not s[INCONCLUSIVE]:
            verdict = "not delivered"
        else:
            verdict = "no evidence yet"

        signals[name] = {
            "parameter": s["key"], "class": s["class"], "unit": s["unit"],
            "verdict": verdict, "devices_positive": positive, "of": n,
            "needs_calibration": s["needs_calibration"],
        }

    rank = {"supported": 0, "likely supported": 1,
            "no evidence yet": 2, "not delivered": 3}
    return {
        "devices": n,
        "unreadable": len(evidences) - n,
        "signals": dict(sorted(signals.items(),
                               key=lambda kv: (rank[kv[1]["verdict"]], kv[0]))),
    }
