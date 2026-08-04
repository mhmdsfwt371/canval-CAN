"""Sensor names in the language the customer speaks.

The catalogue calls a thing "Total Distance (Mileage)". A fleet manager
calls it the odometer. Sales sit between the two, and if the tool hands
them the catalogue's wording they will paste it into a quote and the
customer will not know what they are buying.

WHY THIS IS A TABLE AND NOT A GUESS
-----------------------------------
Machine translation of a term like "Retarder" or "PTO state" produces
something confidently wrong. These are trade terms with settled Arabic
equivalents in the region, so they are written out once, by hand, and
anything not in the table is shown in English rather than translated on
the fly. An English word in an Arabic sentence is a small friction; a
wrong Arabic word in a customer quote is a lost deal.

Anything missing here shows up in `untranslated()` so the list can be
extended from real use rather than guessed at up front.
"""

from __future__ import annotations

import re

# Catalogue name -> what a customer would call it.
AR: dict[str, str] = {
    # drivetrain
    "engine speed": "لفات المحرك",
    "vehicle speed": "سرعة المركبة",
    "engine temperature": "حرارة المحرك",
    "engine hours": "ساعات تشغيل المحرك",
    "total engine hours": "ساعات تشغيل المحرك",
    "engine load": "حمل المحرك",
    "engine percent torque": "نسبة عزم المحرك",
    "engine working": "حالة تشغيل المحرك",
    "accelerator position": "موضع دواسة البنزين",
    "cruise control": "مثبت السرعة",
    "cruise control state": "حالة مثبت السرعة",
    "retarder auto": "الفرامل المساعدة",
    "retarder selection": "اختيار الفرامل المساعدة",
    "retarder percent torque": "نسبة الفرامل المساعدة",
    "retarder torque mode": "وضع الفرامل المساعدة",
    "pto state": "حالة مأخذ الحركة",

    # distance and fuel
    "total distance (mileage)": "عداد المسافة",
    "total distance": "عداد المسافة",
    "total distance high resolution": "عداد المسافة",
    "fuel level": "مستوى الوقود",
    "fuel level 2": "مستوى الوقود (الخزان الثاني)",
    "fuel consumption": "استهلاك الوقود",
    "total fuel used": "إجمالي الوقود المستهلك",
    "total fuel used high resolution": "إجمالي الوقود المستهلك",
    "fuel rate": "معدل استهلاك الوقود",
    "instant fuel economy": "الاستهلاك اللحظي",
    "adblue level": "مستوى محلول الأدبلو",

    # weight
    "axle weight": "حمولة المحور",
    "gross vehicle weight": "الوزن الإجمالي للمركبة",
    "cargo weight": "وزن الحمولة",
    "trailer weight": "وزن المقطورة",
    "weight": "الوزن",

    # state
    "ignition": "الكونتاكت",
    "acc": "الكونتاكت",
    "parking brake": "فرامل اليد",
    "brake pedal switch": "دواسة الفرامل",
    "clutch pedal switch": "دواسة الكلتش",
    "locked": "القفل المركزي",
    "remote lock": "القفل عن بُعد",
    "remote trunk open": "فتح الشنطة عن بُعد",

    # doors and covers
    "door front left": "الباب الأمامي الأيسر",
    "door front right": "الباب الأمامي الأيمن",
    "door rear left": "الباب الخلفي الأيسر",
    "door rear right": "الباب الخلفي الأيمن",
    "trunk cover": "غطاء الشنطة",
    "engine cover": "غطاء المحرك",
    "engine cover 2": "غطاء المحرك",

    # lamps and warnings
    "headlight indicator": "إشارة الأنوار الأمامية",
    "high beam lamp indicator": "إشارة النور العالي",
    "tail light indicator": "إشارة الأنوار الخلفية",
    "check engine warning": "لمبة فحص المحرك",
    "abs warning": "تحذير نظام الفرامل",
    "esp warning": "تحذير نظام الثبات",
    "brake system warning": "تحذير نظام الفرامل",
    "air bag warning": "تحذير الوسادة الهوائية",
    "oil pressure warning": "تحذير ضغط الزيت",
    "reserved warning": "تحذير احتياطي",
    "tell tale status": "حالة اللمبات التحذيرية",
    "diagnostic trouble codes": "أكواد الأعطال",

    # belts and cabin
    "driver seat belt indicator": "حزام أمان السائق",
    "passenger seat belt indicator": "حزام أمان الراكب",
    "interior temperature": "حرارة المقصورة",
    "ambient temperature": "حرارة الجو",
    "ambient temperature cargo": "حرارة صندوق البضاعة",
    "humidity": "الرطوبة",
    "barometric pressure": "الضغط الجوي",
    "webasto": "مدفأة ويباستو",

    # service
    "service distance": "المسافة المتبقية للصيانة",
    "service delay (operational time based)": "الوقت المتبقي للصيانة",
    "transmission oil life remaining": "عمر زيت الجير المتبقي",
    "clutch life remaining": "عمر الكلتش المتبقي",
    "washer fluid level": "مستوى ماء المساحات",
    "dpf 1 soot load": "امتلاء فلتر العادم",
    "dpf 2 soot load": "امتلاء فلتر العادم",
    "dpf 1 ash load": "رماد فلتر العادم",
    "dpf 2 ash load": "رماد فلتر العادم",
}

# Grouped for a customer-facing list: the order sales would read them out.
GROUPS = [
    ("التشغيل والحركة", ["لفات المحرك", "سرعة المركبة", "عداد المسافة",
                          "ساعات تشغيل المحرك", "الكونتاكت", "حالة تشغيل المحرك"]),
    ("الوقود", ["مستوى الوقود", "مستوى الوقود (الخزان الثاني)", "استهلاك الوقود",
                 "إجمالي الوقود المستهلك", "معدل استهلاك الوقود",
                 "الاستهلاك اللحظي", "مستوى محلول الأدبلو"]),
    ("الأحمال", ["الوزن", "حمولة المحور", "الوزن الإجمالي للمركبة",
                  "وزن الحمولة", "وزن المقطورة"]),
    ("حالة المركبة", ["حرارة المحرك", "حمل المحرك", "فرامل اليد",
                       "دواسة الفرامل", "مثبت السرعة", "أكواد الأعطال"]),
]

_STRIP = re.compile(r"\s*\((?:j1939|obd\d*)\)\s*|\*|\[|\]|%", re.I)


def to_arabic(name: str) -> tuple[str, bool]:
    """Customer-facing name, and whether a translation was actually found.

    The flag matters: an untranslated term is shown in English on purpose,
    not passed off as Arabic.
    """
    if not name:
        return "", False
    key = _STRIP.sub(" ", str(name)).strip().lower()
    key = re.sub(r"\s+", " ", key)
    if key in AR:
        return AR[key], True
    # "Fuel Level [%] (J1939)" and "Fuel Level" are the same thing
    bare = re.sub(r"\s*\(.*?\)\s*", " ", key).strip()
    bare = re.sub(r"\s+", " ", bare)
    if bare in AR:
        return AR[bare], True
    return str(name), False


def translate_all(names) -> list[dict]:
    out = []
    seen = set()
    for n in names or []:
        ar, ok = to_arabic(n)
        if ar in seen:          # several catalogue names map to one term
            continue
        seen.add(ar)
        out.append({"ar": ar, "en": n, "translated": ok})
    return out


def untranslated(names) -> list[str]:
    """Terms still showing in English, so the table can be grown from use."""
    return sorted({n for n in (names or []) if not to_arabic(n)[1]})
