"""What to say when the catalogue has no file for a vehicle.

"Not found, send a technician" is the wrong answer for a truck or a bus.
The generic protocol files -- J1939 and FMS -- are not models at all; they
are the standard that heavy vehicles implement, and they carry the common
signals on a very wide range of them. Sending someone out without telling
them to try the standard first wastes the trip that this tool exists to
prevent.

So a miss is not one verdict. It is:

    heavy vehicle  -> no dedicated file, but the standard usually works.
                      Try it, and record what came back.
    passenger car  -> the standard does not apply. A trial is genuinely
                      the only way to know.
    unclear        -> say so, and ask rather than guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Heavy-vehicle makes. Not exhaustive, and not meant to be: an unknown make
# lands in "unclear", which asks instead of guessing wrong.
HEAVY_MAKES = {
    # europe
    "scania", "man", "daf", "iveco", "renault trucks", "actros", "atego",
    "axor", "arocs", "antos", "econic", "unimog", "setra", "neoplan",
    "volvo fh", "volvo fm", "volvo fmx", "volvo fl", "volvo fe", "volvo b",
    # asia
    "shacman", "sinotruk", "howo", "faw", "dongfeng", "foton", "jac",
    "hino", "fuso", "isuzu n", "isuzu f", "yutong", "higer", "king long",
    "faw", "howo", "sinotruk",
    "ankai", "zhongtong", "golden dragon", "shaolin", "xcmg", "sany",
    # india / turkey / russia
    "tata", "ashok leyland", "bharat benz", "otokar", "temsa", "bmc",
    "kamaz", "maz", "ural", "actros", "atego", "arocs", "hiace",
    # north america
    "freightliner", "kenworth", "peterbilt", "western star", "mack",
    "international lt", "international hx",
    # agricultural, they speak the same standard
    "fendt", "john deere", "claas", "massey ferguson", "new holland",
    "case ih", "deutz",
}

HEAVY_WORDS = {
    "truck", "lorry", "bus", "coach", "trailer", "tractor", "tipper",
    "mixer", "tanker", "dumper", "prime mover", "semi", "hgv", "heavy",
    "شاحنة", "شاحنه", "تريلا", "أتوبيس", "اتوبيس", "باص", "حافلة", "حافله",
    "قلاب", "خلاطة", "صهريج", "مقطورة", "لوري", "جرار",
}

# Common Arabic spellings of makes, so a query in Arabic still matches.
# Arabic spellings, several per make because nobody agrees on one. Written
# out rather than transliterated on the fly: a wrong guess here classifies a
# truck as a car and withholds the standard-protocol advice, which is the
# one thing that would have saved the trip.
ALIASES = {
    # heavy makes
    "شاكمان": "shacman", "شكمان": "shacman",
    "مرسيدس": "mercedes", "مرسيديس": "mercedes", "أكتروس": "actros",
    "اكتروس": "actros", "اتيجو": "atego", "أتيجو": "atego",
    "فولفو": "volvo", "سكانيا": "scania", "اسكانيا": "scania",
    "مان": "man ", "داف": "daf", "ايفيكو": "iveco", "إيفيكو": "iveco",
    "هينو": "hino", "فوسو": "fuso", "ميتسوبيشي فوسو": "fuso",
    "كاماز": "kamaz", "كاماز": "kamaz",
    "سينوتراك": "sinotruk", "ساينو تراك": "sinotruk",
    "هوو": "howo", "هووو": "howo",
    "دونغفينغ": "dongfeng", "دونغ فينغ": "dongfeng",
    "فوتون": "foton", "فاو": "faw", "جاك": "jac",
    "يوتونغ": "yutong", "هايجر": "higer", "كينج لونج": "king long",
    "تاتا": "tata", "اوتوكار": "otokar", "أوتوكار": "otokar",
    "فريتلاينر": "freightliner", "ماك": "mack",
    # light makes
    "تويوتا": "toyota", "هايس": "hiace", "هايلكس": "hilux",
    "نيسان": "nissan", "ايسوزو": "isuzu", "إيسوزو": "isuzu",
    "هيونداي": "hyundai", "هونداي": "hyundai", "كيا": "kia",
    "فورد": "ford", "شيفروليه": "chevrolet", "بيجو": "peugeot",
    "رينو": "renault ", "فيات": "fiat", "ام جي": "mg ", "إم جي": "mg ",
    "دايو": "daewoo", "ميتسوبيشي": "mitsubishi",
    # components that hint at a heavy vehicle
    "كامينز": "cummins",
}

# Protocol-level entries in the catalogue, as opposed to a model.
_GENERIC_RE = re.compile(r"\b(j1939|fms|obd\s*2|obdii|generic|standard)\b", re.I)

HEAVY = "heavy"
CAR = "car"
UNCLEAR = "unclear"


def normalise(query: str) -> str:
    """Fold Arabic make names to the spelling the catalogue uses."""
    text = " " + (query or "").strip().lower() + " "
    for arabic, latin in ALIASES.items():
        text = text.replace(arabic, latin)
    return re.sub(r"\s+", " ", text).strip()


def classify_vehicle(query: str) -> str:
    text = normalise(query)
    if any(w in text for w in HEAVY_WORDS):
        return HEAVY
    if any(m in text for m in HEAVY_MAKES):
        return HEAVY
    # A make we know is a car maker, with no heavy hint, is a car.
    car_makes = ("toyota", "nissan", "honda", "hyundai", "kia", "ford",
                 "chevrolet", "dodge", "chrysler", "audi", "bmw", "vw",
                 "volkswagen", "peugeot", "renault ", "opel", "skoda",
                 "seat", "mazda", "mitsubishi", "subaru", "lexus", "infiniti")
    if any(m in text for m in car_makes):
        return CAR
    return UNCLEAR


@dataclass
class Fallback:
    verdict: str
    generic_files: list = field(default_factory=list)
    unmapped: list = field(default_factory=list)   # catalogue rows with no VMID
    notes: list = field(default_factory=list)


# A protocol-level entry is one whose NAME is nothing but the protocol.
# "CASE 5130 (J1939)" is a specific tractor that happens to speak J1939 --
# offering it as a fallback for an unrelated truck would be nonsense, and
# an earlier version did exactly that.
_PROTOCOL_ONLY_RE = re.compile(
    r"^\s*(?:j1939|fms|obd\s*2|obdii|generic|standard|ack|can)"
    r"(?:[\s\-_/]+(?:j1939|fms|obd\s*2|obdii|generic|standard|ack|can|"
    r"\d+\s*kbps|v\d+))*\s*$",
    re.I,
)


def is_generic(name: str) -> bool:
    return bool(name) and bool(_PROTOCOL_ONLY_RE.match(name.strip()))


def find_generic_files(conn) -> list:
    """Protocol-level entries: the standard itself, not a vehicle using it."""
    rows = conn.execute(
        """SELECT file_id, vmid, name, raw_model, bitrate_kbps, can_bus,
                  manual_url, raw_notes
           FROM can_files
           WHERE raw_model LIKE '%J1939%' OR raw_model LIKE '%FMS%'
              OR raw_model LIKE '%OBD2%' OR raw_model LIKE '%generic%'
           ORDER BY vmid IS NULL, name"""
    ).fetchall()
    return [r for r in rows if is_generic(r["name"])]


def advise(conn, query: str) -> Fallback:
    kind = classify_vehicle(query)
    generic = find_generic_files(conn)
    unmapped = [r for r in generic if r["vmid"] is None]

    out = Fallback(verdict=kind, generic_files=generic, unmapped=unmapped)

    if kind == HEAVY:
        out.notes.append(
            "Heavy vehicles implement J1939/FMS as standard, so the generic "
            "file is worth trying before anything else."
        )
        out.notes.append(
            "Many fleets need the standard switched on at the dealer, or a "
            "specific pin wired. Confirm that before calling it unsupported."
        )
    elif kind == CAR:
        out.notes.append(
            "J1939/FMS is a heavy-vehicle standard and will not apply here. "
            "A field trial is the only way to settle it."
        )
    else:
        out.notes.append(
            "Vehicle class unclear from the query. If it is a truck, bus or "
            "tractor the generic file is the first thing to try; if it is a "
            "car, it is not."
        )

    if unmapped:
        out.notes.append(
            f"{len(unmapped)} generic entries carry no VMID in the catalogue, "
            "so installs using them cannot be verified automatically. Record "
            "the result of the trial by hand."
        )
    return out


TRIAL_CHECKLIST = [
    "which CAN bus was used, and which pins",
    "the bitrate that worked",
    "which signals came through, and which stayed at zero",
    "whether the vehicle needed the standard enabled at the dealer",
    "the exact model, year and trim from the chassis plate",
]
