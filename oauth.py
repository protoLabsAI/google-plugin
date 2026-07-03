"""One-click OAuth connect — the authorization-code flow, for the operator.

These routes serve the HUMAN, not the agent: the console view's "Connect Google"
button opens Google's consent screen; Google redirects back to the plugin's public
callback, which exchanges the code for a refresh token and persists it into the
host's untracked ``secrets.yaml``. The operator supplies only a client_id +
client_secret (their own Google Cloud OAuth client) — no manual token minting.

START must be called through the gated ``/api/plugins/google`` router (operator
bearer); CALLBACK is public (Google's redirect can't carry a bearer) and is bound
to a single-use, short-lived ``state`` nonce minted by START.
"""

from __future__ import annotations

import secrets as _secrets
import time
import urllib.parse

import httpx

from .auth import OAUTH_TOKEN_URL

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"

# The "grow into" scope set (see README): Gmail read+draft+hygiene, Calendar rw,
# Drive read, Contacts read, Docs create. Override with the `oauth_scopes` config
# key (space-separated) before connecting.
DEFAULT_SCOPES = (
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/contacts.readonly",
    "https://www.googleapis.com/auth/contacts.other.readonly",
    "https://www.googleapis.com/auth/documents",
)

_STATE_TTL_S = 600
_PENDING: dict[str, float] = {}  # state nonce -> expiry (single-use)


class OAuthFlowError(RuntimeError):
    """A connect-flow failure surfaced to the operator as readable text."""


def begin(client_id: str, redirect_uri: str, scopes: str = "") -> str:
    """Mint a state nonce and build the Google consent URL to open."""
    now = time.time()
    for s, exp in list(_PENDING.items()):  # drop stale nonces
        if exp < now:
            _PENDING.pop(s, None)
    state = _secrets.token_urlsafe(24)
    _PENDING[state] = now + _STATE_TTL_S
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scopes or " ".join(DEFAULT_SCOPES),
        # offline + consent forces Google to issue a refresh token every time,
        # not just on the account's first grant.
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def claim_state(state: str) -> bool:
    """Validate and consume a callback's state nonce (single-use, TTL-bound)."""
    exp = _PENDING.pop(state or "", None)
    return exp is not None and exp >= time.time()


def exchange(client_id: str, client_secret: str, code: str, redirect_uri: str,
             *, client: httpx.Client | None = None) -> dict:
    """Swap the authorization code for tokens. Returns Google's token payload.

    Raises OAuthFlowError with Google's error description on failure, or when the
    grant came back without a refresh token (e.g. a client/consent misconfig).
    """
    owns = client is None
    c = client or httpx.Client(timeout=30)
    try:
        resp = c.post(OAUTH_TOKEN_URL, data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        })
        payload = resp.json() if resp.content else {}
        if resp.status_code != 200:
            detail = payload.get("error_description") or payload.get("error") or f"HTTP {resp.status_code}"
            raise OAuthFlowError(f"token exchange failed: {detail}")
    finally:
        if owns:
            c.close()
    if not payload.get("refresh_token"):
        raise OAuthFlowError(
            "Google returned no refresh token. Re-try the connect (the flow requests "
            "prompt=consent, which should force one); if it persists, revoke the app under "
            "myaccount.google.com/permissions and connect again."
        )
    return payload


def persist_refresh_token(section: str, token: str) -> bool:
    """Merge the refresh token into the host's untracked secrets.yaml (0600).

    ``refresh_token`` is a declared plugin secret (manifest ``secrets:``), so the
    host redacts it from config reads and purges it on uninstall. Host-only import
    stays lazy; returns False when there's no host (unit tests) so the caller can
    say so instead of crashing the callback.
    """
    try:
        from graph.config_io import save_secrets  # host-only, lazy

        save_secrets({section: {"refresh_token": token}})
        return True
    except Exception:  # noqa: BLE001 — no host / write failure ⇒ report, don't crash
        return False
