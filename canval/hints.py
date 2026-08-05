"""What the configuration name says about the vehicle.

Installers name configurations after the truck they are fitting:

    (actross mp4 )(110)-Upgraded LX45 V8
    Stcan-4G-SCANIA-Upgraded (105)
    VOLVO-FH(+20)(spark) lx45-Upgraded

Nothing reads those names today. They are typed by hand, they are
inconsistent, and the store module says outright that they are far too
loose to index on. That judgement is right and this module does not
overturn it.

What it does instead is treat the name as a *lead*. The device tells us
which CAN file is loaded; the name tells us what the installer thought
they were fitting. Where the two disagree, there is something worth a
human look, and three kinds of disagreement are worth money:

    UPGRADE     the device runs a generic J1939/FMS file, but the name
                names a make we have a dedicated file for. The generic
                standard works broadly and reports less; a dedicated file
                on the same truck usually reports more. Nobody has to
                visit the vehicle to find out.

    CANDIDATE   the device has a CAN port with nothing assigned, and the
                name points at a vehicle we have a file for. This is the
                shortest path from "not fitted" to "fitted" in the whole
                estate.

    MISMATCH    the device runs a dedicated file for one make while the
                name says another. Usually a renamed configuration, but
                occasionally a genuinely wrong install, and those are
                expensive to find any other way.

EVIDENCE, NOT VERDICTS
----------------------
Every row carries the configuration name it was derived from and the word
that matched. A hint is never written into device_can and never counted
as an installation: it lives in its own table so that no query answering
a customer can pick it up by accident. The name may be years out of date
-- a vehicle can be replaced without anyone editing the text -- so the
output is a worklist for a person, not an answer for a customer.

WHY THE MATCHING IS DELIBERATELY STRICT
---------------------------------------
The first version let the phonetic matcher loose on every word in the
name, and it produced confident nonsense at scale:

    "shift"        -> SCHAFFER          "2022"    -> JOHN DEERE
    "odometer"     -> TEST              "dual"    -> IVECO
    "connectivity" -> FORD              "500kbps" -> FIAT

Dropping vowels leaves short words as three or four consonants, and at
that length unrelated words collide constantly. No stoplist fixes this:
it would have to contain every word an installer might ever type, and it
would always be one word behind.

So phonetic matching is not applied to free text at all. A configuration
name has to contain the make or model as a word, spelt as a word --
"scania", "sinotruck", "mercedes". The one concession is a short list of
spellings this fleet actually uses for vehicles it actually runs, which
is a small, checkable, finite thing in a way that "any word that sounds
similar" is not.

That trades some recall for precision, and it is the right trade. A
missed lead costs nothing; a wrong lead sends somebody to change a
working installation, and a worklist that is wrong a third of the time
gets ignored entirely -- which costs all the right leads too.

MAKES BEAT MODELS, AND AMBIGUOUS MODELS COUNT FOR NOTHING
---------------------------------------------------------
Model names are not unique across manufacturers, and matching on one
produced a second round of confident nonsense:

    "cruiser" -> CHRYSLER   from "Toyota Land Cruiser Prado110"
    "tracker" -> CHEVROLET  from "iveco Tracker_110-Upgraded"
    "corolla" -> TOYOTA     from "(TOYOTA HIACE)(110)-LX45-EA"

The first two are the same failure: the catalogue holds that model word
under more than one make, and picking either is a coin toss. The third is
worse -- the name says HIACE, the tool answered COROLLA, and both are
Toyotas, so the make check could not catch it.

Two rules follow. A manufacturer name anywhere in the string wins over
any model name, because "Toyota Land Cruiser" is a Toyota whichever word
is longer. And a model word is only usable when the whole catalogue maps
it to exactly one make -- an ambiguous model resolves nothing and is
dropped rather than guessed.

The stoplist stays for the words that survive all that: hardware family
names like "stcan", protocol words like "j1939", and the accessory names
that show up in nearly every configuration.

    python -m canval.hints              build and show the worklists
    python -m canval.hints --limit 40   longer lists
"""

from __future__ import annotations

import argparse
import os
import re

from .fallback import is_generic
from .matching import fold
from .store import connect

# Words that appear in configuration names and are never a vehicle. Some
# are here because they are noise; the first group is here because it
# actively misleads the phonetic matcher.
STOPWORDS = {
    # hardware families -- "stcan" is one edit from "scania"
    "stcan", "xtcan", "lx45", "lx44", "lx43", "lx42", "lx41", "tacho",
    "light", "castanet", "marimba", "viola", "sitar", "tambourine",
    "melodeon", "harmonium", "concertina", "khlui", "kontra", "tonette",
    "harpsichord",
    # protocol and bus words
    "can", "can1", "can2", "canbus", "j1939", "fms", "obd", "obd2", "obdii",
    "generic", "standard", "ack", "kbps",
    # network and firmware
    "2g", "3g", "4g", "lte", "roaming", "sim", "apn", "zain", "stc", "mobily",
    "firmware", "upgraded", "upgrade", "update", "updated", "default",
    # features and accessories
    "ibutton", "button", "wiegand", "buzzer", "temp", "hum", "humidity",
    "spark", "weight", "fuel", "lvl", "level", "lock", "unlock", "relay",
    "sensor", "sensors", "immobilizer", "rfid", "ble", "bluetooth", "uhf",
    "gnss", "gps", "tag", "trail", "wireless", "camera", "dvr", "panic",
    # organisational noise
    "afaqy", "test", "testing", "demo", "office", "trial", "new", "old",
    "copy", "final", "backup", "temp1", "hajj", "neqaba", "file", "config",
    "configuration", "version", "tech", "muti", "multi", "blue", "red",
    "green", "black", "white",
    # Model names that are also ordinary English words. Each one is here
    # because it produced a false lead on real data: "Panda Co Use This"
    # is a company, "Asia Star" is a bus body builder, neither is a Fiat
    # or a Mitsubishi. Uniqueness in the catalogue does not make a word
    # safe -- being a common word does.
    "panda", "star", "bravo", "spark", "city", "focus", "civic", "accord",
    "note", "cube", "jazz", "fit", "van", "pride", "eagle", "tiger",
    "lion", "sunny", "smart", "grand", "prime", "power", "pro", "max",
    "plus", "mini", "one", "two", "gas", "eco", "sport", "classic",
}

# Values found in the catalogue's make column that are not manufacturers.
NOT_A_MAKE = {"j1939", "fms", "obd", "obd2", "obdii", "test", "generic",
              "standard", "can", "ack", "blue", "unknown", "none", "na"}

# Spellings this fleet actually writes, mapped to the catalogue's word.
# Deliberately a short, checkable list rather than a phonetic guess: every
# entry here was read off a real configuration name.
ALIASES = {
    "actross": "actros", "actros": "actros", "aktros": "actros",
    "sinotruck": "sinotruk", "sinotruk": "sinotruk", "synotruck": "sinotruk",
    "shakman": "shacman", "shacman": "shacman", "shackman": "shacman",
    "mercedes": "mercedes", "merecedes": "mercedes", "mercedez": "mercedes",
    "volvo": "volvo", "scania": "scania", "iveco": "iveco",
    "hyundai": "hyundai", "hunday": "hyundai", "hyunda": "hyundai",
    "toyota": "toyota", "nissan": "nissan", "mitsubishi": "mitsubishi",
    "kinglong": "king long", "dongfeng": "dongfeng", "foton": "foton",
    "isuzu": "isuzu", "hino": "hino", "daewoo": "daewoo", "chevrolet":
    "chevrolet", "changan": "changan", "peugeot": "peugeot", "renault":
    "renault", "hiace": "hiace", "hilux": "hilux", "elantra": "elantra",
    "accent": "accent", "cerato": "cerato", "sportage": "sportage",
    "sonet": "sonet", "pegas": "pegas", "optima": "optima", "corolla":
    "corolla", "fortuner": "fortuner", "prado": "prado", "sunny": "sunny",
    "urvan": "urvan", "staria": "staria", "tugella": "tugella",
}

UPGRADE = "upgrade"
CANDIDATE = "candidate"
MISMATCH = "mismatch"

SCHEMA = """
CREATE TABLE IF NOT EXISTS config_hints (
    imei         TEXT NOT NULL,
    kind         TEXT NOT NULL,      -- upgrade | candidate | mismatch
    hinted_make  TEXT,
    hinted_model TEXT,
    matched_word TEXT,               -- the word in the name that matched
    evidence     TEXT,               -- the configuration name, verbatim
    current_file INTEGER,
    current_name TEXT,
    built_at     TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (imei, kind)
);
CREATE INDEX IF NOT EXISTS ix_hint_kind ON config_hints(kind);
CREATE INDEX IF NOT EXISTS ix_hint_make ON config_hints(hinted_make);
"""

_TOKEN = re.compile(r"[a-z0-9\u0600-\u06FF]+")


def catalogue_terms(conn) -> dict:
    """Make and model words worth recognising, with what they resolve to.

    Only from files that name a real vehicle. A term drawn from a
    protocol-level entry would match its own generic file and report every
    truck in the fleet as an upgrade opportunity over itself.
    """
    makes: dict[str, str] = {}
    models: dict[str, set] = {}
    model_rows: dict[str, set] = {}
    for row in conn.execute(
            """SELECT DISTINCT make, model, name FROM can_files
                WHERE make IS NOT NULL AND make != ''"""):
        if is_generic(row["name"]):
            continue
        make = row["make"].strip()
        # The catalogue's `make` column is parsed from free text and holds
        # entries that are not manufacturers at all -- J1939, OBD, TEST.
        # Left in, every heavy vehicle on the standard protocol reads as
        # "running a file for a different make", which is the opposite of
        # true: it is running the standard, on purpose.
        if fold(make) in NOT_A_MAKE or len(fold(make)) < 3:
            continue
        for token in _TOKEN.findall(fold(make)):
            if len(token) >= 4 and token not in STOPWORDS:
                makes[token] = make
        for token in _TOKEN.findall(fold(row["model"] or "")):
            if len(token) >= 4 and token not in STOPWORDS:
                models.setdefault(token, set()).add(make)
                model_rows.setdefault(token, set()).add(
                    (row["model"] or "").strip())

    terms: dict[str, dict] = {}
    # Models first, so that a word which is also a manufacturer name is
    # overwritten by the manufacturer reading below.
    for token, owners in models.items():
        if len(owners) != 1:
            continue        # the same model word under two makes proves nothing
        make = next(iter(owners))
        terms[token] = {"make": make, "models": model_rows[token],
                        "kind": "model"}
    for token, make in makes.items():
        terms[token] = {"make": make, "models": set(), "kind": "make"}
    return terms


def hint_from_config(config_name: str, terms: dict):
    """(make, model, matched word) the name points at, or None.

    A word counts only if it *is* a catalogue make or model, or a known
    spelling of one. Longest word first, so "actross mp4" resolves on
    "actross" rather than on a shorter coincidence elsewhere.
    """
    if not config_name:
        return None
    words = sorted({w for w in _TOKEN.findall(fold(config_name))
                    if len(w) >= 4 and w not in STOPWORDS},
                   key=len, reverse=True)
    fallback = None
    for word in words:
        hit = terms.get(ALIASES.get(word, word))
        if not hit:
            continue
        model = sorted(hit["models"])[0] if len(hit["models"]) == 1 else None
        if hit["kind"] == "make":
            return hit["make"], model, word      # a manufacturer settles it
        if fallback is None:
            fallback = (hit["make"], model, word)
    return fallback


def build_hints(conn) -> dict:
    """Compare every device's loaded file against what its name suggests."""
    conn.executescript(SCHEMA)
    conn.execute("DELETE FROM config_hints")

    terms = catalogue_terms(conn)
    stats = {"catalogue_terms": len(terms), "devices_examined": 0,
             "named": 0, UPGRADE: 0, CANDIDATE: 0, MISMATCH: 0}

    rows = conn.execute(
        """SELECT d.imei, d.config_name, d.file_id, d.element_name,
                  f.name AS file_name, f.make AS file_make
             FROM device_can d
             LEFT JOIN can_files f ON f.file_id = d.file_id
            GROUP BY d.imei""").fetchall()

    # One lookup per distinct configuration name, not per device: 25000
    # devices share a few thousand names, and the phonetic pass is the
    # expensive part.
    resolved: dict = {}
    for row in rows:
        stats["devices_examined"] += 1
        config = row["config_name"]
        if not config:
            continue
        if config not in resolved:
            resolved[config] = hint_from_config(config, terms)
        hint = resolved[config]
        if not hint:
            continue
        make, model, word = hint
        stats["named"] += 1

        assigned = row["file_id"] is not None
        generic = assigned and is_generic(row["file_name"] or "")

        if not assigned:
            # Only somewhere a file could actually go.
            if row["element_name"] and "no CAN ports" in row["element_name"]:
                continue
            kind = CANDIDATE
        elif generic:
            kind = UPGRADE
        elif fold(row["file_make"] or "") in NOT_A_MAKE:
            # The file is dedicated but the catalogue could not parse a
            # make out of it. Nothing to disagree with.
            continue
        elif (row["file_make"] or "").strip().upper() != make.upper():
            kind = MISMATCH
        else:
            continue

        stats[kind] += 1
        conn.execute(
            """INSERT INTO config_hints
               (imei, kind, hinted_make, hinted_model, matched_word,
                evidence, current_file, current_name, built_at)
               VALUES (?,?,?,?,?,?,?,?, datetime('now'))
               ON CONFLICT(imei, kind) DO UPDATE SET
                 hinted_make=excluded.hinted_make,
                 hinted_model=excluded.hinted_model,
                 matched_word=excluded.matched_word,
                 evidence=excluded.evidence,
                 current_file=excluded.current_file,
                 current_name=excluded.current_name,
                 built_at=datetime('now')""",
            (row["imei"], kind, make, model, word, config,
             row["file_id"], row["file_name"]))
    return stats


def report(conn, limit: int = 15) -> None:
    titles = {
        UPGRADE: ("ON A GENERIC FILE, NAMED AS SOMETHING WE COVER",
                  "a dedicated file usually reports more signals"),
        CANDIDATE: ("NO FILE ASSIGNED, NAMED AS SOMETHING WE COVER",
                    "fittable from the desk, no visit needed to decide"),
        MISMATCH: ("RUNNING A FILE FOR A DIFFERENT MAKE THAN THE NAME SAYS",
                   "usually a stale name, occasionally a wrong install"),
    }
    for kind in (CANDIDATE, UPGRADE, MISMATCH):
        title, why = titles[kind]
        total = conn.execute("SELECT COUNT(*) c FROM config_hints WHERE kind=?",
                             (kind,)).fetchone()["c"]
        print(f"\n{title}  ({total} devices)")
        print(f"  {why}\n")
        for row in conn.execute(
                """SELECT hinted_make, COUNT(*) n,
                          MIN(evidence) sample, MIN(matched_word) word,
                          MIN(current_name) now_on
                     FROM config_hints WHERE kind=?
                    GROUP BY hinted_make ORDER BY n DESC LIMIT ?""",
                (kind, limit)):
            on = f"  now on {row['now_on']}" if row["now_on"] else ""
            print(f"  {row['n']:>6}  {row['hinted_make']:<16}"
                  f" matched {row['word']!r}{on}")
            print(f"          e.g. {row['sample']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="canval.hints", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=15,
                        help="makes to list per section")
    parser.add_argument("--db", default=os.environ.get("CANVAL_DB", "canval.db"))
    args = parser.parse_args(argv)

    with connect(args.db) as conn:
        stats = build_hints(conn)
        print(f"\n  catalogue terms      {stats['catalogue_terms']}")
        print(f"  devices examined     {stats['devices_examined']}")
        print(f"  names naming a make  {stats['named']}")
        report(conn, args.limit)
        print("\n  These are leads read from text typed by installers, not "
              "facts read from devices.\n  Nothing here is counted as an "
              "installation anywhere else in the tool.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
