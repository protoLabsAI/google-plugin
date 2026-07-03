"""One-click OAuth connect flow — state nonce, exchange, callback wiring. No network."""

from __future__ import annotations

import urllib.parse
from pathlib import Path

import google_plugin as plugin
import httpx
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from google_plugin import gcal, gmail, oauth, view
from google_plugin.auth import Creds

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = yaml.safe_load((ROOT / "protoagent.plugin.yaml").read_text())

CLIENT = Creds("cid", "csecret", "")


@pytest.fixture(autouse=True)
def _clean_state():
    oauth._PENDING.clear()
    yield
    oauth._PENDING.clear()


def test_begin_builds_consent_url_and_registers_state():
    url = oauth.begin("cid", "http://localhost:7870/plugins/google/oauth/callback")
    parsed = urllib.parse.urlparse(url)
    q = dict(urllib.parse.parse_qsl(parsed.query))
    assert url.startswith(oauth.AUTH_URL)
    assert q["client_id"] == "cid" and q["redirect_uri"].endswith("/oauth/callback")
    assert q["access_type"] == "offline" and q["prompt"] == "consent"  # forces a refresh token
    assert q["scope"] == " ".join(oauth.DEFAULT_SCOPES)
    assert q["state"] in oauth._PENDING


def test_begin_scope_override():
    url = oauth.begin("cid", "http://x/cb", scopes="a b")
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
    assert q["scope"] == "a b"


def test_claim_state_is_single_use_and_ttl_bound(monkeypatch):
    oauth.begin("cid", "http://x/cb")
    (state,) = oauth._PENDING
    assert oauth.claim_state(state) is True
    assert oauth.claim_state(state) is False  # single-use
    assert oauth.claim_state("unknown") is False
    oauth._PENDING["stale"] = 1.0  # long expired
    assert oauth.claim_state("stale") is False


def test_exchange_returns_payload_and_requires_refresh_token():
    def handler(request: httpx.Request) -> httpx.Response:
        body = dict(urllib.parse.parse_qsl(request.content.decode()))
        assert body["grant_type"] == "authorization_code" and body["code"] == "the-code"
        return httpx.Response(200, json={"access_token": "at", "refresh_token": "rt-1"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        payload = oauth.exchange("cid", "cs", "the-code", "http://x/cb", client=c)
    assert payload["refresh_token"] == "rt-1"

    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"access_token": "at"}))) as c:
        with pytest.raises(oauth.OAuthFlowError, match="no refresh token"):
            oauth.exchange("cid", "cs", "the-code", "http://x/cb", client=c)


def test_exchange_surfaces_google_error_description():
    resp = httpx.Response(400, json={"error": "invalid_grant", "error_description": "Bad code"})
    with httpx.Client(transport=httpx.MockTransport(lambda r: resp)) as c:
        with pytest.raises(oauth.OAuthFlowError, match="Bad code"):
            oauth.exchange("cid", "cs", "x", "http://x/cb", client=c)


def test_persist_refresh_token_reports_no_host():
    assert oauth.persist_refresh_token("google", "rt") is False  # no graph.config_io here


# ── Callback route, mounted as the host mounts it ─────────────────────────────


def _app(on_refresh_token=None, scopes_fn=None) -> FastAPI:
    page, data = view.build_router(lambda: CLIENT, gmail, gcal, scopes_fn=scopes_fn, on_refresh_token=on_refresh_token)
    app = FastAPI()
    app.include_router(page, prefix="/plugins/google")
    app.include_router(data, prefix="/api/plugins/google")
    return app


def test_callback_path_is_public_in_manifest():
    assert "/plugins/google/oauth/callback" in MANIFEST["public_paths"]


def test_oauth_start_requires_client_creds():
    page, data = view.build_router(lambda: Creds("", "", ""), gmail, gcal)
    app = FastAPI()
    app.include_router(data, prefix="/api/plugins/google")
    out = TestClient(app).post("/api/plugins/google/oauth/start").json()
    assert "client_id" in out["error"]


def test_oauth_start_mints_url_with_request_derived_redirect():
    c = TestClient(_app(scopes_fn=lambda: "s1 s2"))
    out = c.post("/api/plugins/google/oauth/start").json()
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(out["url"]).query))
    assert q["redirect_uri"] == "http://testserver/plugins/google/oauth/callback"
    assert q["scope"] == "s1 s2"
    assert q["state"] in oauth._PENDING


def test_callback_rejects_unknown_state():
    r = TestClient(_app()).get("/plugins/google/oauth/callback", params={"code": "x", "state": "bogus"})
    assert r.status_code == 400 and "stale" in r.text


def test_callback_reports_user_denial():
    r = TestClient(_app()).get("/plugins/google/oauth/callback", params={"error": "access_denied"})
    assert r.status_code == 400 and "access_denied" in r.text


def test_callback_exchanges_persists_and_swaps_creds(monkeypatch):
    got: dict = {}
    monkeypatch.setattr(
        oauth, "exchange", lambda cid, cs, code, uri, **kw: got.update(code=code, uri=uri) or {"refresh_token": "rt-9"}
    )
    monkeypatch.setattr(
        oauth, "persist_refresh_token", lambda section, tok: got.update(persisted=(section, tok)) or True
    )
    c = TestClient(_app(on_refresh_token=lambda t: got.update(live=t)))
    c.post("/api/plugins/google/oauth/start")
    (state,) = oauth._PENDING
    r = c.get("/plugins/google/oauth/callback", params={"code": "the-code", "state": state})
    assert r.status_code == 200 and "connected" in r.text.lower()
    assert got["code"] == "the-code" and got["uri"] == "http://testserver/plugins/google/oauth/callback"
    assert got["persisted"] == ("google", "rt-9") and got["live"] == "rt-9"


def test_callback_warns_when_persist_fails(monkeypatch):
    monkeypatch.setattr(oauth, "exchange", lambda *a, **kw: {"refresh_token": "rt-9"})
    monkeypatch.setattr(oauth, "persist_refresh_token", lambda *a: False)
    c = TestClient(_app(on_refresh_token=lambda t: None))
    c.post("/api/plugins/google/oauth/start")
    (state,) = oauth._PENDING
    r = c.get("/plugins/google/oauth/callback", params={"code": "x", "state": state})
    assert r.status_code == 200 and "secrets.yaml" in r.text


def test_set_refresh_token_swaps_live_creds(monkeypatch):
    monkeypatch.setattr(plugin, "_CREDS", Creds("cid", "cs", "old"))
    plugin._set_refresh_token("new")
    assert plugin._creds().refresh_token == "new" and plugin._creds().client_id == "cid"


def test_status_reports_has_client_and_email(monkeypatch):
    monkeypatch.setattr(gmail, "profile", lambda c, **kw: {"emailAddress": "josh@x.com"})
    page, data = view.build_router(lambda: Creds("cid", "cs", "rt"), gmail, gcal)
    app = FastAPI()
    app.include_router(data, prefix="/api/plugins/google")
    out = TestClient(app).get("/api/plugins/google/status").json()
    assert out == {"configured": True, "has_client": True, "email": "josh@x.com"}
