"""The service behind the web app.

WHY A SERVER AT ALL
-------------------
The obvious shape for an internal tool is a static page on GitHub Pages
talking straight to the two platforms. That cannot work here, and not for
a technical reason: the XDM client secret and the tracking token would
have to ship to the browser, where anyone on the team could lift them and
use them outside the tool. Both credentials see every customer's fleet.

So the browser talks only to this service, this service holds the
credentials, and the answer it returns has already been stripped of
anything the asker has no business seeing.

Built on the standard library on purpose: `python -m canval.server` and it
runs. No framework to install on whatever machine ends up hosting it.

    python -m canval.server --port 8000

AUTH
----
Set CANVAL_REQUIRE_AUTH=1 and CANVAL_FIREBASE_PROJECT to verify Firebase
ID tokens. Without it the service trusts every caller, which is only safe
on a machine nothing else can reach. It refuses to bind to a public
interface unless auth is on.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .arabic import translate_all, untranslated
from .coverage import ABSENT, NEVER, PROVEN, UNCHECKABLE, UNKNOWN, compare
from .fallback import TRIAL_CHECKLIST, advise, normalise
from .store import (connect, devices_for_vmid, record_trial, revisions_for_vmid,
                    browse_makes, browse_models, browse_years, files_for,
                    last_sync, search_can_files, suggest, sweep_coverage,
                    top_makes, trials_for, whats_new)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


# ------------------------------------------------------------------- auth

class FirebaseVerifier:
    """Verifies Firebase ID tokens against Google's published keys.

    Signature checking needs a crypto library. Where none is available the
    verifier refuses to run rather than waving tokens through -- a check
    that always passes is worse than no check, because it looks like one.
    """

    CERTS = ("https://www.googleapis.com/robot/v1/metadata/x509/"
             "securetoken@system.gserviceaccount.com")

    def __init__(self, project_id: str):
        self.project_id = project_id
        self._certs: dict = {}
        self._fetched = 0.0
        self._lock = threading.Lock()
        try:
            import jwt                                    # noqa: F401
            self._jwt = jwt
        except ImportError as exc:
            raise RuntimeError(
                "Firebase verification needs PyJWT with crypto support:\n"
                "    pip install 'pyjwt[crypto]'\n"
                "Refusing to start with auth required but unverifiable."
            ) from exc

    def _keys(self):
        with self._lock:
            if self._certs and time.time() - self._fetched < 3600:
                return self._certs
            import requests
            from cryptography.x509 import load_pem_x509_certificate

            resp = requests.get(self.CERTS, timeout=15)
            resp.raise_for_status()
            self._certs = {
                kid: load_pem_x509_certificate(pem.encode()).public_key()
                for kid, pem in resp.json().items()
            }
            self._fetched = time.time()
            return self._certs

    def verify(self, token: str) -> dict:
        header = self._jwt.get_unverified_header(token)
        key = self._keys().get(header.get("kid"))
        if key is None:
            raise ValueError("unknown signing key")
        return self._jwt.decode(
            token, key, algorithms=["RS256"],
            audience=self.project_id,
            issuer=f"https://securetoken.google.com/{self.project_id}",
        )


# ---------------------------------------------------------------- answers

def _sensors_for(conn, file_id, source):
    rows = conn.execute(
        """SELECT sensor_name, status FROM can_sensors
           WHERE file_id=? AND source=? ORDER BY sensor_name""",
        (file_id, source)).fetchall()
    return ([r["sensor_name"] for r in rows if r["status"] == "declared"],
            [{"name": r["sensor_name"], "status": r["status"]}
             for r in rows if r["status"] != "declared"])


def _devices_seen(evidences) -> list[dict]:
    """The devices a verdict was read from, so it can be checked by hand.

    Only ones that actually returned readings: an unreachable device tells
    you nothing and sending someone to look it up wastes their time. The
    unit id is included because that is what the tracking platform's own
    URL wants, which saves a search.
    """
    from .monitor import LIVE, PROVEN as LIVE_PROVEN

    out = []
    for ev in evidences:
        if ev.error or not ev.readings:
            continue
        live = [r for r in ev.readings if r.verdict in (LIVE, LIVE_PROVEN)]
        out.append({
            "imei": ev.imei,
            "unit_id": ev.unit_id,
            "live": len(live),
            "last_report": ev.last_report,
            "signals": sorted({r.display_name for r in live if r.display_name})[:6],
        })
    out.sort(key=lambda d: -d["live"])
    return out


def _row_to_card(conn, row) -> dict:
    declared, unusable = _sensors_for(conn, row["file_id"], row["source"])
    revs = revisions_for_vmid(conn, row["vmid"]) if row["vmid"] else []
    newest = max((r["revision"] or 0) for r in revs) if revs else None
    installs = devices_for_vmid(conn, row["vmid"]) if row["vmid"] else []
    outdated = [d for d in installs
                if newest is not None and (d["revision"] or 0) < newest]

    return {
        "file_id": row["file_id"],
        "vmid": row["vmid"],
        "name": row["name"],
        "variant": row["variant"],
        "raw_model": row["raw_model"],
        "year_from": row["year_from"],
        "year_to": row["year_to"],
        "can_bus": row["can_bus"],
        "bitrate_kbps": row["bitrate_kbps"],
        "obd_pins": row["obd_pins"],
        "pins": [int(p) for p in re.findall(r"\d+", row["obd_pins"] or "")],
        "revision": row["revision"],
        "revisions": len(revs),
        "newest_revision": newest,
        "manual_url": row["manual_url"],
        "declared": declared,
        "declared_ar": translate_all(declared),
        "unusable": unusable,
        "installs": len(installs),
        "outdated_installs": len(outdated),
    }


def build_answer(conn, query: str, year: int | None, adapter,
                 max_devices: int = 8, limit: int = 6) -> dict:
    rows = search_can_files(conn, normalise(query), year)
    past = [dict(t) for t in trials_for(conn, query)]

    if not rows:
        fb = advise(conn, query)
        return {
            "query": query, "year": year, "found": 0,
            "verdict": "no_file",
            "vehicle_class": fb.verdict,
            "generic": [{"name": g["name"], "vmid": g["vmid"],
                         "bitrate_kbps": g["bitrate_kbps"]}
                        for g in fb.generic_files[:6]],
            "notes": fb.notes,
            "checklist": TRIAL_CHECKLIST,
            "trials": past,
        }

    cards = []
    for row in rows[:limit]:
        card = _row_to_card(conn, row)
        card["coverage"] = None

        installs = devices_for_vmid(conn, row["vmid"]) if row["vmid"] else []
        if installs and adapter is not None:
            from .monitor import evaluate_device
            evidences = [evaluate_device(adapter, d["imei"])
                         for d in installs[:max_devices]]
            cmp_ = compare(card["declared"], evidences)
            works = [v.declared_name for v in cmp_.by_status(PROVEN)]
            fails = [v.declared_name for v in cmp_.by_status(ABSENT, NEVER)]
            card["coverage"] = {
                "checked": cmp_.devices_checked,
                "of": len(installs),
                # Sales read these out to a customer, so they arrive
                # translated. The English original travels alongside for
                # anyone in R&D reading the same screen.
                "works": translate_all(works),
                "fails": translate_all(fails),
                "unproven": translate_all(
                    [v.declared_name for v in cmp_.by_status(UNKNOWN)]),
                "uncheckable": translate_all(
                    [v.declared_name for v in cmp_.by_status(UNCHECKABLE)]),
                "missing_terms": untranslated(works + fails),
                # The devices the verdict rests on. Without them nobody can
                # check the tool's working -- they would have to take
                # "proven" on faith, which is exactly the habit this was
                # built to break. They stay behind the technical fold: a
                # screenshot of this screen can reach a customer, and these
                # are other customers' vehicles.
                "devices": _devices_seen(evidences),
            }
        cards.append(card)

    best = cards[0]
    cov = best.get("coverage")
    if cov and cov["works"]:
        verdict = "proven"
    elif best["installs"]:
        verdict = "fitted_unproven"
    else:
        verdict = "catalogue_only"

    return {
        "query": query, "year": year,
        "found": len(rows), "shown": len(cards),
        "verdict": verdict,
        "results": cards,
        "trials": past,
        "checklist": TRIAL_CHECKLIST,
    }


def build_answer_for(conn, makes, models, years, adapter,
                     max_devices: int = 8, cap: int = 12) -> dict:
    """The same answer the search produces, reached by picking from lists.

    Several vehicles can be selected at once, so the reply is a list of
    cards. It is capped: a buyer who ticks every make wants a comparison,
    not two hundred cards nobody will read to the end of.
    """
    from .store import _as_list

    rows = files_for(conn, makes, models, years)[:cap]
    parts = [", ".join(_as_list(makes)), ", ".join(_as_list(models))]
    label = " ".join(p for p in parts if p) or "الاختيار"
    year = next((int(y) for y in _as_list(years) if str(y).isdigit()), None)

    if not rows:
        return {"query": label, "year": year, "found": 0,
                "verdict": "no_file", "vehicle_class": "unclear",
                "generic": [], "notes": [], "checklist": TRIAL_CHECKLIST,
                "trials": []}

    cards = []
    for row in rows:
        card = _row_to_card(conn, row)
        card["coverage"] = None
        installs = devices_for_vmid(conn, row["vmid"]) if row["vmid"] else []
        if installs and adapter is not None:
            from .monitor import evaluate_device
            evidences = [evaluate_device(adapter, d["imei"])
                         for d in installs[:max_devices]]
            cmp_ = compare(card["declared"], evidences)
            works = [v.declared_name for v in cmp_.by_status(PROVEN)]
            fails = [v.declared_name for v in cmp_.by_status(ABSENT, NEVER)]
            card["coverage"] = {
                "checked": cmp_.devices_checked, "of": len(installs),
                "works": translate_all(works), "fails": translate_all(fails),
                "unproven": translate_all(
                    [v.declared_name for v in cmp_.by_status(UNKNOWN)]),
                "uncheckable": translate_all(
                    [v.declared_name for v in cmp_.by_status(UNCHECKABLE)]),
                "missing_terms": untranslated(works + fails),
                "devices": _devices_seen(evidences),
            }
        cards.append(card)

    best = cards[0]
    cov = best.get("coverage")
    verdict = ("proven" if cov and cov["works"]
               else "fitted_unproven" if best["installs"]
               else "catalogue_only")
    return {"query": label, "year": year, "found": len(cards),
            "shown": len(cards), "verdict": verdict, "results": cards,
            "trials": [], "checklist": TRIAL_CHECKLIST}


# ---------------------------------------------------------------- handler

class Handler(BaseHTTPRequestHandler):
    server_version = "canval"
    db_path = "canval.db"
    verifier: FirebaseVerifier | None = None
    adapter = None

    def log_message(self, fmt, *args):
        print(f"  {self.address_string()}  {fmt % args}")

    # ------------------------------------------------------------ helpers

    def _send(self, code: int, payload, ctype="application/json"):
        if ctype == "application/json":
            body = json.dumps(payload, ensure_ascii=False, default=str).encode()
        else:
            body = payload if isinstance(payload, bytes) else str(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8"
                                                  if "json" in ctype or "html" in ctype
                                                  else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _identity(self):
        """Returns the caller, or None. Raises on a bad token."""
        if self.verifier is None:
            return "anonymous"
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return None
        try:
            claims = self.verifier.verify(auth[7:])
        except Exception:                                  # noqa: BLE001
            return None
        return claims.get("email") or claims.get("uid")

    # -------------------------------------------------------------- routes

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        path, qs = url.path, urllib.parse.parse_qs(url.query)

        if path == "/api/health":
            with connect(self.db_path) as conn:
                cov = sweep_coverage(conn)
                files = conn.execute(
                    "SELECT COUNT(*) n FROM can_files").fetchone()["n"]
            return self._send(200, {"ok": True, "can_files": files,
                                    "coverage": cov,
                                    "auth": self.verifier is not None,
                                    "live_evidence": self.adapter is not None})

        if path.startswith("/api/"):
            who = self._identity()
            if who is None:
                return self._send(401, {"error": "Sign in to continue."})

            if path == "/api/start":
                # Everything the empty screen needs, in one call: what the
                # fleet is made of, and what became supported since the
                # last sync.
                try:
                    with connect(self.db_path) as conn:
                        return self._send(200, {
                            "makes": top_makes(conn, limit=12),
                            "new": whats_new(conn, days=14, limit=12),
                            "last_sync": last_sync(conn),
                        })
                except sqlite3.Error as exc:
                    return self._send(500, {"error": f"Database: {exc}"})

            # The three lists. Picking from them cannot be misspelt, which
            # is why they exist alongside the search box rather than
            # instead of it.
            if path == "/api/makes":
                with connect(self.db_path) as conn:
                    return self._send(200, {"makes": browse_makes(conn)})

            if path == "/api/models":
                makes = (qs.get("make") or [""])[0]
                if not makes.strip():
                    return self._send(400, {"error": "Pick a make first."})
                with connect(self.db_path) as conn:
                    return self._send(200, {"models": browse_models(conn, makes)})

            if path == "/api/years":
                makes = (qs.get("make") or [""])[0]
                models = (qs.get("model") or [""])[0]
                if not makes.strip():
                    return self._send(400, {"error": "Pick a make first."})
                with connect(self.db_path) as conn:
                    return self._send(200,
                                      {"years": browse_years(conn, makes, models)})

            if path == "/api/answer":
                makes = (qs.get("make") or [""])[0]
                models = (qs.get("model") or [""])[0]
                years = (qs.get("year") or [""])[0]
                if not makes.strip():
                    return self._send(400, {"error": "Pick a make first."})
                with connect(self.db_path) as conn:
                    return self._send(200, build_answer_for(
                        conn, makes, models, years, self.adapter))

            if path == "/api/suggest":
                q = (qs.get("q") or [""])[0].strip()
                if len(q) < 2:
                    return self._send(200, {"suggestions": []})
                try:
                    with connect(self.db_path) as conn:
                        return self._send(200, {"q": q,
                                                "suggestions": suggest(conn, q)})
                except sqlite3.Error as exc:
                    return self._send(500, {"error": f"Database: {exc}"})

            if path == "/api/search":
                q = (qs.get("q") or [""])[0].strip()
                if not q:
                    return self._send(400, {"error": "Type a make and model."})
                year = qs.get("year", [None])[0]
                try:
                    year = int(year) if year else None
                except ValueError:
                    year = None
                try:
                    with connect(self.db_path) as conn:
                        return self._send(200, build_answer(
                            conn, q, year, self.adapter))
                except sqlite3.Error as exc:
                    return self._send(500, {"error": f"Database: {exc}"})

            return self._send(404, {"error": "No such endpoint."})

        # static
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEB_DIR / name).resolve()
        if not str(target).startswith(str(WEB_DIR.resolve())) or not target.is_file():
            return self._send(404, "Not found", "text/plain")
        ctype = {"html": "text/html", "js": "text/javascript",
                 "css": "text/css", "json": "application/json",
                 "svg": "image/svg+xml", "png": "image/png",
                 "webmanifest": "application/manifest+json",
                 }.get(target.suffix.lstrip("."), "application/octet-stream")
        return self._send(200, target.read_bytes(), ctype)

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        who = self._identity()
        if who is None:
            return self._send(401, {"error": "Sign in to continue."})

        if url.path != "/api/trial":
            return self._send(404, {"error": "No such endpoint."})

        length = int(self.headers.get("Content-Length") or 0)
        if length > 100_000:
            return self._send(413, {"error": "That is too much text."})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, {"error": "Could not read that."})

        if not body.get("query") or not body.get("outcome"):
            return self._send(400, {
                "error": "A trial needs the vehicle and the outcome."})

        body["recorded_by"] = who
        for key in ("signals_ok", "signals_bad"):
            if isinstance(body.get(key), list):
                body[key] = ", ".join(str(x) for x in body[key])
        with connect(self.db_path) as conn:
            new_id = record_trial(conn, **{
                k: body.get(k) for k in
                ("query", "make_model", "year", "file_id", "can_bus", "pins",
                 "bitrate_kbps", "outcome", "signals_ok", "signals_bad",
                 "dealer_enable", "notes", "recorded_by")})
        return self._send(201, {"id": new_id, "recorded_by": who})


def build_adapter():
    token = os.environ.get("AFAQY_TOKEN")
    if not token:
        return None
    try:
        from .afaqy import AfaqyAdapter
        return AfaqyAdapter(token=token)
    except Exception:                                      # noqa: BLE001
        return None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="canval.server")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--db", default=os.environ.get("CANVAL_DB", "canval.db"))
    args = p.parse_args(argv)

    require_auth = os.environ.get("CANVAL_REQUIRE_AUTH") == "1"
    project = os.environ.get("CANVAL_FIREBASE_PROJECT")

    verifier = None
    if require_auth:
        if not project:
            print("CANVAL_REQUIRE_AUTH=1 needs CANVAL_FIREBASE_PROJECT.")
            return 2
        verifier = FirebaseVerifier(project)

    # Binding to every interface with no auth would put the whole fleet on
    # the network for anyone who finds the port.
    if args.host not in ("127.0.0.1", "localhost") and verifier is None:
        print("Refusing to listen on a public interface without auth.\n"
              "Set CANVAL_REQUIRE_AUTH=1 and CANVAL_FIREBASE_PROJECT, or "
              "bind to 127.0.0.1 and put a proxy in front.")
        return 2

    Handler.db_path = args.db
    Handler.verifier = verifier
    Handler.adapter = build_adapter()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"\n  canval  http://{args.host}:{args.port}")
    print(f"  database      {args.db}")
    print(f"  auth          {'Firebase' if verifier else 'OFF (local only)'}")
    print(f"  live evidence {'on' if Handler.adapter else 'off (no AFAQY_TOKEN)'}\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
