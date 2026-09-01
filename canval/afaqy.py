"""Adapter for the tracking platform (api.afaqy.pro).

All of this is read off a real capture of POST /units/view, not inferred.

REQUEST
-------
    POST {BASE}/units/view?token=<token>
    Content-Type: application/json

    {"data": "{\\"id\\":\\"<unit id>\\",\\"simplify\\":1}"}

Note the body: the payload is a JSON *string* nested inside a `data`
field, so it has to be encoded twice. Sending a plain object fails.

RESPONSE
--------
    data.i                                     IMEI
    data.id                                    unit id (used in requests)
    data.n                                     display name
    data.device_model                          e.g. "LX45 CAN"
    data.crat / data.upat                      created / updated (ISO, local)
    data.sensors[]                             sensor definitions, see below
    data.lu.dtt                                device time of last message (ms)
    data.lu.dts                                server receive time (ms)
    data.lu.chPrams.<param>                    {className, v, cdt}
    data.lu.sensors_chDate.<type>[0].changeDate

`cdt` is a CHANGE time, not a receive time -- the field name says so, and
the block `sensors_chDate` spells it out. A stale value is one with no
reason to move, not a dead signal.

THE CONVERSION IS IN THE PAYLOAD
--------------------------------
Each sensor definition carries its own conversion:

    n               display name        "Total Engine Hours"
    t               type                "eng_hour"
    param           raw parameter key   "sensor_16386"
    units           "H"
    formula         one of * / - +
    formula_value   the operand         0.05
    calibration     [{x, y}, ...]       optional curve, raw -> engineering
    result_type     "value" or "logic"
    text_0 / text_1 labels for logic sensors

So the scale factors do not have to be guessed, and must not be
hardcoded: they are configured per unit and shipped with every response.
Reading them from the payload also means a unit with an unusual setup
decodes correctly instead of quietly wrong.

Calibration takes the RAW value on its x axis, not a percentage.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime

from .monitor import MonitorAdapter, Parameter, SensorDefinition, VehicleState

BASE_URL = "https://api.afaqy.pro"

# Sensor-type keys as they appear in `t` and under sensors_chDate.
SENSOR_TYPE_LABELS = {
    "acc": "Ignition ACC",
    "ex_battery_volt": "External Battery Volt",
    "in_battery_volt": "Internal Battery Volt",
    "engine_temperature": "Engine Temperature",
    "rpm": "RPM",
    "fuel_level": "Fuel Level",
    "milage": "Engine Mileage",
    "eng_hour": "Engine Hours",
    "weight": "Weight",
    "total_fuel_can": "Total Fuel Can",
}

_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})")


def parse_iso(text) -> int | None:
    if not text:
        return None
    m = _ISO_RE.match(str(text))
    if not m:
        return None
    try:
        return int(datetime(*map(int, m.groups())).timestamp())
    except ValueError:
        return None


# ------------------------------------------------------------- conversion

@dataclass
class SensorSpec:
    """One sensor definition, with everything needed to decode it."""
    name: str
    type: str
    param: str
    units: str = ""
    formula: str = ""
    formula_value: object = None
    calibration: list = field(default_factory=list)
    result_type: str = "value"
    text_0: str = ""
    text_1: str = ""

    @property
    def calibrated(self) -> bool:
        return len(self.calibration) >= 2

    def convert(self, raw):
        """Raw parameter value -> engineering value, the platform's way."""
        try:
            x = float(raw)
        except (TypeError, ValueError):
            return raw

        if self.calibrated:
            return _interpolate(x, self.calibration)

        fv = self.formula_value
        if fv in ("", None) or not self.formula:
            return x
        try:
            fv = float(fv)
        except (TypeError, ValueError):
            return x

        if self.formula == "*":
            return x * fv
        if self.formula == "/":
            return x / fv if fv else x
        if self.formula == "-":
            return x - fv
        if self.formula == "+":
            return x + fv
        return x

    def render(self, raw) -> str:
        """Human-readable, matching what the platform shows on screen.

        Two decimals with trailing zeros stripped reproduces the display
        exactly, including 1388.625 -> 1388.62 (round-half-to-even).
        """
        if self.result_type == "logic":
            try:
                return self.text_1 if float(raw) else self.text_0
            except (TypeError, ValueError):
                return str(raw)
        value = self.convert(raw)
        if isinstance(value, float):
            value = round(value, 2)
            if value == int(value):
                value = int(value)
        return f"{value} {self.units}".strip()


def _interpolate(x: float, points: list) -> float:
    """Piecewise-linear lookup, clamped at both ends.

    Verified against the sampled unit: raw 92 on its eight-point fuel
    curve gives 462.5 L, exactly what the platform displayed.
    """
    pts = sorted(
        ({"x": float(p["x"]), "y": float(p["y"])} for p in points
         if isinstance(p, dict) and "x" in p and "y" in p),
        key=lambda p: p["x"],
    )
    if not pts:
        return x
    if x <= pts[0]["x"]:
        return pts[0]["y"]
    if x >= pts[-1]["x"]:
        return pts[-1]["y"]
    for a, b in zip(pts, pts[1:]):
        if a["x"] <= x <= b["x"]:
            span = b["x"] - a["x"]
            if span == 0:
                return a["y"]
            return a["y"] + (x - a["x"]) * (b["y"] - a["y"]) / span
    return x


def parse_sensor_specs(payload: dict) -> list[SensorSpec]:
    data = (payload or {}).get("data") or {}
    out = []
    for s in data.get("sensors") or []:
        if not isinstance(s, dict):
            continue
        out.append(
            SensorSpec(
                name=str(s.get("n") or ""),
                type=str(s.get("t") or ""),
                param=str(s.get("param") or ""),
                units=str(s.get("units") or ""),
                formula=str(s.get("formula") or ""),
                formula_value=s.get("formula_value"),
                calibration=s.get("calibration") or [],
                result_type=str(s.get("result_type") or "value"),
                text_0=str(s.get("text_0") or ""),
                text_1=str(s.get("text_1") or ""),
            )
        )
    return out


# ------------------------------------------------------------- unit view

@dataclass
class UnitView:
    unit_id: str | None
    imei: str | None
    name: str | None
    device_model: str | None
    created_at: int | None
    last_message: int | None
    server_time: int | None
    parameters: list[Parameter]
    specs: list[SensorSpec]
    sensor_change: dict[str, int]
    state: VehicleState = field(default_factory=VehicleState)
    # Values that arrived without a change time. Kept apart from
    # `parameters` on purpose: setup_batch() and the monitor both reason
    # about when a value last moved, and a fabricated timestamp would
    # corrupt them. Readers that only care WHETHER a value exists use
    # `values()` below.
    untimed: list[Parameter] = field(default_factory=list)

    @property
    def spec_by_param(self) -> dict[str, SensorSpec]:
        return {s.param: s for s in self.specs if s.param}

    def values(self) -> list[Parameter]:
        """Everything the unit is actually carrying a value for.

        Use this to answer "is this sensor alive". Use `parameters` only
        when the question is about when a value last moved.
        """
        return [p for p in self.parameters + self.untimed
                if p.value not in (None, "")]


def parse_unit_view(payload: dict) -> UnitView:
    data = (payload or {}).get("data") or {}
    lu = data.get("lu") or {}

    dtt, dts = lu.get("dtt"), lu.get("dts")

    # A parameter used to be thrown away whole when it had no `cdt`,
    # taking its value with it. That is backwards: the value is the fact
    # and the timestamp is a note about the fact. The estate shows the
    # cost -- 55 devices that reported within two days list their own
    # device battery as silent, which a powered, transmitting unit cannot
    # be. So a value with no change time is kept, just kept separately so
    # nothing that reasons about age has to see a fabricated zero.
    params, untimed = [], []
    for key, entry in (lu.get("chPrams") or {}).items():
        if not isinstance(entry, dict):
            continue
        cdt = entry.get("cdt")
        if cdt:
            params.append(Parameter(key=key, value=entry.get("v"),
                                    changed_at=int(cdt) // 1000))
        elif entry.get("v") not in (None, ""):
            untimed.append(Parameter(key=key, value=entry.get("v"), changed_at=0))

    sensor_change = {}
    for stype, entries in (lu.get("sensors_chDate") or {}).items():
        if isinstance(entries, list) and entries:
            cd = (entries[0] or {}).get("changeDate")
            if cd:
                sensor_change[stype] = int(cd) // 1000

    motion = (lu.get("unit_state") or {}).get("motion") or {}
    state = VehicleState(
        state=str(motion.get("state") or ""),
        since=int(motion["cdt"]) // 1000 if motion.get("cdt") else 0,
    )

    return UnitView(
        unit_id=data.get("id"),
        imei=data.get("i"),
        name=data.get("n"),
        device_model=data.get("device_model"),
        created_at=parse_iso(data.get("crat")),
        last_message=int(dtt) // 1000 if dtt else None,
        server_time=int(dts) // 1000 if dts else None,
        parameters=sorted(params, key=lambda p: -p.changed_at),
        untimed=untimed,
        specs=parse_sensor_specs(payload),
        sensor_change=sensor_change,
        state=state,
    )


def setup_batch(view: UnitView, spread: int = 1800, min_size: int = 3) -> set[str]:
    """Parameters written once at unit setup that never carried real data.

    The payload mixes timezones -- data.crat came back as local time while
    data.sensors[].crat was UTC for the same instant, three hours apart.
    Anchoring on either would mislabel signals on units created elsewhere,
    so the batch is found in the data: the oldest parameters, where several
    share a timestamp within `spread` seconds, are one write rather than a
    coincidence. On the sampled unit thirteen landed inside a minute, 200
    days ago, and never moved since.

    created_at only sanity-checks the result, it never defines it.
    """
    if len(view.parameters) < min_size:
        return set()

    oldest = sorted(view.parameters, key=lambda p: p.changed_at)
    anchor = oldest[0].changed_at
    batch = [p for p in oldest if p.changed_at - anchor <= spread]
    if len(batch) < min_size:
        return set()

    # A young unit has no setup batch to speak of; condemn nothing.
    if view.last_message and (view.last_message - anchor) < 86400:
        return set()

    if view.created_at is not None and abs(view.created_at - anchor) > 15 * 3600:
        return set()      # further apart than any timezone: do not trust it

    return {p.key for p in batch}


def to_definitions(view: UnitView) -> list[SensorDefinition]:
    return [
        SensorDefinition(display_name=s.name, sensor_type=s.type, parameter_key=s.param)
        for s in view.specs
        if s.param
    ]


# --------------------------------------------------------------- adapter

class AfaqyAdapter(MonitorAdapter):
    def __init__(self, token: str, session=None, base_url: str = BASE_URL):
        import requests

        self.token = token
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
        })
        self._views: dict[str, UnitView] = {}
        self._imei_to_unit: dict[str, str] = {}

    def _post(self, path: str, inner: dict) -> dict:
        # the payload is a JSON string inside a `data` field: encoded twice
        resp = self.session.post(
            f"{self.base_url}{path}",
            params={"token": self.token},
            json={"data": json.dumps(inner)},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()

    def view(self, unit_id: str) -> UnitView:
        if unit_id not in self._views:
            raw = self._post("/units/view", {"id": unit_id, "simplify": 1})
            self._views[unit_id] = parse_unit_view(raw)
        return self._views[unit_id]

    # Only the three blocks this tool needs. The API supports thirteen;
    # asking for the rest would drag in driver behaviour, job orders and
    # other customer data we have no business pulling, and would bloat a
    # response that is already megabytes wide.
    FLEET_PROJECTION = ["basic", "last_update", "sensors"]

    def iter_fleet(self, page_size: int = 1000, projection=None):
        """Walk the whole fleet, yielding a UnitView per unit.

        This is the call that changes the shape of the job. `last_update`
        carries chPrams with its cdt stamps and `sensors` carries the
        conversion formulas, so a full estate needs a handful of paginated
        requests rather than one /units/view per device.
        """
        offset = 0
        while True:
            raw = self._post(
                "/v1/units",
                {
                    "offset": offset,
                    "limit": page_size,
                    "simplify": 1,
                    "projection": list(projection or self.FLEET_PROJECTION),
                },
            )

            items = (raw or {}).get("data")
            if isinstance(items, dict):
                items = items.get("items") or items.get("units") or []
            items = items or []

            for unit in items:
                if isinstance(unit, dict):
                    yield parse_unit_view({"data": unit})

            if len(items) < page_size:
                return
            offset += page_size

    def load_fleet(self, page_size: int = 1000) -> dict[str, str]:
        """IMEI -> unit id, built once and reused."""
        for view in self.iter_fleet(page_size=page_size, projection=["basic"]):
            if view.imei and view.unit_id:
                self._imei_to_unit[str(view.imei)] = str(view.unit_id)
        return self._imei_to_unit

    # ------------------------------------------------ MonitorAdapter

    def find_by_imei(self, imei: str) -> str | None:
        if not self._imei_to_unit:
            self.load_fleet()
        return self._imei_to_unit.get(str(imei))

    def parameters(self, unit_id: str) -> tuple[list[Parameter], int]:
        v = self.view(unit_id)
        return v.parameters, v.last_message or 0

    def sensor_definitions(self, unit_id: str) -> list[SensorDefinition]:
        return to_definitions(self.view(unit_id))
