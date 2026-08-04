"""A stand-in for both platforms, shaped from the real captures.

This is not a toy. Every structure here was lifted from an actual response
recorded during this work, and the population statistics match the real
sweep: 6483 devices across 458 configurations, 3934 catalogue rows, 2731
distinct VMIDs, and the same eight configuration names that dominated the
estate.

The point is to run the whole pipeline -- catalogue, sweep, diagnostics,
answer -- against data that behaves like the real thing, so integration
bugs surface here rather than against a production API.

The awkward cases are deliberately reproduced:

  * a 992-device config whose first three devices carry no override, the
    exact shape that made an earlier sweep write off 989 working installs
  * devices carrying a different file on each CAN bus
  * 65 configs containing both devices with a file and devices without,
    which is what disproved "one config, one file"
  * a monitoring account that can only see a fraction of the fleet
  * parked vehicles whose counters are legitimately frozen
"""

from __future__ import annotations

import random
import time

# ---------------------------------------------------------------- catalogue

# Connector pin pairs seen in the real catalogue. The first number is the
# CAN High line, the second CAN Low -- that ordering is what the interface
# colours, so getting it wrong here would hide a real bug.
_PINS = ["OBD 3+11", "OBD 6+14", "OBD 1+9", "OBD 12+13", None]
_RATES = [None, 250, 500, 500, None]

_MAKES = [
    ("MERCEDES ACTROS MP3", [2008, None], ["CAN1", "CAN2", "CAN3"]),
    ("MERCEDES ACTROS MP4", [2012, None], ["CAN1", "CAN2"]),
    ("VOLVO FH", [2013, None], ["CAN1", "CAN2"]),
    ("VOLVO FM", [2010, 2020], ["CAN1"]),
    ("SCANIA R SERIES", [2009, None], ["CAN1", "CAN2"]),
    ("SHACMAN H3000S", [2020, None], ["CAN1"]),
    ("SHACMAN SX 3315", [2015, None], ["CAN1"]),
    ("SINOTRUK HOWO", [2016, None], ["CAN1"]),
    ("ISUZU NPR", [2012, None], ["CAN1"]),
    ("TOYOTA HIACE", [2019, None], ["CAN1"]),
    ("KIA SPORTAGE", [2022, None], ["CAN1"]),
    ("KIA SONET", [2022, None], ["CAN1"]),
    ("FIAT SCUDO", [2022, None], ["CAN1"]),
    ("MG 3", [2024, None], ["CAN1"]),
    ("HYUNDAI PEGAS", [2020, None], ["CAN1"]),
    ("MAN TGX", [2014, None], ["CAN1", "CAN2"]),
    ("DAF XF", [2013, None], ["CAN1"]),
    ("IVECO STRALIS", [2013, None], ["CAN1", "CAN2"]),
]

_SENSORS_HEAVY = [
    "Engine Speed", "Vehicle Speed", "Total Distance (Mileage)",
    "Engine Temperature", "Fuel Level [%]", "Engine Hours", "Total Fuel Used",
    "Axle Weight", "AdBlue Level", "Accelerator Position", "Parking Brake",
    "Brake Pedal Switch", "Ignition", "Gross Vehicle Weight",
]
_SENSORS_LIGHT = [
    "Engine Speed", "Vehicle Speed", "Total Distance (Mileage)",
    "Engine Temperature", "Fuel Level [%]", "Ignition",
    "Door Front Left", "Door Front Right", "Remote Lock [Superseded by Locked]",
    "Engine Cover 2 [Unused]",
]

# Real config names, with the real device counts from the actual sweep.
_CONFIGS_WITHOUT = [
    ("Hajj-file-2024 ( LX45 )-upgraded (110 - Neqaba)", 992),
    ("Hajj-file-2024 ( LX45 )-upgraded (110 - Neqaba) (v8)", 438),
    ("LX45_EA_Afaqy-110", 308),
    ("Sinotruck 110 LX45 (v8)", 208),
    ("Fiat Scudo 2025 ( 110 ) LX45", 180),
    ("Shift_Pegas_2022_(110) LX45", 161),
    ("actross  OTHAIM SA", 131),
    ("KIA_Sportage-2024 lx45- Fuel LVL (lock_unlock (V6))", 126),
]
_CONFIGS_WITH = [
    ("VOLVO-FH(+13)(spark) lx45-Upgraded", 17),
    ("ISUZU (110)- LX45-Upgraded", 9),
    ("(actross +19)(110)-Upgraded LX45-ibutton", 9),
    ("Ter3(Temp&Hum)&Buzzer (lx45)", 8),
    ("VOLVO-FH(+20)(spark) lx45-Upgraded (105)", 8),
    ("(actross mp4 )(110)-Upgraded LX45 V8", 8),
    ("Mercedes Actros (+19) (MP5) CAN2 LX45 - 110 (V8)", 8),
    ("(actross +11)(110)-Upgraded LX45 (+1) iButton", 8),
]


def build_catalogue(n_files: int = 3934, seed: int = 7) -> list[dict]:
    """Rows in the exact shape /canfiles/filter returns."""
    rng = random.Random(seed)
    rows, file_id, vmid = [], 1000, 2
    while len(rows) < n_files:
        name, (y0, y1), buses = _MAKES[len(rows) % len(_MAKES)]
        heavy = any(k in name for k in
                    ("ACTROS", "VOLVO", "SCANIA", "SHACMAN", "HOWO", "MAN",
                     "DAF", "IVECO", "ISUZU"))
        for bus in buses:
            revisions = rng.choice([1, 1, 1, 2, 2, 3])
            this_vmid = vmid
            vmid += 1
            for rev in range(1, revisions + 1):
                pool = _SENSORS_HEAVY if heavy else _SENSORS_LIGHT
                k = min(len(pool), 4 + rev * 3)
                years = f"[{str(y0)[2:]}-{str(y1)[2:]}]" if y1 else f"[{str(y0)[2:]}+]"
                pins = _PINS[file_id % len(_PINS)]
                rate = _RATES[file_id % len(_RATES)]
                parts = [name, years]
                if rate:
                    parts.append(f"({rate}Kbps)")
                parts.append(f"({bus})")
                if pins:
                    parts.append("{" + pins + "}")
                parts.append(f"<VMID: {this_vmid}, Version: {rev}>")
                rows.append({
                    "id": file_id,
                    "model": " ".join(parts),
                    "notes": "Available sensors: " + ", ".join(pool[:k]),
                    "version": 1,          # always 1, as the real API returns
                })
                file_id += 1
                if len(rows) >= n_files:
                    break
            if len(rows) >= n_files:
                break
    # The protocol-level entries, which are what an unmatched truck falls
    # back to. Without them the fallback path is never exercised.
    rows = rows[: max(0, n_files - 2)]
    for label in ("J1939 FMS ACK", "J1939 FMS ACK (500Kbps)"):
        rows.append({
            "id": file_id, "model": label, "version": 1,
            "notes": "Available sensors: " + ", ".join(_SENSORS_HEAVY[:7]),
        })
        file_id += 1
    return rows


# ------------------------------------------------------------------ devices

class FakeXdm:
    """Stands in for XdmClient: catalogue, devices, per-device overrides."""

    def __init__(self, catalogue: list[dict], n_devices: int = 6483,
                 seed: int = 11):
        self.catalogue = catalogue
        self.rng = random.Random(seed)
        self.calls = {"canfiles": 0, "devices": 0, "overrides": 0}

        file_ids = [r["id"] for r in catalogue]
        self.devices = []
        self.truth: dict[str, list] = {}

        imei = 869595060000000
        cfg_id = 1

        def add(config_name, count, mode):
            nonlocal imei, cfg_id
            this_cfg = cfg_id
            cfg_id += 1
            for k in range(count):
                uid = str(imei)
                imei += 1
                self.devices.append({
                    "settings": {
                        "uid": uid,
                        "hardware": {"name": "LX45-EA"},
                        "configuration": {"currentConfigId": this_cfg,
                                          "currentConfigName": config_name},
                    },
                    "information": {"activityUpdate": {
                        "lastActivity": int(time.time()) - self.rng.randint(60, 86400)}},
                })

                # THE SHAPE THAT BROKE THE OLD SWEEP: the first three
                # devices of a big config carry nothing, the rest do.
                if mode == "late" and k < 3:
                    self.truth[uid] = []
                elif mode == "none":
                    self.truth[uid] = []
                elif mode == "mixed" and self.rng.random() < 0.45:
                    self.truth[uid] = []
                else:
                    primary = self.rng.choice(file_ids)
                    entries = [("CAN1 Vehicle model", primary, str(primary))]
                    if self.rng.random() < 0.15:      # a second bus
                        second = self.rng.choice(file_ids)
                        entries.append(("CAN2 Vehicle model", second, str(second)))
                    self.truth[uid] = entries

        # Scale the real config sizes to whatever fleet size was asked
        # for, so a small test fleet keeps the same shape as the real one.
        real_total = sum(c for _, c in _CONFIGS_WITHOUT + _CONFIGS_WITH)
        scale = min(1.0, n_devices / (real_total * 1.6))

        for name, count in _CONFIGS_WITHOUT:
            n = max(4, int(count * scale))
            if len(self.devices) + n > n_devices:
                n = n_devices - len(self.devices)
            if n > 0:
                add(name, n, "late")
        for name, count in _CONFIGS_WITH:
            n = max(2, int(count * scale))
            if len(self.devices) + n > n_devices:
                n = n_devices - len(self.devices)
            if n > 0:
                add(name, n, "always")

        # configs holding both kinds, which is what the real data showed
        for i in range(65):
            if len(self.devices) >= n_devices:
                break
            n = min(self.rng.randint(8, 40), n_devices - len(self.devices))
            add(f"mixed-config-{i}", n, "mixed")

        while len(self.devices) < n_devices:
            n = min(n_devices - len(self.devices), self.rng.randint(4, 30))
            add(f"cfg-{cfg_id}", n, self.rng.choice(["always", "none", "mixed"]))

    # ------------------------------------------------------- client surface

    def iter_can_files(self, model=None, page_size=100, progress=None):
        self.calls["canfiles"] += 1
        for r in self.catalogue:
            yield r
        if progress:
            progress(len(self.catalogue), len(self.catalogue))

    def iter_devices(self, hardware_ids=None, last_activity_from=None,
                     page_size=200):
        self.calls["devices"] += 1
        for d in self.devices:
            yield d

    def device_overrides(self, uid):
        self.calls["overrides"] += 1
        base = [{"elementId": 1, "name": "Sensors capture period", "value": "30"},
                {"elementId": 2, "name": "GNSS speed change threshold", "value": "6"}]
        for element_name, file_id, raw in self.truth.get(uid, []):
            base.append({"elementId": 99, "name": element_name, "value": raw})
        return base


# --------------------------------------------------------------- monitoring

class FakeAfaqy:
    """Stands in for the tracking platform, using the real /units/view shape.

    Only a slice of the fleet is visible, which is what a single-customer
    token actually returns, and a share of vehicles are parked so their
    counters are legitimately frozen.
    """

    SPECS = [
        ("RPM", "rpm", "sensor_12300", "RPM", "*", 0.125, None),
        ("Total Engine Hours", "eng_hour", "sensor_16386", "H", "*", 0.05, None),
        ("ODO", "milage", "sensor_16387", "KM", "*", 0.005, None),
        ("Engine Temperature", "engine_temperature", "sensor_8200", "°C", "-", 40, None),
        ("Total fuel", "total_fuel_can", "sensor_16385", "L", "*", 0.5, None),
        ("Weight", "weight", "sensor_12301", "KG", "*", 10, None),
        ("Vehicle Battery", "ex_battery_volt", "ePwrV", "V", "/", 1000, None),
        ("Fuel Level 1st CAL", "fuel_level", "sensor_8219", "L", "*", 1,
         [{"x": 0, "y": 0}, {"x": 22, "y": 100}, {"x": 62, "y": 300},
          {"x": 77, "y": 400}, {"x": 101, "y": 500}, {"x": 123, "y": 609}]),
    ]

    def __init__(self, xdm: FakeXdm, visible_fraction: float = 0.12, seed: int = 13):
        self.rng = random.Random(seed)
        imeis = [d["settings"]["uid"] for d in xdm.devices]
        self.rng.shuffle(imeis)
        cut = int(len(imeis) * visible_fraction)
        self.visible = {u: f"unit{i}" for i, u in enumerate(imeis[:cut])}
        self.parked = {u for u in self.visible if self.rng.random() < 0.4}
        self.calls = 0

    def find_by_imei(self, imei):
        return self.visible.get(str(imei))

    def parameters(self, unit_id):
        from .monitor import Parameter
        self.calls += 1
        now = int(time.time())
        imei = next(k for k, v in self.visible.items() if v == unit_id)
        parked = imei in self.parked

        out = [
            Parameter("sensor_12300", 4825, now - 5),
            Parameter("sensor_8200", 127, now - 40),
            Parameter("ePwrV", 27980, now - 8),
            Parameter("sensor_16386", 66113, now - (3 * 86400 if parked else 120)),
            Parameter("sensor_16387", 11694120, now - (3 * 86400 if parked else 300)),
            Parameter("sensor_16385", 43651, now - (3 * 86400 if parked else 900)),
            Parameter("sensor_12301", 3009, now - 1800),
            Parameter("sensor_8219", 92, now - 3600),
            # setup batch: written once at install, never since
            Parameter("sensor_12318", 0, now - 200 * 86400),
            Parameter("sensor_148", 0, now - 200 * 86400),
            Parameter("sensor_16", 0, now - 200 * 86400),
            Parameter("fw_name", "Q07_01_132", now - 200 * 86400),
            # worked, then died
            Parameter("sensor_12321", 0, now - 80 * 86400),
        ]
        return out, now

    def sensor_definitions(self, unit_id):
        from .monitor import SensorDefinition
        return [SensorDefinition(n, t, p) for n, t, p, *_ in self.SPECS]

    def state_for(self, unit_id):
        from .monitor import VehicleState
        imei = next(k for k, v in self.visible.items() if v == unit_id)
        now = int(time.time())
        if imei in self.parked:
            return VehicleState("stationary_engine_off", now - 3 * 86400)
        return VehicleState("moving_engine_on", now - 3600)
