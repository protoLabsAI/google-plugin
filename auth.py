"""Service-agnostic Google auth + REST core.

Shared by every Workspace service module (gmail, calendar, and — as this grows —
drive, docs, sheets, …). A service module is just parsing on top of ``request()``;
adding one needs no auth changes. OAuth refresh-token flow over raw httpx, no SDK.

Scopes: this layer is scope-agnostic — it uses whatever the refresh token was
granted. Mint the refresh token with the FULL workspace scope set you intend to
grow into (e.g. gmail.modify, calendar, drive, documents, spreadsheets) so adding
a service later needs no re-consent.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
_EARLY_REFRESH_S = 300  # refresh ~5 min before expiry


class GoogleError(RuntimeError):
    """Configuration or API error surfaced to the agent as a readable string."""


@dataclass
class Creds:
    client_id: str
    client_secret: str
    refresh_token: str

    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.refresh_token)


# {refresh_token: (access_token, expires_at)}
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}


def _now() -> float:
    return time.time()


def get_access_token(creds: Creds, *, client: httpx.Client | None = None) -> str:
    """Exchange the refresh token for a cached short-lived access token."""
    if not creds.configured():
        raise GoogleError("Google is not configured — set client_id, client_secret, refresh_token.")
    cached = _TOKEN_CACHE.get(creds.refresh_token)
    if cached and _now() < cached[1] - _EARLY_REFRESH_S:
        return cached[0]
    owns = client is None
    client = client or httpx.Client(timeout=30)
    try:
        resp = client.post(OAUTH_TOKEN_URL, data={
            "grant_type": "refresh_token",
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "refresh_token": creds.refresh_token,
        })
        resp.raise_for_status()
        payload = resp.json()
    finally:
        if owns:
            client.close()
    token = payload["access_token"]
    _TOKEN_CACHE[creds.refresh_token] = (token, _now() + int(payload.get("expires_in", 3600)))
    return token


def request(creds: Creds, method: str, url: str, *, params: dict | None = None,
            json: dict | None = None, client: httpx.Client | None = None) -> dict:
    """Authenticated Google REST call. Returns parsed JSON (or {} on 204).

    Any service module builds on this — pass a full endpoint URL + params/json.
    Pass ``client`` (e.g. an httpx.MockTransport client) to unit-test offline.
    """
    token = get_access_token(creds, client=client)
    owns = client is None
    c = client or httpx.Client(timeout=30)
    try:
        resp = c.request(method, url, params=params, json=json,
                         headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()
    finally:
        if owns:
            c.close()
