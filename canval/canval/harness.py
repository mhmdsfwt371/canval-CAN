"""Stress the pipeline the way production will.

Running the happy path proves nothing that matters. What matters is:

  1. DECISION QUALITY. Not "did it parse" but "would it have sent someone
     on a pointless drive, or told a customer yes when the answer is no".
     Those two errors are not equal: a wrong yes reaches the customer, a
     wrong no only costs a trip.

  2. INVARIANTS. Running twice must give the same answer. A sweep that
     dies halfway must not leave a database that lies.

  3. HOSTILE DATA. Real catalogues carry unicode, empty fields, absurd
     strings and duplicate ids. None of it should produce a confident
     wrong answer.

  4. FAILURE. Tokens expire mid-sweep, endpoints 500, pages come back
     short. The tool must degrade into "I don't know" rather than "no".

Every bug found during this project has a case here so it cannot return.

    python -m canval.harness
"""

from __future__ import annotations

import os
import random
import tempfile
import time
import traceback

from .index import build_device_index, refresh_catalogue
from .parsers import parse_model, parse_sensors
from .simulate import FakeAfaqy, FakeXdm, build_catalogue
from .store import connect, search_can_files, sweep_coverage, verify
from .xdm import XdmError

RESULTS = []


def check(name: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((name, passed, detail))
    mark = "PASS" if passed else "FAIL"
    print(f"  [{mark}] {name}" + (f"  -- {detail}" if detail else ""))


def fresh(n_files=800, n_devices=1500, seed=3):
    cat = build_catalogue(n_files, seed=seed)
    xdm = FakeXdm(cat, n_devices=n_devices, seed=seed)
    db = tempfile.mktemp(suffix=".db")
    refresh_catalogue(xdm, db)
    return cat, xdm, db


# ------------------------------------------------------- 1. decision quality

def suite_decisions():
    print("\n1. DECISION QUALITY -- would it mislead anyone?\n")
    cat, xdm, db = fresh()
    build_device_index(xdm, db, active_since_days=None, concurrency=8)

    truth = {u: {e[1] for e in v} for u, v in xdm.truth.items() if v}
    with connect(db) as conn:
        stored = {}
        for r in conn.execute(
                "SELECT imei, file_id FROM device_can WHERE file_id IS NOT NULL"):
            stored.setdefault(r["imei"], set()).add(r["file_id"])

        # For each catalogue file, does the tool agree with reality about
        # whether it has ever been fitted?
        fitted_truth = set()
        for files in truth.values():
            fitted_truth |= files
        fitted_tool = set()
        for files in stored.values():
            fitted_tool |= files

        # A file the tool calls unfitted but which really is fitted is the
        # dangerous direction: it sends a technician to a solved problem.
        wrong_no = fitted_truth - fitted_tool
        # A file the tool calls fitted but is not: it would tell a customer
        # "supported" on no evidence.
        wrong_yes = fitted_tool - fitted_truth

        check("no file wrongly reported as never fitted",
              not wrong_no, f"{len(wrong_no)} files")
        check("no file wrongly reported as fitted",
              not wrong_yes, f"{len(wrong_yes)} files")

        cov = sweep_coverage(conn)
        check("coverage counted per device, not per bus row",
              cov["swept"] == len(xdm.devices),
              f"swept={cov['swept']} devices={len(xdm.devices)}")
    os.remove(db)


# --------------------------------------------------------- 2. invariants

def suite_invariants():
    print("\n2. INVARIANTS -- does it stay the same when nothing changed?\n")
    cat, xdm, db = fresh()

    build_device_index(xdm, db, active_since_days=None, concurrency=8)
    with connect(db) as conn:
        first = sorted(tuple(r) for r in conn.execute(
            "SELECT imei, element_name, file_id FROM device_can"))
        cat_first = conn.execute("SELECT COUNT(*) n FROM can_files").fetchone()["n"]

    refresh_catalogue(xdm, db)
    build_device_index(xdm, db, active_since_days=None, concurrency=8)
    with connect(db) as conn:
        second = sorted(tuple(r) for r in conn.execute(
            "SELECT imei, element_name, file_id FROM device_can"))
        cat_second = conn.execute("SELECT COUNT(*) n FROM can_files").fetchone()["n"]

    check("sweep is idempotent", first == second,
          f"{len(first)} vs {len(second)} rows")
    check("catalogue refresh does not accumulate", cat_first == cat_second,
          f"{cat_first} vs {cat_second}")

    # A device that loses its CAN file must not keep the stale row.
    victim = next(u for u, v in xdm.truth.items() if v)
    xdm.truth[victim] = []
    build_device_index(xdm, db, active_since_days=None, concurrency=8)
    with connect(db) as conn:
        left = conn.execute(
            "SELECT COUNT(*) n FROM device_can WHERE imei=? AND file_id IS NOT NULL",
            (victim,)).fetchone()["n"]
    check("removing a device's file clears its old rows", left == 0,
          f"{left} stale rows")
    os.remove(db)


# ------------------------------------------------------- 3. hostile data

def suite_hostile():
    print("\n3. HOSTILE DATA -- garbage in, no confident garbage out\n")
    nasty = [
        "",
        "   ",
        "<VMID: , Version: >",
        "TOYOTA HIACE",                                    # no VMID at all
        "МЕРСЕДЕС АКТРОС [08+] <VMID: 5, Version: 1>",     # cyrillic
        "شاحنة <VMID: 6, Version: 1>",                     # arabic
        "A" * 3000 + " <VMID: 7, Version: 1>",
        "CAR [99-00] <VMID: 8, Version: 1>",               # century rollover
        "X (CAN9) (700Kbps) {OBD 1+2+3} [05-] <VMID: 9, Version: 1>",
        "NULL\x00BYTE <VMID: 10, Version: 1>",
    ]
    crashed = []
    for raw in nasty:
        try:
            m = parse_model(raw)
            parse_sensors(raw)
            _ = m.covers_year(2015)
        except Exception as exc:                            # noqa: BLE001
            crashed.append((raw[:30], exc))
    check("parser survives hostile model strings", not crashed,
          f"{len(crashed)} crashes")

    # Century rollover: [99-00] should not become 1999-2000 backwards
    m = parse_model("CAR [99-00] <VMID: 8, Version: 1>")
    sane = m.year_from is None or m.year_to is None or m.year_from <= m.year_to
    check("year ranges never run backwards", sane,
          f"{m.year_from}-{m.year_to}")

    # Duplicate ids from the server must not multiply rows
    dupes = [{"id": 1, "model": "A <VMID: 1, Version: 1>", "notes": ""},
             {"id": 1, "model": "A <VMID: 1, Version: 1>", "notes": ""},
             {"id": 1, "model": "A <VMID: 1, Version: 2>", "notes": ""}]

    class Dup:
        def iter_can_files(self, progress=None):
            for r in dupes:
                yield r
            if progress:
                progress(len(dupes), len(dupes))

    db = tempfile.mktemp(suffix=".db")
    st = refresh_catalogue(Dup(), db)
    with connect(db) as conn:
        n = conn.execute("SELECT COUNT(*) n FROM can_files").fetchone()["n"]
        v = verify(conn)
    check("duplicate ids collapse to one row", n == 1, f"{n} rows")
    check("integrity flags the count mismatch", not v["ok"] or st["can_files"] == n,
          f"ok={v['ok']} problems={v['problems']}")
    os.remove(db)


# ----------------------------------------------------------- 4. failure

def suite_failure():
    print("\n4. FAILURE -- degrade to 'unknown', never to a wrong 'no'\n")
    cat = build_catalogue(300, seed=5)

    class DiesHalfway(FakeXdm):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.n = 0

        def device_overrides(self, uid):
            self.n += 1
            if self.n > 200:
                raise XdmError("401 token expired")
            return super().device_overrides(uid)

    xdm = DiesHalfway(cat, n_devices=600, seed=5)
    db = tempfile.mktemp(suffix=".db")
    refresh_catalogue(xdm, db)
    st = build_device_index(xdm, db, active_since_days=None, concurrency=4)

    check("errors are counted, not swallowed", st["errors"] > 0,
          f"{st['errors']} errors")

    # The critical property: a device we failed to READ must not be stored
    # as a device with no CAN file. That is the difference between "we do
    # not know" and "there is nothing there".
    with connect(db) as conn:
        stored = {r["imei"] for r in conn.execute("SELECT DISTINCT imei FROM device_can")}
        failed_logged = conn.execute(
            "SELECT COUNT(*) n FROM sweep_log WHERE status='read_failed'"
        ).fetchone()["n"]

    # The exact assertion: a device whose read failed must appear nowhere
    # in device_can, because a row there is a claim about its state.
    check("failed reads are absent from device_can, not stored as 'no file'",
          len(stored) + st["errors"] == len(xdm.devices),
          f"{len(stored)} stored + {st['errors']} failed "
          f"= {len(stored)+st['errors']} of {len(xdm.devices)}")
    check("failed reads are logged as unknown",
          failed_logged == st["errors"],
          f"{failed_logged} logged vs {st['errors']} errors")
    os.remove(db)

    # Catalogue truncation must raise, not return a short list quietly.
    class Truncates:
        def iter_can_files(self, progress=None):
            raise XdmError("Catalogue truncated: 2000 of 3934 rows")

    db2 = tempfile.mktemp(suffix=".db")
    raised = False
    try:
        refresh_catalogue(Truncates(), db2)
    except XdmError:
        raised = True
    check("a truncated catalogue raises", raised)
    if os.path.exists(db2):
        os.remove(db2)


# -------------------------------------------------------------- 5. scale

def suite_scale():
    print("\n5. SCALE -- the whole estate, not just one hardware type\n")
    cat = build_catalogue(3934, seed=9)
    xdm = FakeXdm(cat, n_devices=44043, seed=9)
    db = tempfile.mktemp(suffix=".db")

    t0 = time.time()
    refresh_catalogue(xdm, db)
    t_cat = time.time() - t0

    t0 = time.time()
    st = build_device_index(xdm, db, active_since_days=None, concurrency=8)
    t_sweep = time.time() - t0

    with connect(db) as conn:
        cov = sweep_coverage(conn)
        t0 = time.time()
        rows = search_can_files(conn, "mercedes actros")
        t_search = time.time() - t0

    print(f"       catalogue {t_cat:.1f}s   sweep {t_sweep:.1f}s "
          f"({st['calls']} calls)   search {t_search*1000:.0f}ms")
    check("full estate sweeps without error", st["errors"] == 0)
    check("search stays interactive at full scale", t_search < 1.0,
          f"{t_search*1000:.0f}ms for {len(rows)} rows")
    check("every device accounted for", cov["swept"] == 44043,
          f"{cov['swept']}")
    os.remove(db)


# ------------------------------------------------------- 6. regressions

def suite_regressions():
    print("\n6. REGRESSIONS -- every bug found in this project\n")

    # A. sampling extrapolated an empty result over a whole config
    cat = build_catalogue(200, seed=2)
    file_ids = [r["id"] for r in cat]

    class LateConfig(FakeXdm):
        def __init__(self):
            self.catalogue = cat
            self.rng = random.Random(1)
            self.calls = {"canfiles": 0, "devices": 0, "overrides": 0}
            self.devices = []
            self.truth = {}
            for i in range(500):
                uid = f"8600000000{i:05d}"
                self.devices.append({
                    "settings": {"uid": uid, "hardware": {"name": "LX45-EA"},
                                 "configuration": {"currentConfigId": 1,
                                                   "currentConfigName": "big"}},
                    "information": {"activityUpdate": {"lastActivity": 1}}})
                self.truth[uid] = ([] if i < 3
                                   else [("CAN1 Vehicle model", file_ids[0],
                                          str(file_ids[0]))])

    x = LateConfig()
    db = tempfile.mktemp(suffix=".db")
    refresh_catalogue(x, db)
    st = build_device_index(x, db, active_since_days=None, concurrency=8,
                            sample_configs=3)
    check("empty sample never condemns a config",
          st["with_can_file"] == 497, f"found {st['with_can_file']} of 497")
    os.remove(db)

    # B. a device on two buses kept only one file
    class TwoBus(LateConfig):
        def __init__(self):
            super().__init__()
            for i, uid in enumerate(list(self.truth)):
                self.truth[uid] = [("CAN1 Vehicle model", file_ids[0], "a"),
                                   ("CAN2 Vehicle model", file_ids[1], "b")]

    x = TwoBus()
    db = tempfile.mktemp(suffix=".db")
    refresh_catalogue(x, db)
    st = build_device_index(x, db, active_since_days=None, concurrency=8)
    check("both CAN buses are recorded",
          st["bus_entries"] == 2 * len(x.devices),
          f"{st['bus_entries']} entries for {len(x.devices)} devices")
    os.remove(db)

    # C. "shacman 7300" must reach "SHACMAN H3000S"
    class Sh:
        def iter_can_files(self, progress=None):
            yield {"id": 1, "model": "SHACMAN H3000S (500Kbps) [20+] "
                                     "<VMID: 2888, Version: 1>",
                   "notes": "Available sensors: Engine Speed"}
            if progress:
                progress(1, 1)

    db = tempfile.mktemp(suffix=".db")
    refresh_catalogue(Sh(), db)
    with connect(db) as conn:
        hit = search_can_files(conn, "shacman 7300")
        hit2 = search_can_files(conn, "shacman")
    check("word-order-independent search", len(hit) == 1, f"{len(hit)} hits")
    check("plain make search still works", len(hit2) == 1)
    os.remove(db)

    # D. trim labels are not mistaken for years
    m = parse_model("MERCEDES W211 [E-class] (CAN1) <VMID: 12, Version: 1>")
    check("trim label parsed as variant, not a year",
          m.variant == "E-class" and m.year_from is None and not m.unparsed,
          f"variant={m.variant} years={m.year_from} issues={m.unparsed}")

    # E. the API `version` field must not be used for revision ordering
    a = parse_model("X (CAN1) <VMID: 5, Version: 1>")
    b = parse_model("X (CAN1) <VMID: 5, Version: 2>")
    check("revision read from the name, not the API field",
          a.version == 1 and b.version == 2)


# ------------------------------------------------------- 7. the real question

def suite_coverage():
    print("\n7. THE REAL QUESTION -- what will NOT work?\n")
    from .coverage import (ABSENT, NEVER, PROVEN as CP, UNCHECKABLE, UNKNOWN,
                           canonical, compare)
    from .monitor import (DeviceEvidence, Parameter, SensorDefinition,
                          VehicleState, read_snapshot)

    # The join between catalogue names and platform types must hold.
    pairs = [("Total Distance (Mileage)", "milage"),
             ("Engine Hours", "eng_hour"),
             ("Fuel Level [%]", "fuel_level"),
             ("Fuel Level [%] (J1939)", "fuel_level"),
             ("Total Fuel Used High Resolution", "total_fuel_can"),
             ("Gross Vehicle Weight", "weight"),
             ("Engine Speed", "rpm"),
             ("Ignition", "acc"),
             ("Door Front Left", None),
             ("Headlight Indicator", None)]
    bad = [(a, canonical(a), b) for a, b in pairs if canonical(a) != b]
    check("catalogue names map to platform types", not bad, str(bad[:3]))

    now = int(time.time())
    declared = ["Engine Speed", "Engine Hours", "Total Distance (Mileage)",
                "Axle Weight", "Fuel Level [%]", "Door Front Left"]

    defs = [SensorDefinition("Engine Speed", "rpm", "sensor_12300"),
            SensorDefinition("Total Engine Hours", "eng_hour", "sensor_16386"),
            SensorDefinition("Weight", "weight", "sensor_12301")]
    params = [Parameter("sensor_12300", 4825, now - 5),
              Parameter("sensor_16386", 66113, now - 60),
              Parameter("sensor_12301", 0, now - 200 * 86400)]
    ev = DeviceEvidence(imei="1", last_report=now,
                        readings=read_snapshot(params, now, defs,
                                               state=VehicleState("moving_engine_on",
                                                                  now - 7 * 3600)))
    c = compare(declared, [ev])
    got = {v.declared_name: v.status for v in c.verdicts}

    check("live sensors reported as working",
          got["Engine Speed"] == CP and got["Engine Hours"] == CP, str(got))
    check("a frozen sensor is reported as not delivered",
          got["Axle Weight"] == NEVER, got["Axle Weight"])
    check("a sensor absent from the device is reported as not delivered",
          got["Total Distance (Mileage)"] == ABSENT,
          got["Total Distance (Mileage)"])
    check("uncomparable sensors are not counted either way",
          got["Door Front Left"] == UNCHECKABLE, got["Door Front Left"])

    # With nothing readable, everything must be UNKNOWN -- not a failure list.
    blind = DeviceEvidence(imei="2", error="not found on the monitoring platform")
    c2 = compare(declared, [blind])
    statuses = {v.status for v in c2.verdicts}
    check("no readable install yields 'unknown', never 'does not work'",
          statuses <= {UNKNOWN, UNCHECKABLE}, str(statuses))


# --------------------------------------------------- 8. how people type

def suite_spelling():
    print("\n8. SPELLING -- nobody types it the same way twice\n")
    from .matching import rank, similar

    CAT = ["SHACMAN H3000S", "MERCEDES ACTROS MP3", "VOLVO FH", "SINOTRUK HOWO",
           "TOYOTA HIACE", "TOYOTA HILUX", "KAMAZ 6520", "SCANIA R SERIES",
           "MAN TGX", "DAF XF", "FAW J6", "HINO 500", "ISUZU NPR",
           "KIA SPORTAGE", "FIAT SCUDO", "IVECO STRALIS", "TATA PRIMA",
           "HYUNDAI PORTER"]

    variants = [
        ("شاكمان", "SHACMAN"), ("شكمان", "SHACMAN"), ("شاكمن", "SHACMAN"),
        ("مرسيدس اكتروس", "ACTROS"), ("أكتروس", "ACTROS"), ("مرسيديس", "MERCEDES"),
        ("فولفو", "VOLVO"), ("سينوتراك", "SINOTRUK"), ("هوو", "HOWO"),
        ("تويوتا هايس", "HIACE"), ("هايلكس", "HILUX"),
        ("كاماز", "KAMAZ"), ("كماز", "KAMAZ"), ("سكانيا", "SCANIA"),
        ("ايفيكو", "IVECO"), ("إيفيكو", "IVECO"), ("ايسوزو", "ISUZU"),
        ("هينو", "HINO"), ("فاو", "FAW"), ("كيا", "KIA"), ("فيات", "FIAT"),
        ("هيونداي", "HYUNDAI"), ("داف", "DAF"), ("مان", "MAN"),
        ("shakman", "SHACMAN"), ("shcman", "SHACMAN"),
    ]
    missed = [q for q, want in variants
              if not (rank(q, CAT, 1) and want in rank(q, CAT, 1)[0][0])]
    check("every spelling reaches the right vehicle", not missed,
          f"{len(missed)} missed: {missed[:4]}")

    # Two makes that merely rhyme must never be treated as one, because a
    # confident wrong answer here becomes a wrong quote.
    collisions = [(a, b) for a, b in
                  [("toyota", "tata"), ("فولفو", "فيات"), ("كيا", "كاماز"),
                   ("مان", "ماك"), ("هينو", "هوو"), ("daf", "faw"),
                   ("hino", "howo"), ("scania", "shacman"), ("كيا", "كماز")]
                  if similar(a, b)]
    check("similar-sounding makes stay apart", not collisions, str(collisions))

    # A search must not answer with the weak tail of the ranking.
    cat_ = build_catalogue(400, seed=7)

    class C:
        def iter_can_files(self, progress=None):
            for r in cat_:
                yield r
            if progress:
                progress(len(cat_), len(cat_))

    db = tempfile.mktemp(suffix=".db")
    refresh_catalogue(C(), db)
    with connect(db) as conn:
        makes = {r["name"] for r in search_can_files(conn, "سكانيا")}
        nonsense = search_can_files(conn, "حاجة مش موجودة خالص")
    check("a sound search returns one make, not a shortlist",
          len(makes) == 1, str(sorted(makes)))
    check("nonsense returns nothing rather than a guess",
          not nonsense, f"{len(nonsense)} rows")
    os.remove(db)


# ------------------------------------------------- 9. the daily sync

def suite_sync():
    print("\n9. DAILY SYNC -- what changed since yesterday?\n")
    from .store import last_sync, top_makes, whats_new

    cat = build_catalogue(300, seed=17)
    xdm = FakeXdm(cat, n_devices=900, seed=17)
    db = tempfile.mktemp(suffix=".db")

    refresh_catalogue(xdm, db)
    build_device_index(xdm, db, active_since_days=None, concurrency=6)

    with connect(db) as conn:
        baseline = whats_new(conn, days=365)
        makes = top_makes(conn, limit=10)
    check("the first sync reports nothing as new", not baseline,
          f"{len(baseline)} items")
    check("opening screen is built from real installs",
          makes and all(m["installs"] > 0 for m in makes),
          f"{len(makes)} makes")

    time.sleep(1.2)     # so the second sync lands in a later second
    grown = cat[:-2] + [
        {"id": 70001, "model": "KAMAZ 6520 [18+] (CAN1) <VMID: 7001, Version: 1>",
         "notes": "Available sensors: Engine Speed"},
        {"id": 70002, "model": "KAMAZ 6520 [18+] (CAN2) <VMID: 7002, Version: 1>",
         "notes": "Available sensors: Axle Weight"},
    ]

    class Grown:
        def iter_can_files(self, progress=None):
            for r in grown:
                yield r
            if progress:
                progress(len(grown), len(grown))

    st = refresh_catalogue(Grown(), db)
    check("added files are counted", st["new_since_last_sync"] == 2,
          str(st["new_since_last_sync"]))
    check("withdrawn files are counted", st["withdrawn"] == 2,
          str(st["withdrawn"]))

    with connect(db) as conn:
        fresh = whats_new(conn, days=30)
        run = last_sync(conn)
    names = {n["name"] for n in fresh}
    check("a new vehicle on two buses is listed once",
          names == {"KAMAZ 6520"} and fresh[0]["buses"] == 2,
          f"{names}, buses={fresh[0]['buses'] if fresh else '-'}")
    check("the sync run is recorded", run and run["added"] == 2, str(run))

    # Running again with nothing changed must not invent news.
    time.sleep(1.2)
    st2 = refresh_catalogue(Grown(), db)
    check("an unchanged sync reports nothing new",
          st2["new_since_last_sync"] == 0 and st2["withdrawn"] == 0,
          f"+{st2['new_since_last_sync']} -{st2['withdrawn']}")
    os.remove(db)


def main():
    print("=" * 74)
    print("  canval harness -- adversarial run against the simulator")
    print("=" * 74)
    for suite in (suite_decisions, suite_invariants, suite_hostile,
                  suite_failure, suite_scale, suite_regressions,
                  suite_coverage, suite_spelling, suite_sync):
        try:
            suite()
        except Exception:                                   # noqa: BLE001
            print(f"  [FAIL] {suite.__name__} raised")
            traceback.print_exc()
            RESULTS.append((suite.__name__, False, "raised"))

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n" + "=" * 74)
    print(f"  {passed}/{len(RESULTS)} checks passed")
    if passed < len(RESULTS):
        print("\n  FAILURES:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"    - {name}  {detail}")
    print("=" * 74)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
