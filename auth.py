"""Service-agnostic Google auth + REST core.

Shared by every Workspace service module (gmail, calendar, and — as this grows —
drive, docs, sheets, …). A service module is just parsing on top of ``request()``;
adding one needs no auth changes. OAuth refresh-token flow over raw httpx, no SDK.

Scopes: this layer is scope-agnostic — it uses whatever the refresh token was
granted. Mint the refresh token with the FULL workspace scope set you intend to
grow into (e.g. gmail.modify, calendar, drive, documents, spreadsheets) so adding
a service later needs no re-consent.

Errors surface as GoogleError carrying Google's own message (the JSON error body),
not a bare HTTP status — "insufficient authentication scopes" beats "403". A 401 on
a data call retries once with a fresh token (the cached one may have been revoked).
"""

from __future__ import annotations

import threading
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


# {refresh_token: (access_token, expires_at)} — tools run on worker threads, so the
# cache is lock-guarded; the network refresh happens outside the lock (a rare
# duplicate refresh is harmless, a held lock across I/O is not).
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
_CACHE_LOCK = threading.Lock()


def _now() -> float:
    return time.time()


def _error_detail(resp: httpx.Response) -> str:
    """Google's own error message out of a failed response's JSON body."""
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001 — non-JSON error body
        return (resp.text or "")[:200]
    err = payload.get("error")
    if isinstance(err, dict):  # API style: {"error": {"code": ..., "message": ..., "status": ...}}
        return str(err.get("message") or err.get("status") or "")[:300]
    # OAuth style: {"error": "invalid_grant", "error_description": "..."}
    return str(payload.get("error_description") or err or "")[:300]


def invalidate_token(creds: Creds) -> None:
    """Drop the cached access token (e.g. after a 401 — it may have been revoked)."""
    with _CACHE_LOCK:
        _TOKEN_CACHE.pop(creds.refresh_token, None)


def get_access_token(creds: Creds, *, client: httpx.Client | None = None) -> str:
    """Exchange the refresh token for a cached short-lived access token."""
    if not creds.configured():
        raise GoogleError("Google is not configured — set client_id, client_secret, refresh_token.")
    with _CACHE_LOCK:
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
        if resp.status_code != 200:
            detail = _error_detail(resp) or f"HTTP {resp.status_code}"
            raise GoogleError(
                f"token refresh failed: {detail} — if the grant was revoked or expired, "
                "reconnect from the Google panel (or re-mint the refresh token)."
            )
        payload = resp.json()
    finally:
        if owns:
            client.close()
    token = payload["access_token"]
    with _CACHE_LOCK:
        _TOKEN_CACHE[creds.refresh_token] = (token, _now() + int(payload.get("expires_in", 3600)))
    return token


def request(creds: Creds, method: str, url: str, *, params: dict | None = None,
            json: dict | None = None, client: httpx.Client | None = None) -> dict:
    """Authenticated Google REST call. Returns parsed JSON (or {} on 204).

    Any service module builds on this — pass a full endpoint URL + params/json.
    Pass ``client`` (e.g. an httpx.MockTransport client) to unit-test offline.
    A 401 retries once with a freshly minted token; other errors raise GoogleError
    with Google's message from the response body.
    """
    owns = client is None
    c = client or httpx.Client(timeout=30)
    try:
        for attempt in (1, 2):
            token = get_access_token(creds, client=c)
            resp = c.request(method, url, params=params, json=json,
                             headers={"Authorization": f"Bearer {token}"})
            if resp.status_code == 401 and attempt == 1:
                invalidate_token(creds)  # cached token revoked/expired early — retry fresh
                continue
            break
    finally:
        if owns:
            c.close()
    if resp.status_code >= 400:
        detail = _error_detail(resp) or "no detail"
        raise GoogleError(f"{method} {resp.url.path} -> {resp.status_code}: {detail}")
    if resp.status_code == 204 or not resp.content:
        return {}
    return resp.json()
