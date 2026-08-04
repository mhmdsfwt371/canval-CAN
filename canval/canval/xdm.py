"""Thin client over the XDM external API.

Only the endpoints this tool actually needs, taken from the v4 OpenAPI spec:

    GET  /api/external/v1/hardwares/sdkVersions
    GET  /api/external/v3/canfiles/filter
    POST /api/external/v4/devicesSdk/filter
    GET  /api/external/v3/settingsOverrides/{uid}/overrides
"""

from __future__ import annotations

import random
import threading
import time
from typing import Any, Iterator

import requests

from .config import Settings

_RETRY_STATUS = {429, 500, 502, 503, 504}


class XdmError(RuntimeError):
    pass


class XdmClient:
    def __init__(self, settings: Settings):
        self.s = settings
        self._session = requests.Session()
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- auth

    def _get_token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._expires_at - 60:
                return self._token

            resp = self._session.post(
                self.s.token_url,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "client_id": self.s.client_id,
                    "client_secret": self.s.client_secret,
                    "grant_type": "client_credentials",
                },
                timeout=self.s.timeout,
            )
            if resp.status_code != 200:
                # Show what the auth server actually said. "invalid_client"
                # means the credentials; "unauthorized_client" means the grant
                # type is not enabled for them; anything else is worth reading
                # rather than guessing at.
                detail = resp.text[:400]
                try:
                    body = resp.json()
                    detail = (f"{body.get('error', '?')}: "
                              f"{body.get('error_description', '')}").strip(": ")
                except Exception:                      # noqa: BLE001
                    pass
                raise XdmError(
                    f"Token request failed ({resp.status_code}) at {self.s.token_url}\n"
                    f"  server said: {detail}\n"
                    f"  client_id sent: {self.s.client_id}"
                )
            payload = resp.json()
            self._token = payload["access_token"]
            self._expires_at = time.time() + float(payload.get("expires_in", 300))
            return self._token

    # ------------------------------------------------------------ requests

    def _request(self, method: str, path: str, **kw) -> Any:
        url = f"{self.s.base_url}{path}"
        last = None

        for attempt in range(5):
            headers = {"Authorization": f"Bearer {self._get_token()}"}
            headers.update(kw.pop("headers", {}))
            try:
                resp = self._session.request(
                    method, url, headers=headers, timeout=self.s.timeout, **kw
                )
            except requests.RequestException as exc:
                last = str(exc)
                time.sleep(min(2**attempt, 20) + random.random())
                continue

            if resp.status_code == 401:
                # token rejected: force a refresh and retry once more
                with self._lock:
                    self._token = None
                last = "401 unauthorized"
                continue

            if resp.status_code in _RETRY_STATUS:
                last = f"{resp.status_code} {resp.text[:200]}"
                wait = float(resp.headers.get("Retry-After", min(2**attempt, 20)))
                time.sleep(wait + random.random())
                continue

            if not resp.ok:
                raise XdmError(f"{method} {path} -> {resp.status_code} {resp.text[:300]}")

            if not resp.content:
                return None
            return resp.json()

        raise XdmError(f"{method} {path} failed after retries: {last}")

    # ----------------------------------------------------------- endpoints

    def hardware_versions(self) -> list[dict]:
        """Hardware list. Use it to resolve the id for e.g. LX45-EA."""
        data = self._request("GET", "/api/external/v1/hardwares/sdkVersions")
        return data if isinstance(data, list) else [data]

    def iter_can_files(self, model: str | None = None, page_size: int = 100,
                       progress=None) -> Iterator[dict]:
        """Yield every CAN file in the catalogue: {id, model, notes, version}.

        Paging is driven by recordCount, not by whether a page came back
        full. A short page in the middle of a run is not the end -- an
        earlier version stopped on one and silently lost a thousand rows,
        which then read as "that model does not exist".

        The offset advances by the rows actually received, so a short page
        leaves no gap; advancing by page_size instead would skip whatever
        the server held back.
        """
        first = 0
        total = None
        seen = 0
        empty_pages = 0

        while True:
            params = {"FirstRecord": first, "ItemsPerPage": page_size}
            if model:
                params["Model"] = model

            page = self._request(
                "GET", "/api/external/v3/canfiles/filter", params=params
            ) or {}
            results = page.get("results") or []
            reported = (page.get("paginator") or {}).get("recordCount")
            if reported is not None:
                total = reported

            for row in results:
                yield row
            seen += len(results)

            if progress:
                progress(seen, total)

            if not results:
                # An empty page at a valid offset means the server stopped
                # short. Retry once, then fail loudly: an incomplete
                # catalogue silently reports real vehicles as unsupported,
                # which is worse than no catalogue at all.
                empty_pages += 1
                if total is None:
                    return
                if empty_pages >= 2:
                    raise XdmError(
                        f"Catalogue truncated: {seen} of {total} rows before "
                        f"the server returned empty pages at offset {first}. "
                        "Re-run; do not trust a partial catalogue."
                    )
                continue
            empty_pages = 0

            first += len(results)
            if total is not None and first >= total:
                if seen < total:
                    raise XdmError(
                        f"Catalogue truncated: {seen} of {total} rows fetched."
                    )
                return

    def iter_devices(
        self,
        hardware_ids: list[int] | None = None,
        last_activity_from: int | None = None,
        page_size: int = 200,
    ) -> Iterator[dict]:
        """Yield devices. Narrow with hardware_ids to keep the sweep small.

        last_activity_from is unix seconds; use it to skip devices that have
        never reported, they carry no evidence.
        """
        first = 0
        while True:
            # The schema calls this `filter`, singular. Sending `filters`
            # made the server return a 500 null-reference rather than a
            # validation error, which reads like an outage instead of a
            # typo -- worth the comment.
            body: dict[str, Any] = {
                "paginator": {"firstRecord": first, "itemsPerPage": page_size}
            }
            filt: dict[str, Any] = {}
            if hardware_ids:
                filt["hardwareId"] = list(hardware_ids)
            if last_activity_from:
                filt["lastActivityFrom"] = int(last_activity_from)
            if filt:
                body["filter"] = filt

            page = self._request(
                "POST", "/api/external/v4/devicesSdk/filter", json=body
            )
            results = (page or {}).get("results") or []
            for row in results:
                yield row

            total = ((page or {}).get("paginator") or {}).get("recordCount")
            first += len(results)
            if not results or (total is not None and first >= total):
                return

    def device_overrides(self, uid: str) -> list[dict]:
        """Flat list of {elementId, name, value} for one device."""
        data = self._request(
            "GET", f"/api/external/v3/settingsOverrides/{uid}/overrides"
        )
        return (data or {}).get("overrides") or []
