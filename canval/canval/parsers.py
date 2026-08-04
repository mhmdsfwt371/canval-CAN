"""Parsers for the two free-text fields in the CAN catalogue.

Design rule: never drop something silently. Anything the parser does not
recognise is preserved in `unparsed` / `raw` and counted, so a bad pattern
shows up as a number in the report instead of a wrong answer in the UI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------- model name
#
# Observed shapes (order of the bracket groups varies, so match by pattern
# rather than by position):
#
#   DODGE AVENGER [07-14] <VMID: 3, Version: 1>
#   MERCEDES W211 [E-class] (CAN1) <VMID: 12, Version: 1>
#
# Square brackets carry two different things. Two digits mean a year range;
# anything else is a trim or body-class label -- [E-class], [CLK], [GL-class].
# Those matter for search: someone asking for "mercedes e-class" should find
# W211 and W210, which carry the class only in the bracket.
#   DODGE CALIBER [07+] (CAN2) <VMID: 4, Version: 1>
#   FORD GALAXY [06+] (CAN1) {OBD 3+11} <VMID: 5, Version: 1>
#   SHACMAN H3000S (500Kbps) [20+] <VMID: 2888, Version: 1>
#   SHACMAN SX 3315 (CAN1) <VMID: 2234, Version: 1>

# The revision lives inside the model string, next to the VMID. The API's
# own `version` field is NOT this number -- it came back as 1 for a row
# whose name said "Version: 2", so it is fixed and cannot be used to tell
# revisions apart. Read the name.
# Makes written as two words. Splitting on the first space alone would turn
# GREAT WALL POER into the make "GREAT", and a dropdown listing "GREAT" and
# "KING" as manufacturers destroys trust in everything next to it.
TWO_WORD_MAKES = {
    "great wall", "king long", "land rover", "ashok leyland", "bharat benz",
    "golden dragon", "western star", "alfa romeo", "aston martin",
    "range rover", "mercedes benz", "rolls royce", "shaanxi automobile",
    "dong feng", "sino truk", "case ih", "new holland", "massey ferguson",
    "john deere", "iran khodro", "byd auto",
}

_VMID_RE = re.compile(r"<\s*VMID:\s*(\d+)\s*,\s*Version:\s*(\d+)\s*>", re.I)
_YEARS_RE = re.compile(r"\[\s*(\d{2})\s*(?:-\s*(\d{2})|(\+))\s*\]")
_BRACE_RE = re.compile(r"\{([^}]*)\}")
_PAREN_RE = re.compile(r"\(([^)]*)\)")
_LABEL_RE = re.compile(r"\[([^\]]*)\]")
_CANBUS_RE = re.compile(r"^CAN\s*(\d)$", re.I)
_BITRATE_RE = re.compile(r"^(\d+)\s*k(?:bps)?$", re.I)


def _expand_year(two_digit: str) -> int:
    """95 -> 1995, 07 -> 2007. Anything above 80 is treated as 19xx."""
    n = int(two_digit)
    return 1900 + n if n > 80 else 2000 + n


@dataclass
class ParsedModel:
    raw: str
    vmid: int | None = None
    version: int | None = None      # the revision, taken from the name
    name: str = ""
    year_from: int | None = None
    year_to: int | None = None          # None with year_from set means "onwards"
    can_bus: int | None = None          # 1 or 2, when the file names a bus
    bitrate_kbps: int | None = None
    obd_pins: str | None = None
    variant: str | None = None          # trim or body class, e.g. "E-class"
    make: str = ""                      # MAN
    model: str = ""                     # TGX
    extra_notes: list[str] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.vmid is not None and bool(self.name)

    def covers_year(self, year: int | None) -> bool:
        if year is None or self.year_from is None:
            return True                      # no year info: do not exclude
        if year < self.year_from:
            return False
        return self.year_to is None or year <= self.year_to


def parse_model(raw: str) -> ParsedModel:
    out = ParsedModel(raw=raw or "")
    if not raw:
        out.unparsed.append("empty model string")
        return out

    text = raw

    m = _VMID_RE.search(text)
    if m:
        out.vmid, out.version = int(m.group(1)), int(m.group(2))
        text = text[: m.start()] + text[m.end() :]
    else:
        out.unparsed.append("no VMID token")

    m = _YEARS_RE.search(text)
    if m:
        out.year_from = _expand_year(m.group(1))
        out.year_to = _expand_year(m.group(2)) if m.group(2) else None
        text = text[: m.start()] + text[m.end() :]

    m = _BRACE_RE.search(text)
    if m:
        out.obd_pins = m.group(1).strip()
        text = text[: m.start()] + text[m.end() :]

    # whatever is left in square brackets is a trim or class label
    labels = _LABEL_RE.findall(text)
    if labels:
        out.variant = " ".join(x.strip() for x in labels if x.strip()) or None

    for m in list(_PAREN_RE.finditer(text)):
        token = m.group(1).strip()
        bus = _CANBUS_RE.match(token)
        rate = _BITRATE_RE.match(token)
        if bus:
            out.can_bus = int(bus.group(1))
        elif rate:
            out.bitrate_kbps = int(rate.group(1))
        else:
            out.extra_notes.append(token)
    text = _PAREN_RE.sub(" ", text)

    # only braces and angle brackets should be gone by now; square brackets
    # are legitimate name content
    for leftover in re.findall(r"[{}<>]", text):
        out.unparsed.append(f"stray delimiter {leftover!r}")
        break

    out.name = re.sub(r"\s+", " ", text).strip(" -,")
    if not out.name:
        out.unparsed.append("empty name after stripping tokens")

    out.make, out.model = split_make_model(out.name)
    return out


def split_make_model(name: str) -> tuple[str, str]:
    """Separate the manufacturer from the model.

    The catalogue writes them as one string -- "MERCEDES ACTROS MP3" -- but
    a person picking from a list thinks in two steps: the make, then the
    model. Everything after the make is the model, including trim codes,
    because MP3 and MP4 are genuinely different vehicles to a fitter.
    """
    words = (name or "").split()
    if not words:
        return "", ""
    if len(words) >= 2 and " ".join(words[:2]).lower() in TWO_WORD_MAKES:
        return " ".join(words[:2]).upper(), " ".join(words[2:])
    return words[0].upper(), " ".join(words[1:])


# -------------------------------------------------------------- sensor list
#
#   Available sensors: Door Front Left, Engine Cover 2 [Unused],
#   Remote Lock [Superseded by Locked], Fuel Level [%], Ignition

_PREFIX_RE = re.compile(r"^\s*available\s+sensors\s*:\s*", re.I)
_MARKER_RE = re.compile(r"\[([^\]]*)\]")
_SUPERSEDED_RE = re.compile(r"^superseded\s+by\s+(.+)$", re.I)

STATUS_DECLARED = "declared"       # listed and usable
STATUS_UNUSED = "unused"           # listed but explicitly not used
STATUS_SUPERSEDED = "superseded"   # replaced by another signal


@dataclass
class Sensor:
    name: str
    status: str = STATUS_DECLARED
    unit: str | None = None
    superseded_by: str | None = None
    marker: str | None = None      # any marker we did not recognise

    @property
    def usable(self) -> bool:
        return self.status == STATUS_DECLARED


@dataclass
class ParsedSensors:
    raw: str
    sensors: list[Sensor] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)

    @property
    def usable(self) -> list[Sensor]:
        return [s for s in self.sensors if s.usable]


def parse_sensors(raw: str) -> ParsedSensors:
    out = ParsedSensors(raw=raw or "")
    if not raw or not raw.strip():
        return out

    if not _PREFIX_RE.search(raw):
        # some rows may hold real notes instead of a sensor list
        out.unparsed.append("no 'Available sensors:' prefix")
        return out

    body = _PREFIX_RE.sub("", raw).strip()

    for chunk in (c.strip() for c in body.split(",")):
        if not chunk:
            continue

        sensor = Sensor(name=chunk)
        markers = _MARKER_RE.findall(chunk)
        base = _MARKER_RE.sub("", chunk).strip()

        for marker in markers:
            token = marker.strip()
            sup = _SUPERSEDED_RE.match(token)
            if token.lower() == "unused":
                sensor.status = STATUS_UNUSED
            elif sup:
                sensor.status = STATUS_SUPERSEDED
                sensor.superseded_by = sup.group(1).strip()
            elif token in {"%", "l", "L", "km", "KM"} or len(token) <= 3:
                sensor.unit = token
            else:
                sensor.marker = token
                out.unparsed.append(f"unknown marker [{token}] on {base!r}")

        sensor.name = base or chunk
        out.sensors.append(sensor)

    return out
