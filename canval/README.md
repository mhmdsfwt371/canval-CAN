# canval — CAN file support validation

Answers one question, in two gates:

> A customer asks R&D: *is vehicle X supported?*

**Gate 1 — catalogue.** Is there a CAN file for it, which bus, which pins,
which sensors does it declare? Answered instantly from a local copy of the
XDM catalogue. No device needed.

**Gate 2 — evidence.** Has that file ever been installed, on which IMEIs,
and did those devices actually report? Answered from the reverse index plus
live parameters from the tracking platform.

Gate 1 alone is not an answer. The catalogue says what *should* work; only
gate 2 says what *did*. A file can be present and still fail on a given trim
or build year — and in practice a file built for one model often turns out to
be the one that works on another.

## Setup

```bash
pip install requests
export XDM_CLIENT_ID=...
export XDM_CLIENT_SECRET=...       # keep this out of shell history and git
export XDM_REGION=eu               # or com
```

## Use

```bash
python -m canval.cli hardware                    # find the id for LX45-EA
python -m canval.cli catalogue                   # ~40 calls, run weekly
python -m canval.cli index --hardware 12         # the sweep, see below
python -m canval.cli check "shacman 7300" --year 2021
python -m canval.cli devices --vmid 2888
```

## Why the sweep exists

XDM answers *"which CAN file does device X carry?"* one device at a time:

```
GET /api/external/v3/settingsOverrides/{uid}/overrides
```

There is no endpoint for the reverse question, *"which devices carry file
Y?"* — `devicesSdk/filter` has no vehicle-model field. So the reverse index
has to be swept once and then kept fresh.

Two things keep the cost sane:

* `--hardware` limits the sweep to one hardware version. The full estate is
  ~44k SDK devices; one hardware type is a fraction of that.
* `--active-days` skips devices that have not reported recently. A silent
  device carries no evidence, so it is not worth a call.

Run the sweep nightly. Everything after it is a local query.

## What the parsers guarantee

Nothing is dropped silently. A model string that does not match, or a sensor
marker that is not recognised, lands in `parse_issues` and is counted in the
`catalogue` summary. Check that number before trusting a search:

```sql
SELECT file_id, raw_model, parse_issues FROM can_files
WHERE parse_issues IS NOT NULL;
```

Handled markers in the sensor list, each meaning something different:

| marker                  | meaning                        | counted usable |
|-------------------------|--------------------------------|----------------|
| *(none)*                | declared and usable            | yes            |
| `[Unused]`              | listed but not actually used   | no             |
| `[Superseded by X]`     | replaced by another signal     | no, points to X|
| `[%]`, `[L]`            | unit, part of the reading      | yes            |

## The remaining blocker

`monitor.py` defines `MonitorAdapter` — three methods:

```python
find_by_imei(imei)          -> unit id
parameters(unit_id)         -> ([Parameter(key, value, updated_at)], last_activity)
sensor_definitions(unit_id) -> [SensorDefinition(display_name, type, parameter_key)]
```

`NotConfigured` is wired in as a placeholder, so `check` runs today and
returns gate 1 plus the install list, and reports gate 2 as unconfigured.

**The per-parameter timestamp is the critical field.** Without it, a sensor
that is declared but dead looks exactly like a working one. Age is measured
against the device's own last report, not the wall clock, so a truck parked
for a week is not mistaken for a broken sensor.

## Decoder table

Raw CAN counters are scaled integers. The factors in `monitor.DECODERS` were
each derived by comparing a raw signal param against the value the platform
displayed for the same device at the same moment:

| key             | raw      | factor  | result   | reading            |
|-----------------|----------|---------|----------|--------------------|
| `sensor_16386`  | 66097    | ×0.05   | 3304.85  | engine hours       |
| `sensor_16385`  | 43651    | ×0.5    | 21825.5  | total fuel         |
| `sensor_16387`  | 11694080 | ×0.005  | 58470.4  | odometer km        |
| `sensor_8200`   | 128      | −40     | 88       | engine temp °C     |
| `sensor_12300`  | 8199     | ×0.125  | 1024.9   | engine rpm         |

These match the usual J1939 resolutions, which is why they are worth
generalising — but verify any new key against a live device before adding it,
and note the check in `verified_on`.

## Verdicts across devices

One device is weak evidence. `aggregate()` rolls several installs of the same
file together:

* **confirmed** — alive on most devices sampled
* **partial** — alive on some only. Usually a trim or year split inside what
  the catalogue treats as one entry; a signal that the row should be split.
