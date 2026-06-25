"""Unit tests for the Google Workspace service layer — mocked httpx, no network.

    cd ~/dev/protoAgent && uv run --frozen python -m pytest ~/dev/google-plugin -q

Tests auth.py / gmail.py / calendar.py via flat imports (the modules' dual-import
fallback). The thin @tool wrappers in __init__.py are smoke-tested live after install.
"""

from __future__ import annotations

import base64
import importlib.util
import sys
from pathlib import Path

import httpx
import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent


def _load(name: str, filename: str):
    """Load a plugin module straight from its file — no package/relative-import
    machinery, and no stdlib `calendar` shadow. `auth` must register under that
    exact name so gmail/calendar's dual-import fallback (`from auth import …`) finds it."""
    spec = importlib.util.spec_from_file_location(name, PLUGIN_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


auth = _load("auth", "auth.py")
gmail = _load("gmail", "gmail.py")
cal = _load("calendar_svc", "calendar.py")
Creds, GoogleError = auth.Creds, auth.GoogleError

CREDS = Creds("cid", "csecret", "rtok")


def _b64url(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()


def _handler(request: httpx.Request) -> httpx.Response:
    host, path = request.url.host, request.url.path
    if host == "oauth2.googleapis.com":
        return httpx.Response(200, json={"access_token": "at-123", "expires_in": 3600})
    if path.endswith("/messages"):
        return httpx.Response(200, json={"messages": [{"id": "m1"}, {"id": "m2"}]})
    if path.endswith("/messages/m1") or path.endswith("/messages/m2"):
        mid = path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={
            "id": mid, "threadId": "t1", "snippet": f"snip-{mid}",
            "payload": {"headers": [{"name": "From", "value": "alice@x.com"},
                                    {"name": "Subject", "value": f"Subj {mid}"},
                                    {"name": "Date", "value": "Wed, 25 Jun 2026 10:00:00 -0700"}]},
        })
    if "/threads/" in path:
        return httpx.Response(200, json={"messages": [{
            "id": "m1", "threadId": "t1", "snippet": "hello",
            "payload": {"headers": [{"name": "From", "value": "bob@x.com"},
                                    {"name": "Subject", "value": "Re: hi"},
                                    {"name": "Message-ID", "value": "<abc@x>"}],
                        "parts": [{"mimeType": "text/plain", "body": {"data": _b64url("PLAIN BODY")}}]},
        }]})
    if path.endswith("/drafts"):
        return httpx.Response(200, json={"id": "d1", "message": {"id": "msg1", "threadId": "t1"}})
    if path.endswith("/events"):
        return httpx.Response(200, json={"items": [{
            "id": "e1", "summary": "Standup",
            "start": {"dateTime": "2026-06-26T09:00:00-07:00"},
            "end": {"dateTime": "2026-06-26T09:15:00-07:00"},
            "attendees": [{"email": "a@x.com"}], "location": "Zoom", "htmlLink": "http://cal/e1"}]})
    if "/events/" in path:
        return httpx.Response(200, json={
            "id": "e1", "summary": "Standup", "description": "daily",
            "start": {"dateTime": "2026-06-26T09:00:00-07:00"},
            "end": {"dateTime": "2026-06-26T09:15:00-07:00"},
            "attendees": [{"email": "a@x.com", "displayName": "Al", "responseStatus": "accepted"}],
            "organizer": {"email": "o@x.com", "displayName": "Org"}, "htmlLink": "http://cal/e1"})
    return httpx.Response(404, json={"error": path})


@pytest.fixture()
def client():
    auth._TOKEN_CACHE.clear()
    c = httpx.Client(transport=httpx.MockTransport(_handler))
    yield c
    c.close()


def test_access_token_fetch_and_cache(client):
    assert auth.get_access_token(CREDS, client=client) == "at-123"
    bad = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert auth.get_access_token(CREDS, client=bad) == "at-123"  # served from cache, no re-fetch
    bad.close()


def test_unconfigured_raises():
    with pytest.raises(GoogleError):
        auth.get_access_token(Creds("", "", ""))


def test_gmail_list_summarizes_each_message(client):
    msgs = gmail.list_messages(CREDS, "is:unread", 20, client=client)
    assert [m["messageId"] for m in msgs] == ["m1", "m2"]
    assert msgs[0]["from"] == "alice@x.com" and msgs[0]["subject"] == "Subj m1" and msgs[0]["snippet"] == "snip-m1"


def test_gmail_thread_extracts_plain_body(client):
    msgs = gmail.get_thread(CREDS, "t1", client=client)
    assert msgs[0]["body"] == "PLAIN BODY" and msgs[0]["subject"] == "Re: hi"


def test_build_draft_raw_roundtrips_headers_and_body():
    decoded = base64.urlsafe_b64decode(gmail.build_draft_raw("hi there", to="x@y.com", subject="Hello").encode()).decode()
    assert "To: x@y.com" in decoded and "Subject: Hello" in decoded
    assert decoded.endswith("hi there") and "Content-Type: text/plain" in decoded


def test_create_draft_resolves_thread_headers(client):
    d = gmail.create_draft(CREDS, "reply body", thread_id="t1", client=client)
    assert d["draftId"] == "d1" and d["sent"] is False
    assert d["to"] == "bob@x.com" and d["subject"] == "Re: hi"


def test_calendar_list_and_detail(client):
    events = cal.list_upcoming(CREDS, days=7, client=client, now_iso="2026-06-25T00:00:00+00:00")
    assert events[0]["title"] == "Standup" and events[0]["attendees"] == ["a@x.com"]
    detail = cal.event_detail(CREDS, "e1", client=client)
    assert detail["description"] == "daily"
    assert detail["attendees"][0] == {"email": "a@x.com", "name": "Al", "status": "accepted"}
    assert detail["organizer"]["email"] == "o@x.com"
