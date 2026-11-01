"""One place that hands the nightly job a working platform token.

The token the browser issues lives thirty days, and twice now the job ran
for weeks on an expired one -- green tick every night, live readings and
door commands frozen underneath. So the job no longer depends on anyone
pasting a token in on time:

    1. AFAQY_TOKEN, if it is set and not yet expired, is used as it is.
    2. Otherwise, with AFAQY_USERNAME and AFAQY_PASSWORD set, the job logs
       in itself through POST /auth/login -- the same call the web app
       makes, no OTP -- and uses the token it gets back for this run.

Nothing here prints a password or a token. The login answer is searched
for the token rather than read from one assumed field, because the two
hosts the platform answers on do not wrap it the same way, and a login
that "succeeds" but hands back nothing usable must say so, loudly.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time

HOSTS = ("https://api.afaqy.pro", "https://api.afaqy.sa")
_JWT = re.compile(r"^eyJ[\w-]+\.[\w-]+\.[\w-]+$")


def token_state(token: str) -> tuple[bool, str]:
    """Read the expiry out of the token itself before spending calls."""
    try:
        body = token.split(".")[1]
        body += "=" * (-len(body) % 4)
        exp = int(json.loads(base64.urlsafe_b64decode(body)).get("exp", 0))
    except Exception:                                   # noqa: BLE001
        return True, "token is not readable, trying anyway"
    left = exp - int(time.time())
    when = time.strftime("%Y-%m-%d", time.gmtime(exp))
    if left <= 0:
        return False, f"token expired on {when}"
    return True, f"token valid until {when} ({left // 86400} days left)"


def _find_jwt(obj):
    """The first JWT-shaped string anywhere in a JSON answer."""
    if isinstance(obj, str):
        return obj if _JWT.match(obj.strip()) else None
    if isinstance(obj, dict):
        for k in ("token", "access_token", "accessToken"):
            hit = _find_jwt(obj.get(k))
            if hit:
                return hit
        obj = list(obj.values())
    if isinstance(obj, list):
        for v in obj:
            hit = _find_jwt(v)
            if hit:
                return hit
    return None


def login(username: str, password: str, session=None) -> tuple[str | None, str]:
    """Log in and return (token, note). Tries both hosts and both body
    shapes the platform accepts: the web app sends the payload as a JSON
    string inside `data`, the integration documentation as an object."""
    import requests
    s = session or requests.Session()
    creds = {"username": username, "password": password}
    tried = []
    for host in HOSTS:
        for body in ({"data": json.dumps(creds)}, {"data": creds}):
            try:
                r = s.post(f"{host}/auth/login", json=body, timeout=60,
                           headers={"Accept": "application/json",
                                    "Content-Type": "application/json"})
            except Exception as exc:                    # noqa: BLE001
                tried.append(f"{host}: {type(exc).__name__}")
                continue
            if r.ok:
                try:
                    tok = _find_jwt(r.json())
                except ValueError:
                    tok = None
                if tok:
                    alive, note = token_state(tok)
                    if alive:
                        return tok, f"logged in on {host}; {note}"
                tried.append(f"{host}: 200 but no usable token")
            else:
                tried.append(f"{host}: HTTP {r.status_code}")
    return None, "login failed (" + "; ".join(dict.fromkeys(tried)) + ")"


def get_token() -> tuple[str | None, str]:
    """A token for this run, or None with the reason."""
    tok = os.environ.get("AFAQY_TOKEN", "").strip()
    if tok:
        alive, note = token_state(tok)
        if alive:
            return tok, note
        stale = note
    else:
        stale = "no AFAQY_TOKEN set"
    user = os.environ.get("AFAQY_USERNAME", "").strip()
    pw = os.environ.get("AFAQY_PASSWORD", "")
    if not (user and pw):
        return None, (f"{stale}, and AFAQY_USERNAME / AFAQY_PASSWORD are not "
                      "set -- add them as secrets and the job logs in itself")
    got, note = login(user, pw)
    return got, f"{stale}; {note}"
