"""Reading the device-management console's own endpoints.

WHY THIS EXISTS
---------------
The documented API needs client_credentials, which Xirgo has to issue. The
console itself runs on internal endpoints that the browser session can
already reach, and they return more than the documented ones do -- the CAN
catalogue here carries `manualUrl`, the installation guide per vehicle,
which the public API omits entirely.

So this is the way in while the official credentials are pending.

THE TRADE
---------
These endpoints are undocumented. Nobody promised they will keep their
shape, and a console release can change them without warning. A browser
session also expires, which the official credentials would not.

Treat this as the bridge, not the destination: everything above this
module is written against the same shapes the documented API returns, so
swapping back is a one-file change. Keep asking Xirgo for the credentials.

ENDPOINTS, read off a real capture
----------------------------------
    POST /api/CanFiles/GetCanFilesWithAllParameters
         {"paginator": {...}, "filters": {}}
         -> {"paginator": {"recordCount": 3934}, "results": [...]}
            results: id, version, model, notes, manualUrl, createdOn, changeLog

    POST /api/Devices2/GetItemWithAllParameters
         {"paginator": {...}, "filters": {"uids": []}}
         -> results: imei, uid, currentConfigId, currentConfigName,
                     hardwareVersionId, hardwareVersionName,
                     currentUserSettingsTemplateId, lastActivity, ...

    GET  /api/Hardwares/GetPossibleHardwareVersionsSdkNames?hardwareType=2
         -> [{"id": 98, "name": "LX45-EA"}, ...]
"""

from __future__ import annotations

import os
import random
import time
from typing import Iterator

import requests

DEFAULT_BASE = "https://xdm.xgfleet.eu"
_RETRY = {429, 500, 502, 503, 504}


class SessionExpired(RuntimeError):
    pass


class XdmSession:
    """Talks to the console endpoints with the bearer token from the browser.

    Get the token from the console: DevTools -> Network -> any api call ->
    Headers -> Authorization. Copy everything after "Bearer ".
    """

    def __init__(self, bearer: str | None = None, base_url: str | None = None,
                 timeout: int = 60):
        token = bearer or os.environ.get("XDM_BEARER")
        if not token:
            raise RuntimeError(
                "No console token. Set XDM_BEARER to the value of the "
                "Authorization header (without the leading 'Bearer ')."
            )
        self.token = token.replace("Bearer ", "").strip()
        self.base_url = (base_url or os.environ.get("XDM_BASE") or DEFAULT_BASE).rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
        })

    def _request(self, method: str, path: str, **kw):
        url = f"{self.base_url}{path}"
        last = None
        for attempt in range(4):
            try:
                resp = self.session.request(method, url, timeout=self.timeout, **kw)
            except requests.RequestException as exc:
                last = str(exc)
                time.sleep(min(2 ** attempt, 15) + random.random())
                continue

            if resp.status_code in (401, 403):
                raise SessionExpired(
                    "The console token was rejected. Sessions expire -- open the "
                    "console, copy a fresh Authorization header, and set "
                    "XDM_BEARER again."
                )
            if resp.status_code in _RETRY:
                last = f"{resp.status_code}"
                wait = float(resp.headers.get("Retry-After", min(2 ** attempt, 15)))
                time.sleep(wait + random.random())
                continue
            if not resp.ok:
                raise RuntimeError(f"{method} {path} -> {resp.status_code} "
                                   f"{resp.text[:300]}")
            return resp.json() if resp.content else None

        raise RuntimeError(f"{method} {path} failed after retries: {last}")

    # ------------------------------------------------------------- reads

    @staticmethod
    def _paginator(first: int, per_page: int, sort_field: str | None = None):
        p = {
            "firstRecord": first,
            "recordCount": 0,
            "itemsPerPage": per_page,
            "sortOrderAsc": True,
            "dbSortField": sort_field,
            "dbSortOrder": "ASC" if sort_field else None,
        }
        if sort_field:
            p["sortField"] = sort_field
        return p

    def iter_can_files(self, per_page: int = 200, delay: float = 0.3) -> Iterator[dict]:
        """The whole CAN catalogue, including manualUrl and changeLog."""
        first, total, seen, empty = 0, None, 0, 0
        while True:
            page = self._request(
                "POST", "/api/CanFiles/GetCanFilesWithAllParameters",
                json={"paginator": self._paginator(first, per_page, "id"),
                      "filters": {}},
            ) or {}
            rows = page.get("results") or []
            for row in rows:
                yield row
            seen += len(rows)

            reported = (page.get("paginator") or {}).get("recordCount")
            if reported is not None:
                total = reported

            if not rows:
                # Stopping quietly on a short read is what made an earlier
                # run store 2859 of 3934 rows and report real vehicles as
                # unsupported. Fail loudly instead.
                empty += 1
                if total is None:
                    return
                if empty >= 2:
                    raise RuntimeError(
                        f"Catalogue truncated: {seen} of {total} rows before "
                        f"empty pages at offset {first}. Re-run."
                    )
                continue
            empty = 0

            first += len(rows)
            if total is not None and first >= total:
                if seen < total:
                    raise RuntimeError(
                        f"Catalogue truncated: {seen} of {total} rows fetched."
                    )
                return
            if delay:
                time.sleep(delay)

    def iter_devices(self, per_page: int = 200, hardware_version_id: int | None = None,
                     delay: float = 0.3, progress=None) -> Iterator[dict]:
        """Every device, 200 at a time.

        One paginated sweep replaces what would otherwise be a call per
        device just to learn which config each one carries.
        """
        first, total = 0, None
        while True:
            page = self._request(
                "POST", "/api/Devices2/GetItemWithAllParameters",
                json={"paginator": self._paginator(first, per_page, "uid"),
                      "filters": {"uids": []}},
            ) or {}
            rows = page.get("results") or []
            for row in rows:
                if (hardware_version_id is None
                        or row.get("hardwareVersionId") == hardware_version_id):
                    yield row

            total = (page.get("paginator") or {}).get("recordCount", total)
            first += len(rows)
            if progress and total:
                progress(first, total)
            if not rows or (total is not None and first >= total):
                return
            if delay:
                time.sleep(delay)

    def hardware_versions(self, hardware_type: int = 2) -> list[dict]:
        data = self._request(
            "GET", "/api/Hardwares/GetPossibleHardwareVersionsSdkNames",
            params={"hardwareType": hardware_type},
        )
        if isinstance(data, dict):
            data = data.get("results") or data.get("items") or []
        return data or []

    def find_hardware(self, name_contains: str, hardware_type: int = 2) -> list[dict]:
        needle = name_contains.lower()
        return [h for h in self.hardware_versions(hardware_type)
                if needle in str(h.get("name", "")).lower()]
