"""Local store.

THE SHAPE, and why
------------------
A VMID is not a vehicle. It is a vehicle *on one CAN bus*: the same truck
appears three times in the catalogue with three different VMIDs, one per
bus.

    MERCEDES ACTROS MP3 (CAN1)  VMID 1274
    MERCEDES ACTROS MP3 (CAN2)  VMID 608
    MERCEDES ACTROS MP3 (CAN3)  VMID 1610

Grouping by VMID alone would silently merge buses; grouping by name alone
would silently merge revisions. So the model is three levels:

    vehicle   name + year range
      bus     one VMID per CAN bus
        revision   one row per file, newest wins

The revision number comes from the model string, not from the API's
`version` field -- that field returned 1 for a row whose own name said
"Version: 2", so it is useless for ordering.

WHY SOURCES ARE TRACKED
-----------------------
An earlier database ended up with 6793 rows for a 3934-row catalogue,
because two different clients wrote into the same table and neither
cleared the other's rows. Every row now records where it came from, and a
refresh deletes only its own source. `verify()` fails loudly if the counts
stop making sense.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

SOURCE_API = "api"          # documented client_credentials API
SOURCE_CONSOLE = "console"  # browser session, undocumented endpoints

SCHEMA = """
CREATE TABLE IF NOT EXISTS can_files (
    file_id      INTEGER NOT NULL,
    source       TEXT    NOT NULL,
    vmid         INTEGER,
    revision     INTEGER,
    raw_model    TEXT NOT NULL,
    name         TEXT,
    variant      TEXT,
    make         TEXT,
    model        TEXT,
    year_from    INTEGER,
    year_to      INTEGER,
    can_bus      INTEGER,
    bitrate_kbps INTEGER,
    obd_pins     TEXT,
    raw_notes    TEXT,
    manual_url   TEXT,
    change_log   TEXT,
    created_on   TEXT,
    parse_issues TEXT,
    fetched_at   TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (file_id, source)
);
CREATE INDEX IF NOT EXISTS ix_cf_vmid   ON can_files(vmid);
CREATE INDEX IF NOT EXISTS ix_cf_name   ON can_files(name);
CREATE INDEX IF NOT EXISTS ix_cf_make   ON can_files(make);
CREATE INDEX IF NOT EXISTS ix_cf_model  ON can_files(make, model);
CREATE INDEX IF NOT EXISTS ix_cf_source ON can_files(source);

CREATE TABLE IF NOT EXISTS can_sensors (
    file_id       INTEGER NOT NULL,
    source        TEXT    NOT NULL,
    sensor_name   TEXT    NOT NULL,
    status        TEXT    NOT NULL,
    unit          TEXT,
    superseded_by TEXT,
    PRIMARY KEY (file_id, source, sensor_name)
);

-- One row per device PER BUS, keyed by the bus itself.
--
-- The previous key was (imei, element_name), which looked right and was
-- not: CAN1 and CAN2 both call their element "Vehicle model", so the
-- second row overwrote the first and one of the two files disappeared.
-- The whole 6475-device sweep produced zero devices with more than one
-- row, on an estate where dual-bus fitments are ordinary.
--
-- `inherited` records where the value came from. Most devices inherit
-- theirs from the configuration template rather than overriding it, and
-- an earlier reader that only looked at overrides therefore reported 95%
-- of the estate as never fitted.
--
-- `port_function` is context, not a filter. A port set to Sleep can still
-- carry an assigned model, and the question being answered is whether the
-- file is assigned -- not whether the port happens to be awake today.
CREATE TABLE IF NOT EXISTS device_can (
    imei          TEXT    NOT NULL,
    bus           INTEGER NOT NULL,   -- 1 = CAN1, 2 = CAN2, 0 = nothing found
    element_name  TEXT    NOT NULL,
    element_id    INTEGER,
    file_id       INTEGER,
    raw_value     TEXT,
    inherited     INTEGER,            -- 1 = from the template, 0 = set here
    port_function TEXT,
    hardware      TEXT,
    config_name   TEXT,
    template_id   INTEGER,
    last_activity INTEGER,
    seen_at       TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (imei, bus)
);

-- One row per device PER SCRIPT SLOT. A device carries up to three, and
-- every slot's element is called plain "Script", so this key is the same
-- lesson (imei, bus) taught: key by the position, never by the name.
-- The "(none)" slot marks a device that was read and had no script at
-- all -- absence recorded is not the same as never asked.
CREATE TABLE IF NOT EXISTS device_script (
    imei         TEXT NOT NULL,
    slot         TEXT NOT NULL,      -- Script1 / Script2 / Script3 / (none)
    element_id   INTEGER,
    script_id    INTEGER,            -- when the stored value is numeric
    raw_value    TEXT,
    script_name  TEXT,               -- resolved from the catalogue, or the
                                     -- raw value itself when non-numeric
    inherited    INTEGER,            -- 1 = from the template, 0 = set here
    hardware     TEXT,
    config_name  TEXT,
    template_id  INTEGER,
    seen_at      TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (imei, slot)
);
CREATE INDEX IF NOT EXISTS ix_dc_file ON device_can(file_id);
CREATE INDEX IF NOT EXISTS ix_dc_imei ON device_can(imei);

CREATE TABLE IF NOT EXISTS sweep_log (
    imei       TEXT PRIMARY KEY,
    status     TEXT NOT NULL,
    detail     TEXT,
    checked_at TEXT DEFAULT (datetime('now'))
);

-- Field trials, recorded by whoever went out. This is the loop that stops
-- the same question being asked twice: an unsupported vehicle checked once
-- becomes a desk answer forever after.
CREATE TABLE IF NOT EXISTS trials (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    query        TEXT NOT NULL,
    make_model   TEXT,
    year         INTEGER,
    file_id      INTEGER,
    can_bus      INTEGER,
    pins         TEXT,
    bitrate_kbps INTEGER,
    outcome      TEXT NOT NULL,      -- works | partial | fails
    signals_ok   TEXT,
    signals_bad  TEXT,
    dealer_enable INTEGER,           -- did it need enabling at the dealer
    notes        TEXT,
    recorded_by  TEXT,
    recorded_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_trials_q ON trials(lower(query));

-- Phonetic index over the catalogue, so type-ahead does not recompute a
-- sound match across 3934 rows on every keystroke. Built once after a
-- catalogue refresh; a lookup is then an indexed prefix scan.
CREATE TABLE IF NOT EXISTS name_sounds (
    name      TEXT NOT NULL,
    word      TEXT NOT NULL,      -- one folded word of the name
    skel      TEXT NOT NULL,      -- its consonant skeleton
    position  INTEGER NOT NULL,   -- 0 = first word, which carries the make
    PRIMARY KEY (name, word, skel)
);
CREATE INDEX IF NOT EXISTS ix_sounds_skel ON name_sounds(skel);
CREATE INDEX IF NOT EXISTS ix_sounds_word ON name_sounds(word);

-- What the catalogue looked like over time. This table is never cleared by
-- a refresh, which is the whole point: `can_files` is replaced wholesale
-- every sync, so without a record that outlives it there is no way to say
-- what arrived this week. Xirgo add files continuously, and "these twelve
-- vehicles became supported since Sunday" is a sales trigger nobody
-- currently gets.
CREATE TABLE IF NOT EXISTS file_history (
    file_id     INTEGER NOT NULL,
    source      TEXT NOT NULL,
    vmid        INTEGER,
    name        TEXT,
    raw_model   TEXT,
    revision    INTEGER,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    gone_at     TEXT,
    PRIMARY KEY (file_id, source)
);
CREATE INDEX IF NOT EXISTS ix_hist_first ON file_history(first_seen);
CREATE INDEX IF NOT EXISTS ix_hist_name  ON file_history(name);

CREATE TABLE IF NOT EXISTS sync_runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT NOT NULL,
    rows       INTEGER,
    added      INTEGER,
    removed    INTEGER,
    ran_at     TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS source_runs (
    source     TEXT PRIMARY KEY,
    rows       INTEGER,
    expected   INTEGER,
    finished_at TEXT
);

-- Newest revision per bus. This is what a recommendation should point at.
CREATE VIEW IF NOT EXISTS current_files AS
SELECT f.*
FROM can_files f
JOIN (
    SELECT vmid, source, MAX(COALESCE(revision, 0)) AS rev, MAX(file_id) AS fid
    FROM can_files WHERE vmid IS NOT NULL GROUP BY vmid, source
) top
  ON f.vmid = top.vmid AND f.source = top.source
 AND COALESCE(f.revision, 0) = top.rev AND f.file_id = top.fid;
"""


@contextmanager
def connect(path: str):
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def clear_source(conn, source: str) -> int:
    """Drop everything from one source before refilling it.

    Refreshing without this is what produced a database holding two
    overlapping copies of the catalogue.
    """
    n = conn.execute("SELECT COUNT(*) c FROM can_files WHERE source=?",
                     (source,)).fetchone()["c"]
    conn.execute("DELETE FROM can_sensors WHERE source=?", (source,))
    conn.execute("DELETE FROM can_files WHERE source=?", (source,))
    return n


def upsert_can_file(conn, parsed, file_id: int, raw_notes: str, sensors,
                    source: str = SOURCE_API, manual_url=None,
                    change_log=None, created_on=None) -> None:
    conn.execute(
        """INSERT INTO can_files
           (file_id, source, vmid, revision, raw_model, name, variant,
            make, model, year_from, year_to, can_bus, bitrate_kbps, obd_pins,
            raw_notes, manual_url, change_log, created_on, parse_issues,
            fetched_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'))
           ON CONFLICT(file_id, source) DO UPDATE SET
             vmid=excluded.vmid, revision=excluded.revision,
             raw_model=excluded.raw_model, name=excluded.name,
             variant=excluded.variant, make=excluded.make,
             model=excluded.model, year_from=excluded.year_from,
             year_to=excluded.year_to, can_bus=excluded.can_bus,
             bitrate_kbps=excluded.bitrate_kbps, obd_pins=excluded.obd_pins,
             raw_notes=excluded.raw_notes, manual_url=excluded.manual_url,
             change_log=excluded.change_log, created_on=excluded.created_on,
             parse_issues=excluded.parse_issues, fetched_at=datetime('now')""",
        (file_id, source, parsed.vmid, parsed.version, parsed.raw, parsed.name,
         parsed.variant, parsed.make, parsed.model,
         parsed.year_from, parsed.year_to, parsed.can_bus,
         parsed.bitrate_kbps, parsed.obd_pins, raw_notes, manual_url,
         change_log, created_on, "; ".join(parsed.unparsed) or None),
    )
    conn.execute("DELETE FROM can_sensors WHERE file_id=? AND source=?",
                 (file_id, source))
    conn.executemany(
        """INSERT OR REPLACE INTO can_sensors
           (file_id, source, sensor_name, status, unit, superseded_by)
           VALUES (?,?,?,?,?,?)""",
        [(file_id, source, s.name, s.status, s.unit, s.superseded_by)
         for s in sensors.sensors],
    )


def record_run(conn, source: str, rows: int, expected: int | None) -> None:
    conn.execute(
        """INSERT INTO source_runs (source, rows, expected, finished_at)
           VALUES (?,?,?, datetime('now'))
           ON CONFLICT(source) DO UPDATE SET
             rows=excluded.rows, expected=excluded.expected,
             finished_at=datetime('now')""",
        (source, rows, expected),
    )


def verify(conn) -> dict:
    """Sanity checks. Anything false here means do not trust a search."""
    out = {"ok": True, "problems": [], "counts": {}}

    for r in conn.execute(
        "SELECT source, COUNT(*) n FROM can_files GROUP BY source"
    ):
        out["counts"][r["source"]] = r["n"]

    for r in conn.execute("SELECT * FROM source_runs"):
        stored = out["counts"].get(r["source"], 0)
        if r["expected"] and stored != r["expected"]:
            out["ok"] = False
            out["problems"].append(
                f"{r['source']}: stored {stored} rows but the server "
                f"reported {r['expected']}"
            )

    dupes = conn.execute(
        """SELECT COUNT(*) n FROM (
             SELECT file_id FROM can_files GROUP BY file_id, source
             HAVING COUNT(*) > 1)"""
    ).fetchone()["n"]
    if dupes:
        out["ok"] = False
        out["problems"].append(f"{dupes} duplicate file_id within one source")

    orphan = conn.execute(
        """SELECT COUNT(*) n FROM device_can d
           WHERE d.file_id IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM can_files f WHERE f.file_id=d.file_id)"""
    ).fetchone()["n"]
    if orphan:
        out["problems"].append(
            f"{orphan} devices point at a file_id missing from the catalogue")

    out["vehicles"] = conn.execute(
        "SELECT COUNT(DISTINCT vmid) n FROM can_files WHERE vmid IS NOT NULL"
    ).fetchone()["n"]
    out["no_vmid"] = conn.execute(
        "SELECT COUNT(*) n FROM can_files WHERE vmid IS NULL"
    ).fetchone()["n"]
    return out


def record_device_can(conn, imei, file_id, element_name, raw_value,
                      hardware=None, config_name=None, last_activity=None):
    """Record one bus of one device. Call once per vehicle-model override."""
    conn.execute(
        """INSERT INTO device_can
           (imei, element_name, file_id, raw_value, hardware, config_name,
            last_activity, seen_at)
           VALUES (?,?,?,?,?,?,?, datetime('now'))
           ON CONFLICT(imei, element_name) DO UPDATE SET
             file_id=excluded.file_id, raw_value=excluded.raw_value,
             hardware=excluded.hardware, config_name=excluded.config_name,
             last_activity=excluded.last_activity, seen_at=datetime('now')""",
        (imei, element_name or "(none)", file_id, raw_value, hardware,
         config_name, last_activity),
    )


def clear_device(conn, imei: str) -> None:
    """Drop a device's rows before rewriting them.

    Without this a device that loses a bus keeps the stale row forever.
    """
    conn.execute("DELETE FROM device_can WHERE imei=?", (imei,))
    conn.execute("DELETE FROM device_script WHERE imei=?", (imei,))


def log_sweep(conn, imei: str, status: str, detail: str | None = None):
    conn.execute(
        """INSERT INTO sweep_log (imei, status, detail, checked_at)
           VALUES (?,?,?, datetime('now'))
           ON CONFLICT(imei) DO UPDATE SET
             status=excluded.status, detail=excluded.detail,
             checked_at=datetime('now')""",
        (imei, status, detail),
    )


def devices_for_file(conn, file_id: int) -> list:
    return conn.execute(
        """SELECT imei, element_name, raw_value, hardware, config_name,
                  last_activity
           FROM device_can WHERE file_id=? ORDER BY last_activity DESC""",
        (file_id,),
    ).fetchall()


def sweep_coverage(conn) -> dict:
    """Coverage counted by device, not by row -- a device with two buses
    is one device, not two."""
    row = conn.execute(
        """SELECT COUNT(DISTINCT imei) total,
                  COUNT(DISTINCT CASE WHEN file_id IS NOT NULL THEN imei END) resolved
           FROM device_can"""
    ).fetchone()
    total = row["total"] or 0
    resolved = row["resolved"] or 0
    buses = conn.execute(
        "SELECT COUNT(*) n FROM device_can WHERE file_id IS NOT NULL"
    ).fetchone()["n"]
    return {
        "swept": total,
        "resolved": resolved,
        "unresolved": total - resolved,
        "bus_entries": buses,
        "pct": round(100 * resolved / total, 1) if total else 0.0,
    }


def devices_for_vmid(conn, vmid: int) -> list:
    """Every device on any revision of this bus, newest revision first."""
    return conn.execute(
        """SELECT d.imei, d.file_id, d.element_name, d.hardware,
                  d.config_name, d.last_activity,
                  f.revision, f.raw_model
           FROM device_can d
           JOIN can_files f ON f.file_id = d.file_id
           WHERE f.vmid = ? AND d.file_id IS NOT NULL
           GROUP BY d.imei, d.element_name
           ORDER BY f.revision DESC, d.last_activity DESC""",
        (vmid,),
    ).fetchall()


_NOISE = {"can", "canbus", "bus", "truck", "lorry", "bus", "vehicle", "car",
          "model", "v1", "v2"}


def search_can_files(conn, text: str, year: int | None = None,
                     current_only: bool = True) -> list:
    """Search the catalogue, one word at a time.

    A single LIKE on the whole phrase fails on the way people actually
    ask: "shacman 7300" never matches "SHACMAN H3000S", because the words
    are not adjacent in that order. Each word is matched separately and
    all must hit, so word order and extra words stop mattering. Digits
    match loosely -- 7300 finds H3000S -- since model numbers get quoted
    with and without their prefix letters.
    """
    table = "current_files" if current_only else "can_files"

    words = [w for w in text.strip().lower().split()
             if w and w not in _NOISE]
    if not words:
        words = [text.strip().lower()]

    clauses, params = [], []
    for w in words:
        blob = "(lower(raw_model) || ' ' || lower(coalesce(variant,'')))"
        if w.isdigit() and len(w) >= 3:
            # 7300 -> also try 300, 30, so H3000S is reachable
            alts = {w, w.rstrip("0") or w, w[1:], w[:-1]}
            sub = " OR ".join(f"{blob} LIKE ?" for _ in alts)
            clauses.append(f"({sub})")
            params.extend(f"%{a}%" for a in alts)
        else:
            clauses.append(f"{blob} LIKE ?")
            params.append(f"%{w}%")

    # Rank by evidence, not alphabetically. A search for a common make can
    # return hundreds of catalogue rows; the ones already fitted and
    # reporting are the answer, the rest are background. Sorting by name
    # buries the useful hit on page four, which is the same as not having
    # it.
    rows = conn.execute(
        f"""SELECT f.*,
                   (SELECT COUNT(DISTINCT d.imei)
                      FROM device_can d
                     WHERE d.file_id = f.file_id) AS installs
            FROM {table} f
            WHERE {' AND '.join(clauses)}
            ORDER BY installs DESC, f.name, f.can_bus, f.year_from""",
        params,
    ).fetchall()

    if not rows and len(words) > 1:
        # nothing matched every word: fall back to the most distinctive one
        longest = max(words, key=len)
        rows = conn.execute(
            f"""SELECT f.*,
                       (SELECT COUNT(DISTINCT d.imei)
                          FROM device_can d
                         WHERE d.file_id = f.file_id) AS installs
                FROM {table} f
                WHERE lower(f.raw_model) LIKE ?
                   OR lower(coalesce(f.variant,'')) LIKE ?
                ORDER BY installs DESC, f.name, f.can_bus, f.year_from""",
            (f"%{longest}%", f"%{longest}%"),
        ).fetchall()

    if not rows:
        # Still nothing. Before giving up, try matching by sound rather than
        # by letters: the catalogue is in latin script and the person may
        # have typed Arabic, or simply spelt it their own way. شكمان and
        # شاكمان and SHACMAN are one truck.
        rows = _match_by_sound(conn, text, table, year)

    return rows


def _match_by_sound(conn, text: str, table: str, year: int | None) -> list:
    from .matching import ANSWER_BAR, rank

    names = conn.execute(
        f"SELECT DISTINCT name FROM {table} WHERE name IS NOT NULL AND name != ''"
    ).fetchall()
    hits = rank(text, [r["name"] for r in names], limit=6)
    if not hits:
        return []

    # Keep only what is as good as the best hit. A sound search returns a
    # ranked list, and letting the weak tail through means a search for
    # SCANIA also answers with FIAT -- the ordering is right but the reader
    # sees three makes and stops trusting the tool.
    top = hits[0][1]
    if top < ANSWER_BAR:
        return []
    strong = [n for n, score in hits if score >= max(ANSWER_BAR, top - 0.05)]

    marks = ",".join("?" * len(strong))
    rows = conn.execute(
        f"""SELECT f.*,
                   (SELECT COUNT(DISTINCT d.imei)
                      FROM device_can d
                     WHERE d.file_id = f.file_id) AS installs
            FROM {table} f
            WHERE f.name IN ({marks})
            ORDER BY installs DESC, f.name, f.can_bus, f.year_from""",
        strong,
    ).fetchall()

    if year is None:
        return rows
    keep = []
    for r in rows:
        if r["year_from"] is None:
            keep.append(r)
        elif year >= r["year_from"] and (r["year_to"] is None or year <= r["year_to"]):
            keep.append(r)
    return keep


def revisions_for_vmid(conn, vmid: int) -> list:
    return conn.execute(
        """SELECT file_id, source, revision, raw_model, change_log, created_on
           FROM can_files WHERE vmid=? ORDER BY COALESCE(revision,0), file_id""",
        (vmid,),
    ).fetchall()


def record_trial(conn, **kw) -> int:
    """Store a field result. The point of the tool is that this happens once."""
    cols = ("query", "make_model", "year", "file_id", "can_bus", "pins",
            "bitrate_kbps", "outcome", "signals_ok", "signals_bad",
            "dealer_enable", "notes", "recorded_by")
    values = [kw.get(c) for c in cols]
    cur = conn.execute(
        f"INSERT INTO trials ({','.join(cols)}) "
        f"VALUES ({','.join('?' * len(cols))})", values)
    return cur.lastrowid


def trials_for(conn, query: str, file_id: int | None = None) -> list:
    """Past field results for a query or a file.

    Matched on any shared word, because nobody types the same thing twice.
    """
    words = [w for w in (query or "").lower().split() if len(w) > 2]
    if file_id is not None:
        rows = conn.execute(
            "SELECT * FROM trials WHERE file_id=? ORDER BY recorded_at DESC",
            (file_id,)).fetchall()
        if rows:
            return rows
    if not words:
        return []
    clause = " OR ".join("lower(query) LIKE ?" for _ in words)
    return conn.execute(
        f"SELECT * FROM trials WHERE {clause} ORDER BY recorded_at DESC LIMIT 20",
        [f"%{w}%" for w in words]).fetchall()


# ---------------------------------------------------------------- suggest

def rebuild_sound_index(conn) -> int:
    """Index every catalogue name by how its words sound.

    One row per (name, word, skeleton). A word can produce several
    skeletons where a letter has two readings, and all of them are stored,
    because the point is to be findable rather than tidy.
    """
    from .matching import fold, skeletons

    conn.execute("DELETE FROM name_sounds")
    rows = conn.execute(
        "SELECT DISTINCT name FROM can_files WHERE name IS NOT NULL AND name != ''"
    ).fetchall()

    batch = []
    for r in rows:
        name = r["name"]
        for pos, word in enumerate(fold(name).split()):
            if len(word) < 2:
                continue
            for skel in skeletons(word):
                if skel:
                    batch.append((name, word, skel, pos))

    conn.executemany(
        "INSERT OR IGNORE INTO name_sounds (name, word, skel, position) "
        "VALUES (?,?,?,?)", batch)
    return len(rows)


def suggest(conn, text: str, limit: int = 8) -> list[dict]:
    """What the person might have meant, best first.

    Deliberately a list rather than an answer. "مرسيدس" matches thirty-five
    catalogue rows across unrelated model families, and picking one for the
    reader would be a guess presented as a fact. Ordering by how many
    vehicles are actually fitted puts the ones we can speak about first.
    """
    from .matching import SUGGEST_BAR, fold, prefix_score, skeletons

    words = [w for w in fold(text).split() if len(w) >= 2]
    if not words:
        return []

    # Gather candidate names phonetically: exact skeleton, or a skeleton
    # that starts with what has been typed so far (someone mid-word).
    candidates: set = set()
    for w in words:
        # by letters: the word so far starts a catalogue word
        for r in conn.execute(
                "SELECT name FROM name_sounds WHERE word LIKE ? LIMIT 500",
                (w + "%",)):
            candidates.add(r["name"])
        # by sound: same, on the consonant skeleton, so شكم reaches SHACMAN
        for skel in skeletons(w):
            if not skel:
                continue
            for r in conn.execute(
                    "SELECT name FROM name_sounds "
                    "WHERE skel = ? OR skel LIKE ? LIMIT 500",
                    (skel, skel + "%")):
                candidates.add(r["name"])

    if not candidates:
        return []

    scored = sorted(((n, prefix_score(text, n)) for n in candidates),
                    key=lambda x: -x[1])
    keep = [(n, sc) for n, sc in scored[: limit * 3] if sc >= SUGGEST_BAR]
    if not keep:
        return []

    out = []
    for name, score in keep:
        row = conn.execute(
            """SELECT f.name, f.variant,
                      MIN(f.year_from) AS y0, MAX(coalesce(f.year_to, 9999)) AS y1,
                      COUNT(DISTINCT f.vmid) AS buses,
                      (SELECT COUNT(DISTINCT d.imei) FROM device_can d
                        JOIN can_files g ON g.file_id = d.file_id
                       WHERE g.name = f.name AND d.file_id IS NOT NULL) AS installs
               FROM can_files f WHERE f.name = ? GROUP BY f.name""",
            (name,)).fetchone()
        if not row:
            continue
        out.append({
            "name": row["name"],
            "variant": row["variant"],
            "year_from": row["y0"],
            "year_to": None if row["y1"] == 9999 else row["y1"],
            "buses": row["buses"],
            "installs": row["installs"] or 0,
            "score": score,
        })

    # Order: real vehicles before protocol entries, then confidence, then
    # how many are fitted. J1939 is a fallback, not something anyone was
    # searching for, so it never leads the list.
    from .fallback import is_generic

    out.sort(key=lambda x: (is_generic(x["name"]),
                            -round(x["score"], 2),
                            -(x["installs"] > 0),
                            -x["installs"]))
    return out[:limit]


# ------------------------------------------------------------ what changed

def record_history(conn, source: str) -> dict:
    """Fold the current catalogue into the history table.

    Called after a refresh, while `can_files` holds the new snapshot. A
    file already known keeps its original `first_seen`; a file that has
    stopped appearing is marked gone rather than deleted, because "this was
    withdrawn" is worth knowing too.
    """
    before = {r["file_id"] for r in conn.execute(
        "SELECT file_id FROM file_history WHERE source=? AND gone_at IS NULL",
        (source,))}

    conn.execute(
        """INSERT INTO file_history
             (file_id, source, vmid, name, raw_model, revision,
              first_seen, last_seen)
           SELECT file_id, source, vmid, name, raw_model, revision,
                  datetime('now'), datetime('now')
             FROM can_files WHERE source = ?
           ON CONFLICT(file_id, source) DO UPDATE SET
             vmid=excluded.vmid, name=excluded.name,
             raw_model=excluded.raw_model, revision=excluded.revision,
             last_seen=datetime('now'), gone_at=NULL""",
        (source,))

    now = {r["file_id"] for r in conn.execute(
        "SELECT file_id FROM can_files WHERE source=?", (source,))}

    vanished = before - now
    if vanished:
        conn.executemany(
            "UPDATE file_history SET gone_at=datetime('now') "
            "WHERE file_id=? AND source=? AND gone_at IS NULL",
            [(f, source) for f in vanished])

    added = len(now - before)
    conn.execute(
        "INSERT INTO sync_runs (source, rows, added, removed, ran_at) "
        "VALUES (?,?,?,?, datetime('now'))",
        (source, len(now), added, len(vanished)))
    return {"total": len(now), "added": added, "removed": len(vanished)}


def whats_new(conn, days: int = 7, limit: int = 40) -> list[dict]:
    """Vehicles that became supported recently.

    Grouped by vehicle rather than by file: one truck arriving with three
    CAN buses is one new vehicle, and listing it three times reads like
    three.

    The first sync is a baseline, not news. Reporting all 3934 files as
    new on day one would bury the handful that actually arrive each week,
    and after being ignored once nobody looks again. Anything written in
    the same second as that first run is part of it.
    """
    baseline = conn.execute(
        "SELECT MIN(ran_at) t FROM sync_runs").fetchone()["t"]
    if not baseline:
        return []

    rows = conn.execute(
        """SELECT h.name,
                  MIN(h.first_seen)      AS since,
                  COUNT(DISTINCT h.vmid) AS buses,
                  MIN(f.year_from)       AS year_from,
                  MAX(coalesce(f.year_to, 9999)) AS year_to
             FROM file_history h
             LEFT JOIN can_files f
                    ON f.file_id = h.file_id AND f.source = h.source
            WHERE h.gone_at IS NULL
              AND h.name IS NOT NULL AND h.name != ''
              AND h.first_seen >= datetime('now', ?)
              AND h.first_seen > ?
            GROUP BY h.name
            ORDER BY since DESC, h.name
            LIMIT ?""",
        (f"-{int(days)} days", baseline, limit)).fetchall()

    return [{
        "name": r["name"],
        "since": r["since"],
        "buses": r["buses"],
        "year_from": r["year_from"],
        "year_to": None if (r["year_to"] or 0) == 9999 else r["year_to"],
    } for r in rows]


def last_sync(conn) -> dict | None:
    r = conn.execute(
        "SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1").fetchone()
    return dict(r) if r else None


# ------------------------------------------------------------- top makes

def top_makes(conn, limit: int = 12) -> list[dict]:
    """The makes this fleet actually runs, most fitted first.

    An empty search box tells a salesperson nothing. The makes we have real
    installs for are both a starting point and a useful fact in themselves:
    it is a picture of what the fleet is made of.
    """
    rows = conn.execute(
        """SELECT f.name,
                  COUNT(DISTINCT d.imei) AS installs,
                  COUNT(DISTINCT f.vmid) AS buses,
                  MIN(f.year_from)       AS year_from
             FROM can_files f
             JOIN device_can d ON d.file_id = f.file_id
            WHERE d.file_id IS NOT NULL AND f.name IS NOT NULL AND f.name != ''
            GROUP BY f.name
            HAVING installs > 0
            ORDER BY installs DESC
            LIMIT ?""", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------ browse by lists

from datetime import date as _date

_THIS_YEAR = _date.today().year


def browse_makes(conn) -> list[dict]:
    """Every manufacturer in the catalogue, most-fitted first.

    Protocol entries are excluded: J1939 is not a manufacturer, and a
    dropdown that lists it beside MERCEDES teaches the reader that the
    list cannot be trusted.
    """
    rows = conn.execute(
        """SELECT f.make,
                  COUNT(DISTINCT f.model) AS models,
                  COUNT(DISTINCT d.imei)  AS installs
             FROM can_files f
             LEFT JOIN device_can d
                    ON d.file_id = f.file_id AND d.file_id IS NOT NULL
            WHERE f.make IS NOT NULL AND f.make != ''
            GROUP BY f.make
            ORDER BY installs DESC, f.make""").fetchall()

    from .fallback import is_generic
    return [dict(r) for r in rows if not is_generic(r["make"])]


def _as_list(value) -> list[str]:
    """Accept one value or several, from a string or a list."""
    if value is None:
        return []
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",")]
    else:
        parts = [str(p).strip() for p in value]
    return [p for p in parts if p]


def browse_models(conn, makes) -> list[dict]:
    """Models across one or several makes.

    Several, because a fleet buyer compares: "we run MAN and Scania, what
    do you cover?" is one question, not two, and answering it as two loses
    the comparison that was the point.
    """
    picked = _as_list(makes)
    if not picked:
        return []
    marks = ",".join("?" * len(picked))
    rows = conn.execute(
        f"""SELECT f.make, f.model,
                   MIN(f.year_from) AS year_from,
                   MAX(CASE WHEN f.year_to IS NULL THEN 9999 ELSE f.year_to END) AS year_to,
                   COUNT(DISTINCT f.vmid) AS buses,
                   COUNT(DISTINCT d.imei) AS installs
              FROM can_files f
              LEFT JOIN device_can d
                     ON d.file_id = f.file_id AND d.file_id IS NOT NULL
             WHERE upper(f.make) IN ({marks})
               AND f.model IS NOT NULL AND f.model != ''
             GROUP BY f.make, f.model
             ORDER BY installs DESC, f.make, f.model""",
        [m.upper() for m in picked]).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["year_to"] = None if d["year_to"] == 9999 else d["year_to"]
        d["label"] = f"{d['make']} {d['model']}".strip()
        out.append(d)
    return out


def browse_years(conn, makes, models=None) -> list[dict]:
    """Individual years, newest first, each with what is fitted that year.

    The catalogue stores ranges. A person asking about a 2019 truck should
    not have to read "[14+]" and work out that it covers them.
    """
    mk, md = _as_list(makes), _as_list(models)
    if not mk:
        return []
    where = ["upper(f.make) IN (" + ",".join("?" * len(mk)) + ")"]
    params = [m.upper() for m in mk]
    if md:
        where.append("upper(f.model) IN (" + ",".join("?" * len(md)) + ")")
        params += [m.upper() for m in md]

    rows = conn.execute(
        f"""SELECT f.file_id, f.year_from, f.year_to,
                   (SELECT COUNT(DISTINCT d.imei) FROM device_can d
                     WHERE d.file_id = f.file_id) AS installs
              FROM can_files f
             WHERE {' AND '.join(where)}""", params).fetchall()
    if not rows:
        return []

    spans = [(r["year_from"], r["year_to"], r["installs"] or 0) for r in rows]
    known = [s for s in spans if s[0]]
    if not known:
        return [{"year": None, "label": "كل السنوات", "installs":
                 max((s[2] for s in spans), default=0)}]

    lo = min(s[0] for s in known)
    hi = max((s[1] or _THIS_YEAR) for s in known)
    hi = min(hi, _THIS_YEAR + 1)

    out = []
    for y in range(hi, lo - 1, -1):
        fitted = sum(s[2] for s in known
                     if s[0] <= y and (s[1] is None or y <= s[1]))
        covered = any(s[0] <= y and (s[1] is None or y <= s[1]) for s in known)
        if covered:
            out.append({"year": y, "label": str(y), "installs": fitted})
    return out


def files_for(conn, makes, models, years=None) -> list:
    """The answer once the three lists have narrowed it down.

    One row per CAN bus, not every file that matches. A model can have
    dozens of catalogue entries across buses and revisions; handing all of
    them to a salesperson is the same as handing them nothing. For each
    bus, the file with the most real installs wins, because that is the one
    we can actually speak about -- ties go to the newest revision.
    """
    mk, md = _as_list(makes), _as_list(models)
    yrs = [int(y) for y in _as_list(years) if str(y).isdigit()]
    if not mk:
        return []

    where = ["upper(f.make) IN (" + ",".join("?" * len(mk)) + ")"]
    params = [m.upper() for m in mk]
    if md:
        where.append("upper(f.model) IN (" + ",".join("?" * len(md)) + ")")
        params += [m.upper() for m in md]

    rows = conn.execute(
        f"""SELECT f.*,
                   (SELECT COUNT(DISTINCT d.imei) FROM device_can d
                     WHERE d.file_id = f.file_id) AS installs
              FROM current_files f
             WHERE {' AND '.join(where)}""", params).fetchall()

    if yrs:
        rows = [r for r in rows
                if r["year_from"] is None
                or any(y >= r["year_from"]
                       and (r["year_to"] is None or y <= r["year_to"]) for y in yrs)]

    # One entry per vehicle per bus. A model can carry dozens of catalogue
    # rows across buses and revisions; handing all of them to a salesperson
    # is the same as handing them nothing.
    best: dict = {}
    for r in rows:
        key = (r["make"], r["model"], r["can_bus"])
        cur = best.get(key)
        if cur is None or ((r["installs"] or 0), r["revision"] or 0) > (
                (cur["installs"] or 0), cur["revision"] or 0):
            best[key] = r

    return sorted(best.values(),
                  key=lambda r: (-(r["installs"] or 0), r["make"] or "",
                                 r["model"] or "", r["can_bus"] or 99))


# ------------------------------------------------------------- migration

def _migrate(conn) -> None:
    """Move an old device_can aside rather than reshaping it.

    The v1 rows cannot be salvaged: they were keyed by element name, so a
    dual-bus device lost one of its two files, and they only ever recorded
    overrides, so an inherited assignment reads as absent. Converting them
    would carry both errors forward wearing a new schema.

    The old table is kept, not dropped. Diffing it against the next sweep
    is the clearest possible statement of what the tool was missing, and
    that is worth more than the disk it costs.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(device_can)")}
    if not cols or "bus" in cols:
        return
    stamp = conn.execute("SELECT strftime('%Y%m%d_%H%M%S','now')").fetchone()[0]
    conn.execute(f"ALTER TABLE device_can RENAME TO device_can_v1_{stamp}")
    conn.executescript(SCHEMA)


def record_device_bus(conn, imei: str, bus: int, *, element_name: str = "(none)",
                      element_id=None, file_id=None, raw_value=None,
                      inherited=None, port_function=None, hardware=None,
                      config_name=None, template_id=None,
                      last_activity=None) -> None:
    """Record one bus of one device. Bus 0 means nothing was assigned."""
    conn.execute(
        """INSERT INTO device_can
           (imei, bus, element_name, element_id, file_id, raw_value,
            inherited, port_function, hardware, config_name, template_id,
            last_activity, seen_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'))
           ON CONFLICT(imei, bus) DO UPDATE SET
             element_name=excluded.element_name,
             element_id=excluded.element_id, file_id=excluded.file_id,
             raw_value=excluded.raw_value, inherited=excluded.inherited,
             port_function=excluded.port_function,
             hardware=excluded.hardware, config_name=excluded.config_name,
             template_id=excluded.template_id,
             last_activity=excluded.last_activity, seen_at=datetime('now')""",
        (imei, int(bus), element_name or "(none)", element_id, file_id,
         raw_value, inherited, port_function, hardware, config_name,
         template_id, last_activity),
    )


def coverage_detail(conn) -> dict:
    """Coverage split by where the value came from.

    The inherited count is the headline: it is everything the previous
    reader was blind to.
    """
    row = conn.execute(
        """SELECT COUNT(DISTINCT imei) devices,
                  COUNT(DISTINCT CASE WHEN file_id IS NOT NULL THEN imei END) fitted,
                  SUM(file_id IS NOT NULL) buses,
                  SUM(file_id IS NOT NULL AND inherited = 1) inherited,
                  SUM(file_id IS NOT NULL AND inherited = 0) overridden
             FROM device_can""").fetchone()
    dual = conn.execute(
        """SELECT COUNT(*) n FROM (
             SELECT imei FROM device_can WHERE file_id IS NOT NULL
             GROUP BY imei HAVING COUNT(*) > 1)""").fetchone()["n"]
    orphan = conn.execute(
        """SELECT COUNT(*) n FROM device_can d
            WHERE d.file_id IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM can_files f
                               WHERE f.file_id = d.file_id)""").fetchone()["n"]
    total = row["devices"] or 0
    fitted = row["fitted"] or 0
    return {
        "devices": total, "fitted": fitted, "unfitted": total - fitted,
        "pct": round(100 * fitted / total, 1) if total else 0.0,
        "bus_entries": row["buses"] or 0,
        "inherited": row["inherited"] or 0,
        "overridden": row["overridden"] or 0,
        "dual_bus_devices": dual,
        "pointing_at_missing_file": orphan,
    }


def record_device_script(conn, imei: str, slot: str, *, element_id=None,
                         script_id=None, raw_value=None, script_name=None,
                         inherited=None, hardware=None, config_name=None,
                         template_id=None) -> None:
    """Record one script slot of one device. Slot "(none)" means the
    device was read and carries no script anywhere."""
    conn.execute(
        """INSERT INTO device_script
           (imei, slot, element_id, script_id, raw_value, script_name,
            inherited, hardware, config_name, template_id, seen_at)
           VALUES (?,?,?,?,?,?,?,?,?,?, datetime('now'))
           ON CONFLICT(imei, slot) DO UPDATE SET
             element_id=excluded.element_id, script_id=excluded.script_id,
             raw_value=excluded.raw_value, script_name=excluded.script_name,
             inherited=excluded.inherited, hardware=excluded.hardware,
             config_name=excluded.config_name,
             template_id=excluded.template_id, seen_at=datetime('now')""",
        (imei, slot or "(none)", element_id, script_id, raw_value,
         script_name, inherited, hardware, config_name, template_id),
    )
