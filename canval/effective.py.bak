"""Which CAN file a device actually runs -- inherited values included.

WHY THIS MODULE EXISTS
----------------------
The first sweep read

    /api/external/v3/settingsOverrides/{uid}/overrides

and treated an empty list as "this device has no CAN file". 6151 of 6475
devices came back empty. They were not empty.

An override is only what somebody changed on that one device. The value
the device actually runs is the *effective* value, and it lives behind a
different path entirely:

    /api/external/v3/settingsOverrides/{uid}
        /categories/{cat}/elementGroups/{group}/elements/{element}

    -> {"overriden": false, "value": "3702", "defaultValue": "", ...}

`overriden: false` alongside a real value is the ordinary case: the file
is set in the configuration template and inherited by every device under
it. Reading overrides alone sees about 5% of the estate and reports the
other 95% as never fitted, which sends a technician out to look at a
working installation. That is the exact failure this project exists to
prevent, so the reader had to change.

TWO BUSES, ONE NAME
-------------------
CAN1 and CAN2 each carry an element called "Vehicle model", and they are
frequently different files -- one live device runs 2293 on CAN1 and 3733
on CAN2. The old table was keyed (imei, element_name), and since both
elements share that name the second row overwrote the first. The damage
was measurable: across the whole 6475-device sweep, not one device had
more than one row stored. Rows are now keyed by bus, and the element id
is what tells the two buses apart -- the name cannot.

A SLEEPING PORT IS STILL AN ASSIGNMENT
--------------------------------------
`CAN function` can be set to Sleep, and a model is often still assigned
underneath it. The question being answered is whether a file is assigned
anywhere in the fleet, so a sleeping port does not disqualify the row.
The function is recorded in its own column as context for whoever reads
the result, never as a filter.

THE SHORTCUT, AND THE PROOF IT DEMANDS
--------------------------------------
Walking the settings tree costs about a dozen calls, which is impossible
25000 times over. But the tree belongs to the template, not the device:
every device on template 38928 has its model element at id 10336482.
So the walk happens once per template and is cached, and the inherited
value is read once per template too.

Each device then costs a single call -- its own override list -- because
that list carries element ids, and element ids say which bus a value
belongs to. That is what makes a full sweep affordable.

The shortcut rests on one assumption: that two devices under the same
template inherit the same value. `verify_sample` tests it by re-reading
sampled devices the slow way and comparing. Mismatches are counted and
reported, never swallowed -- an unverified shortcut is how the sampling
bug got into the last version.

Measured on the first full family (8466 LX45-EA devices): 17359 calls,
515 templates, 0 verification mismatches, 0 read failures. 95% carried an
assigned file where the previous reader found 5%.

WHY THE TREE MAP IS CACHED IN THE DATABASE
------------------------------------------
Those 515 tree walks were about 40% of the calls in that run, and they
were thrown away when the process exited -- so a nightly resync paid for
them again every night to learn nothing new. Element ids belong to the
template, so the map is stored in `template_map` and only unfamiliar or
stale templates are walked. A resync then costs roughly one call per
device.

Cached ids can go stale if somebody restructures a template. A map older
than `map_max_age_days` is re-walked, and any element that has gone
missing shows up as an unreadable verification sample rather than as a
silently empty result.

WHAT MAKES A DEVICE WORTH RE-READING
------------------------------------
Re-reading 25000 devices to learn that 24900 of them are exactly as they
were last night is most of the cost of this tool for none of the value.

The obvious trigger -- "it has been active since we last read it" -- does
not work. A device that reports every day is active since every read, so
that rule re-reads the entire estate daily and is the same as having no
rule. Activity is about the vehicle moving; nothing about it says the
settings changed.

The signal that actually tracks an assignment change is the configuration
attached to the device, and it arrives free in the listing. A technician
who fits a CAN harness reassigns the configuration, and that shows up
here without a single extra call.

So a device is re-read when it is new, when its configuration name has
changed, or when the last read is older than `refresh_after_days`. The
age rule is the safety net for the case the configuration name cannot
see: someone editing an override inside an existing configuration.

DEVICES THAT LEAVE
------------------
A device removed from the account stops appearing in the listing, and its
rows would otherwise sit in the index forever, still being counted in
"fitted on 46 devices". A full sweep of a hardware family therefore drops
rows for devices of that family that the account no longer returns. A
partial run never prunes -- absence from a 300-device sample is not
absence from the account.

THREE WAYS TO HAVE NO FILE, AND ONLY ONE OF THEM IS AN ANSWER
-------------------------------------------------------------
"No CAN file" was one bucket, and it hid the difference between:

    no template       the device has no configuration at all
    no model set      the template has CAN ports, none carries a model
    no CAN branch     we could not find CAN ports in the template at all

The last one is not a fact about the vehicle, it is a failure to look.
This reader was built against LX45-EA, where the ports live under
Hardware > CAN1 | CAN2. The rest of the estate is 16000 older devices,
mostly XtCAN 2G, and if they name that branch anything else then every
one of them would report "no CAN file" -- a confident, wrong answer at
ten thousand times the scale of the bug this replaced.

So a template with no CAN branch is recorded under its own name, counted
separately, and the branch names that *were* found are kept, so the shape
mismatch announces itself in the first hundred devices instead of hiding
in a total.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .store import clear_device, connect, log_sweep, record_device_bus

BUS_NAMES = ("CAN1", "CAN2")
_ROOT = "/api/external/v3/settingsOverrides"

# The hardware families that carry a CAN interface, with the device counts
# found on 2026-08-04. The zero-device variants of the same families are
# left out deliberately: they cost a call each and answer nothing.
CAN_HARDWARE = {
    4: "XtCAN 2G",              # 10370
    98: "LX45-EA",              # 8466
    15: "StCAN XG3780M03",      # 5006
    16: "XtCAN XG3780M05",      # 1150
    89: "XtCAN XG3792M05",      # 69
    3: "StCAN 2G",              # 18
    12: "XtCAN 2G 32MB",        # 10
}

_CACHE_DDL = """
CREATE TABLE IF NOT EXISTS template_map (
    template_id INTEGER PRIMARY KEY,
    bus_map     TEXT    NOT NULL,   -- {"CAN1": {"model": [cat, group, element]}}
    has_can     INTEGER NOT NULL,
    branches    TEXT,               -- what the Hardware category really held
    reader      TEXT,               -- which reader version concluded this
    walked_at   TEXT DEFAULT (datetime('now'))
);
"""

# Bump this whenever the tree walk changes what it can recognise. A cached
# "no CAN ports here" is only as good as the reader that decided it, and
# one release of this reader wrote that verdict for 2347 devices that had
# CAN1 and CAN2 sitting right there. Maps that found something stay valid;
# empty ones from an older reader are walked again.
READER = "2"

# Recorded on the bus-0 row, so the reason survives in the data instead of
# living only in a counter somebody has to remember to read.
#
# The middle one was called "(no CAN ports in template)" and that reading
# was wrong. 2347 devices carried it while their tree plainly listed CAN1
# and CAN2, and opening one in the console settled it: the port is there,
# the Vehicle model entry under it is empty, and the console itself says
# "Nothing to see here". The element is only created when a file is
# assigned, so its absence is the absence of a file -- not the absence of
# a CAN interface.
#
# The distinction is the whole answer to a customer. "This device has no
# CAN port" is a hardware limit and ends the conversation. "This device
# has a CAN port with nothing assigned" is a job someone can do.
NO_TEMPLATE = "(no configuration)"
NO_CAN_PORTS = "(no CAN ports on this device)"
NO_MODEL = "(CAN port present, no file assigned)"

# What earlier versions wrote for the same states, so a database written
# by one of them can be corrected in place instead of re-swept.
_LEGACY_UNKNOWN = "(no CAN ports in template)"
_LEGACY_NO_MODEL = "(no model assigned)"


class _Counting:
    """Wraps the client so every call lands in the run's own tally.

    Counting at the call site was leaving the tree walks out, which made a
    sweep look three times cheaper than it is. A cost estimate that is
    quietly too low is how the next person decides a full run is fine.
    """

    def __init__(self, client, stats):
        self._client = client
        self._stats = stats

    def _request(self, *args, **kw):
        self._stats["calls"] += 1
        return self._client._request(*args, **kw)


# --------------------------------------------------------------- raw reads

def _get(client, path):
    """GET returning parsed JSON, or None when the call failed.

    Failure is deliberately not fatal here: one unreadable device must not
    stop a sweep of thousands. Everything that returns None is counted and
    logged as unread rather than stored as "no file", because the two mean
    very different things to whoever reads the answer.
    """
    try:
        return client._request("GET", path)
    except Exception:                                   # noqa: BLE001
        return None


def device_settings(client, uid):
    """(template_id, {element_id: value}) for one device, in one call.

    The element ids are the whole point. The endpoint ignores every filter
    parameter it is offered -- categoryId, groupId, elementGroupId all
    return the same complete list -- so filtering has to happen here, by
    id, against the template map.
    """
    data = _get(client, f"{_ROOT}/{uid}/overrides")
    if data is None:
        return None
    out = {}
    for o in data.get("overrides") or []:
        eid = o.get("elementId")
        if eid is not None:
            out[int(eid)] = str(o.get("value") or "").strip()
    return data.get("templateId"), out


def read_element(client, uid, where):
    cat, group, element = where
    return _get(client, f"{_ROOT}/{uid}/categories/{cat}"
                        f"/elementGroups/{group}/elements/{element}")


def _element_key(name):
    """Which of the two settings we care about an element is, if either."""
    low = str(name or "").lower()
    if "vehicle model" in low:
        return "model"
    if "can function" in low or low.strip() in ("function", "can function id"):
        return "func"
    return None


def _groups_under(client, uid, category_id, depth=0, max_depth=3):
    """Every element group beneath a category, however deep it sits.

    The first version of this looked for an intermediate category named
    "Vehicle model" and read the group inside it, because that is the
    shape LX45-EA uses. Older hardware nests differently -- some put the
    groups straight under CAN1 -- and against those the reader found the
    CAN1 branch, failed to recognise anything inside it, and wrote off
    2347 devices as having no CAN ports at all while its own diagnostic
    printed CAN1 and CAN2 in the list of branches it had just walked.

    So the shape is no longer assumed. Descend, collect every group, and
    decide from the element names, which are stable across generations.
    """
    body = _get(client, f"{_ROOT}/{uid}/categories/{category_id}") or {}
    for group in body.get("userElementGroups") or []:
        yield category_id, group["id"]
    if depth >= max_depth:
        return
    for sub in body.get("subCategories") or []:
        yield from _groups_under(client, uid, sub["id"], depth + 1, max_depth)


def map_template(client, uid):
    """Find the CAN elements in the settings tree.

    Returns (buses, branches) where buses is

        {"CAN1": {"model": (cat, group, element), "func": (...)}, ...}

    and branches is whatever the Hardware category actually contained.
    The second value only matters when the first is empty: it is the
    difference between "this device has no CAN ports" and "this reader
    does not recognise how this device names them", and only one of those
    is safe to report to a customer.

    Only the Hardware branch is walked. The full tree is nine categories
    deep in places and nothing else carries a vehicle model, so walking
    all of it would multiply the cost of every new template by six.
    """
    found = {}
    root = _get(client, f"{_ROOT}/{uid}") or {}
    names = [str(c.get("name")) for c in root.get("categories") or []]
    hardware = next((c["id"] for c in root.get("categories") or []
                     if str(c.get("name")) == "Hardware"), None)
    if hardware is None:
        return found, [f"top-level: {', '.join(names)}"] if names else []

    branches = _get(client, f"{_ROOT}/{uid}/categories/{hardware}") or {}
    seen_branches = [str(b.get("name"))
                     for b in branches.get("subCategories") or []]
    for branch in branches.get("subCategories") or []:
        bus = str(branch.get("name") or "")
        if bus not in BUS_NAMES:
            continue
        for cat_id, group_id in _groups_under(client, uid, branch["id"]):
            body = _get(client, f"{_ROOT}/{uid}/categories/{cat_id}"
                                f"/elementGroups/{group_id}") or {}
            for element in body.get("userElements") or []:
                key = _element_key(element.get("name"))
                if key and key not in found.get(bus, {}):
                    found.setdefault(bus, {})[key] = (
                        cat_id, group_id, element["id"])
    return found, seen_branches


def _dropdown(element):
    """value -> label for the CAN function options, e.g. {"2": "Sleep"}."""
    validation = ((element or {}).get("type") or {}).get("dropdownValidation") or {}
    return {str(o.get("value")): str(o.get("label"))
            for o in validation.get("options") or []}


# ------------------------------------------------------------- the sweep

def load_maps(conn, max_age_days=30):
    """Template maps learned by earlier runs, minus the stale ones.

    Returns (maps, has_ports). `has_ports` covers templates whose walk
    found no usable element but whose Hardware branch did list a CAN
    port -- the difference between no interface and an empty one.
    """
    conn.executescript(_CACHE_DDL)
    if "reader" not in {r[1] for r in conn.execute(
            "PRAGMA table_info(template_map)")}:
        conn.execute("ALTER TABLE template_map ADD COLUMN reader TEXT")
    rows = conn.execute(
        "SELECT template_id, bus_map, has_can, branches FROM template_map "
        "WHERE walked_at >= datetime('now', ?) "
        "  AND (has_can = 1 OR reader = ?)",
        (f"-{int(max_age_days)} days", READER)).fetchall()
    out, ports = {}, {}
    for r in rows:
        buses = json.loads(r["bus_map"])
        out[r["template_id"]] = {
            bus: {k: tuple(v) for k, v in keys.items()}
            for bus, keys in buses.items()}
        ports[r["template_id"]] = bool(buses) or any(
            b in (r["branches"] or "") for b in BUS_NAMES)
    return out, ports


def relabel_legacy(conn):
    """Correct rows written before the empty-port case was understood.

    The devices were read correctly; only the conclusion drawn from the
    reading was wrong. Rewriting the label is instant, where re-sweeping
    to fix a word would cost tens of thousands of calls.
    """
    with_ports = {r["template_id"] for r in conn.execute(
        "SELECT template_id, branches FROM template_map WHERE has_can = 0")
        if any(b in (r["branches"] or "") for b in BUS_NAMES)}
    moved = 0
    if with_ports:
        marks = ",".join("?" * len(with_ports))
        moved = conn.execute(
            f"UPDATE device_can SET element_name = ? "
            f"WHERE element_name = ? AND template_id IN ({marks})",
            (NO_MODEL, _LEGACY_UNKNOWN, *with_ports)).rowcount
    renamed = conn.execute(
        "UPDATE device_can SET element_name = ? WHERE element_name = ?",
        (NO_CAN_PORTS, _LEGACY_UNKNOWN)).rowcount
    renamed += conn.execute(
        "UPDATE device_can SET element_name = ? WHERE element_name = ?",
        (NO_MODEL, _LEGACY_NO_MODEL)).rowcount
    return moved, renamed


def save_map(conn, template_id, buses, branches):
    conn.execute(
        """INSERT INTO template_map (template_id, bus_map, has_can, branches,
                                     reader, walked_at)
           VALUES (?,?,?,?,?, datetime('now'))
           ON CONFLICT(template_id) DO UPDATE SET
             bus_map=excluded.bus_map, has_can=excluded.has_can,
             branches=excluded.branches, reader=excluded.reader,
             walked_at=datetime('now')""",
        (template_id, json.dumps({b: {k: list(v) for k, v in keys.items()}
                                  for b, keys in buses.items()}),
         1 if buses else 0, ", ".join(branches) or None, READER))


def sweep_devices(client, db_path, hardware_ids=None, active_since_days=60,
                  concurrency=6, verify_sample=2, limit=None, chunk=200,
                  map_max_age_days=30, refresh_after_days=7, force=False,
                  prune=True, progress=None, notice=None):
    """Read every device's effective CAN assignment and store it by bus.

    Work is chunked and written as it goes, so an interrupted run keeps
    everything it had finished and the next run simply carries on: a job
    nobody dares restart is the same as no job.

    By default only devices that need it are read -- new ones, ones whose
    configuration changed, and ones last read more than
    `refresh_after_days` ago. Pass force=True for a full re-read.
    """
    since = (int(time.time()) - active_since_days * 86400
             if active_since_days else None)

    stats = {
        "listed": 0, "devices": 0, "skipped_unchanged": 0, "pruned_gone": 0,
        "templates": 0,
        "templates_from_cache": 0, "templates_walked": 0, "calls": 0,
        "with_can_file": 0, "bus_entries": 0, "multi_bus_devices": 0,
        "inherited_entries": 0, "overridden_entries": 0,
        "no_file_assigned": 0, "no_configuration": 0, "no_can_ports": 0,
        "relabelled_rows": 0, "reclassified_as_empty_port": 0,
        "unread": 0, "errors": 0,
        "verified": 0, "verify_mismatches": 0, "verify_unreadable": 0,
        "verify_problems": [], "shape_warnings": [],
        "mode": "effective",
    }
    api = _Counting(client, stats)
    listed: list = []

    with connect(db_path) as conn:
        trees, ports = load_maps(conn, map_max_age_days)
        fixed, renamed = relabel_legacy(conn)
        stats["relabelled_rows"] = fixed + renamed
        stats["reclassified_as_empty_port"] = fixed
        fresh = {}
        if not force:
            fresh = {r["imei"]: r["config_name"] for r in conn.execute(
                """SELECT imei, config_name, MAX(seen_at) FROM device_can
                    WHERE seen_at >= datetime('now', ?)
                    GROUP BY imei""", (f"-{int(refresh_after_days)} days",))}
    stats["templates_from_cache"] = len(trees)

    devices = []
    for dev in client.iter_devices(hardware_ids=hardware_ids,
                                   last_activity_from=since):
        settings = dev.get("settings") or {}
        info = dev.get("information") or {}
        uid = settings.get("uid")
        if not uid:
            continue
        cfg = settings.get("configuration") or {}
        config = cfg.get("currentConfigName")
        hardware = (settings.get("hardware") or {}).get("name")
        listed.append((uid, hardware))

        # Unchanged configuration plus a recent read means nothing about
        # this device's CAN assignment can have moved that we would see.
        if uid in fresh and fresh[uid] == config:
            stats["skipped_unchanged"] += 1
            continue

        devices.append({
            "imei": uid, "hardware": hardware, "config": config,
            "last_activity": (info.get("activityUpdate") or {}).get("lastActivity"),
        })
        if limit and len(devices) >= limit:
            break

    stats["listed"] = len(listed)
    stats["devices"] = len(devices)

    # A device the account no longer returns must not keep being counted
    # as a live installation. Only a complete run may conclude that.
    if prune and not limit:
        families = {hw for _, hw in listed if hw}
        keep = {uid for uid, _ in listed}
        if families and keep:
            with connect(db_path) as conn:
                marks = ",".join("?" * len(families))
                gone = [r["imei"] for r in conn.execute(
                    f"SELECT DISTINCT imei FROM device_can "
                    f"WHERE hardware IN ({marks})", tuple(families))
                    if r["imei"] not in keep]
                for imei in gone:
                    conn.execute("DELETE FROM device_can WHERE imei=?", (imei,))
                    conn.execute("DELETE FROM sweep_log WHERE imei=?", (imei,))
                stats["pruned_gone"] = len(gone)

    if not devices:
        return stats

    defaults: dict = {}     # (template_id, bus, key) -> inherited value
    labels: dict = {}       # CAN function value -> label
    seen: dict = {}         # imei -> (template_id, {element_id: value})
    resolved: dict = {}     # imei -> {bus: raw value}
    shapes: dict = {}       # template_id -> branch names, when no CAN found
    done = 0

    for start in range(0, len(devices), chunk):
        batch = devices[start:start + chunk]
        got, failed = {}, set()

        # -- one call per device, in parallel: template id + its overrides
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(device_settings, api, d["imei"]): d
                       for d in batch}
            for fut in as_completed(futures):
                dev = futures[fut]
                try:
                    result = fut.result()
                except Exception:                       # noqa: BLE001
                    result = None
                if result is None:
                    failed.add(dev["imei"])
                    stats["errors"] += 1
                else:
                    got[dev["imei"]] = result

        seen.update(got)

        # -- walk each unfamiliar template once, then keep the map
        #
        # In parallel, because this was the slow half of a run and it was
        # running one template at a time: a family with 100 unfamiliar
        # templates spent over a thousand sequential calls before the
        # first device was resolved, which reads as a hang.
        unknown = {}
        for imei, (tid, _) in got.items():
            if tid is not None and tid not in trees and tid not in unknown:
                unknown[tid] = imei
        if unknown:
            if notice:
                notice(f"    walking {len(unknown)} new template(s)")
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                jobs = {pool.submit(map_template, api, imei): tid
                        for tid, imei in unknown.items()}
                walked = {}
                for fut in as_completed(jobs):
                    tid = jobs[fut]
                    try:
                        buses, branches = fut.result()
                    except Exception:                   # noqa: BLE001
                        continue
                    walked[tid] = (buses, branches)
            with connect(db_path) as conn:
                for tid, (buses, branches) in walked.items():
                    trees[tid] = buses
                    ports[tid] = bool(buses) or any(
                        b in branches for b in BUS_NAMES)
                    stats["templates_walked"] += 1
                    save_map(conn, tid, buses, branches)
                    if not buses:
                        shapes[tid] = branches

        members: dict = {}
        for imei, (tid, overrides) in got.items():
            members.setdefault(tid, []).append((imei, overrides))

        # -- learn each template's inherited values from a device that
        #    does not override them: whatever it reports is the template's
        for tid, tree in ((t_, trees.get(t_) or {}) for t_ in members):
            for bus, keys in tree.items():
                for key, where in keys.items():
                    if (tid, bus, key) in defaults:
                        continue
                    element = where[2]
                    donor = next((i for i, o in members[tid]
                                  if element not in o), None)
                    if donor is None:
                        continue
                    body = read_element(api, donor, where)
                    if body is None:
                        continue
                    defaults[(tid, bus, key)] = str(body.get("value") or "").strip()
                    if key == "func" and not labels:
                        labels.update(_dropdown(body))

        # -- resolve and store
        with connect(db_path) as conn:
            for dev in batch:
                imei = dev["imei"]
                if imei in failed:
                    stats["unread"] += 1
                    log_sweep(conn, imei, "read_failed",
                              "settings unreadable: status unknown, not "
                              "stored as absent")
                    continue

                tid, overrides = got[imei]
                tree = trees.get(tid) or {}
                clear_device(conn, imei)
                written = 0
                resolved[imei] = {}

                for bus_name in BUS_NAMES:
                    keys = tree.get(bus_name) or {}
                    where = keys.get("model")
                    if not where:
                        continue
                    element = where[2]
                    if element in overrides:
                        raw, inherited = overrides[element], 0
                    else:
                        raw, inherited = defaults.get((tid, bus_name, "model")), 1
                    raw = str(raw or "").strip()
                    resolved[imei][bus_name] = raw
                    if not raw.isdigit() or int(raw) <= 0:
                        continue

                    function = None
                    fwhere = keys.get("func")
                    if fwhere:
                        fid = fwhere[2]
                        fval = (overrides[fid] if fid in overrides
                                else defaults.get((tid, bus_name, "func")))
                        if fval is not None:
                            function = labels.get(str(fval), str(fval))

                    record_device_bus(
                        conn, imei, 1 if bus_name == "CAN1" else 2,
                        element_name=f"{bus_name} Vehicle model",
                        element_id=element, file_id=int(raw), raw_value=raw,
                        inherited=inherited, port_function=function,
                        hardware=dev["hardware"], config_name=dev["config"],
                        template_id=tid, last_activity=dev["last_activity"])
                    written += 1
                    stats["bus_entries"] += 1
                    stats["inherited_entries" if inherited
                          else "overridden_entries"] += 1

                if written:
                    stats["with_can_file"] += 1
                    if written > 1:
                        stats["multi_bus_devices"] += 1
                    log_sweep(conn, imei, "indexed",
                              ",".join(f"{b}={v}" for b, v
                                       in resolved[imei].items() if v))
                    continue

                # Nothing assigned -- but say which kind of nothing.
                if tid is None:
                    reason, key = NO_TEMPLATE, "no_configuration"
                elif not tree and not ports.get(tid):
                    reason, key = NO_CAN_PORTS, "no_can_ports"
                else:
                    # Either the port exists with an empty Vehicle model
                    # entry, or the entry exists holding nothing. Both are
                    # the same fact to whoever asked: fittable, not fitted.
                    reason, key = NO_MODEL, "no_file_assigned"
                stats[key] += 1
                record_device_bus(
                    conn, imei, 0, element_name=reason,
                    hardware=dev["hardware"], config_name=dev["config"],
                    template_id=tid, last_activity=dev["last_activity"])
                log_sweep(conn, imei, key, reason)

        done += len(batch)
        if progress:
            progress(done, len(devices))

    stats["templates"] = len({t_ for t_, _ in seen.values() if t_ is not None})

    # A template with no CAN ports is either a device that genuinely has
    # none, or this reader failing to recognise how that hardware names
    # them. Carry the evidence out so the difference can be settled by
    # looking, not by assuming.
    if shapes:
        empty = {t_: b for t_, b in shapes.items()
                 if any(x in BUS_NAMES for x in b)}
        stats["templates_with_empty_can_port"] = len(empty)
        stats["templates_without_can_ports"] = len(shapes) - len(empty)
        # A template with no Hardware category at all is neither of those
        # and has never been explained, so it still gets said out loud.
        for tid, branches in shapes.items():
            if any(str(b).startswith("top-level:") for b in branches):
                if len(stats["shape_warnings"]) < 10:
                    stats["shape_warnings"].append(
                        f"template {tid}: no Hardware category at all. "
                        f"{'; '.join(branches)}")

    # -- prove the shortcut, on a sample, the slow way
    if verify_sample:
        for tid, tree in trees.items():
            picks = [i for i, (t_, _) in seen.items()
                     if t_ == tid][:verify_sample]
            for imei in picks:
                for bus_name, keys in tree.items():
                    where = keys.get("model")
                    if not where:
                        continue
                    body = read_element(api, imei, where)
                    if body is None:
                        # A cached id that no longer reads is a stale map,
                        # not a device without a file.
                        stats["verify_unreadable"] += 1
                        if len(stats["verify_problems"]) < 20:
                            stats["verify_problems"].append(
                                f"{imei} {bus_name}: cached element "
                                f"{where[2]} no longer readable (template "
                                f"{tid}) -- the map may be stale")
                        continue
                    live = str(body.get("value") or "").strip()
                    ours = str((resolved.get(imei) or {}).get(bus_name) or "")
                    stats["verified"] += 1
                    if live != ours:
                        stats["verify_mismatches"] += 1
                        if len(stats["verify_problems"]) < 20:
                            stats["verify_problems"].append(
                                f"{imei} {bus_name}: stored {ours!r}, "
                                f"device reports {live!r} (template {tid})")

    return stats
