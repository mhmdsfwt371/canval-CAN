"""Configuration. Credentials are read from the environment only.

Never hardcode client_secret in this file or commit it anywhere.

Required:
    XDM_CLIENT_ID
    XDM_CLIENT_SECRET
Optional:
    XDM_REGION       "eu" (default) or "com"
    XDM_CONCURRENCY  parallel requests during the sweep (default 6)
    CANVAL_DB        path to the local sqlite file (default ./canval.db)
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# The auth host follows the region, and getting this wrong costs an hour:
# an EU client against the .com realm returns "invalid_client", which reads
# exactly like a bad secret. Keep the login and API hosts paired.
_REGIONS = {
    "eu": {
        "token": "https://login.xgfleet.eu/realms/public/protocol/openid-connect/token",
        "base": "https://xdm.xgfleet.eu",
    },
    "com": {
        "token": "https://login.xgfleet.com/realms/public/protocol/openid-connect/token",
        "base": "https://xdm.xgfleet.com",
    },
}

# Kept so anything importing the old name still works.
TOKEN_URL = _REGIONS["com"]["token"]


@dataclass(frozen=True)
class Settings:
    client_id: str
    client_secret: str
    base_url: str
    token_url: str
    concurrency: int
    db_path: str
    timeout: int = 30

    @classmethod
    def from_env(cls) -> "Settings":
        cid = os.environ.get("XDM_CLIENT_ID")
        sec = os.environ.get("XDM_CLIENT_SECRET")
        if not cid or not sec:
            raise RuntimeError(
                "Missing XDM_CLIENT_ID / XDM_CLIENT_SECRET in the environment."
            )

        region = os.environ.get("XDM_REGION", "eu").lower()
        if region not in _REGIONS:
            raise RuntimeError(f"XDM_REGION must be one of {sorted(_REGIONS)}")

        return cls(
            client_id=cid,
            client_secret=sec,
            base_url=os.environ.get("XDM_BASE") or _REGIONS[region]["base"],
            token_url=os.environ.get("XDM_TOKEN_URL") or _REGIONS[region]["token"],
            concurrency=int(os.environ.get("XDM_CONCURRENCY", "6")),
            db_path=os.environ.get("CANVAL_DB", "canval.db"),
        )
