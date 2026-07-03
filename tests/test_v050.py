"""v0.5.0 batch — contacts search, docs create, own-calendar event creation."""

from __future__ import annotations

import json

import google_plugin as plugin
import httpx

from google_plugin import auth, gcal, gdocs, gpeople

CREDS = auth.Creds("cid", "csecret", "rtok")


def _client(handler):
    auth._TOKEN_CACHE.clear()

    def h(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(h))


# ── Contacts ──────────────────────────────────────────────────────────────────


def test_contacts_search_merges_sources_dedupes_and_warms_once():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = dict(request.url.params).get("query", "")
        calls.append((request.url.path, q))
        person = {
            "names": [{"displayName": "Mike Roe"}],
            "emailAddresses": [{"value": "mike@x.com"}],
            "organizations": [{"name": "Acme"}],
        }
        return httpx.Response(200, json={"results": [{"person": person}]} if q else {})

    gpeople._WARMED.clear()
    with _client(handler) as c:
        people = gpeople.search(CREDS, "mike", client=c)
        gpeople.search(CREDS, "mike", client=c)  # second call: no re-warmup
    assert people == [{"name": "Mike Roe", "emails": ["mike@x.com"], "org": "Acme"}]  # deduped across sources
    warmups = [c for c in calls if c[1] == ""]
    assert len(warmups) == 2  # one per source, once per token
    assert {p for p, _ in calls} == {"/v1/people:searchContacts", "/v1/otherContacts:search"}


# ── Docs ──────────────────────────────────────────────────────────────────────


def test_docs_create_inserts_text_and_links():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, json.loads(request.read() or b"{}")))
        if request.url.path.endswith(":batchUpdate"):
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"documentId": "doc-1", "title": "Notes"})

    with _client(handler) as c:
        out = gdocs.create(CREDS, "Notes", "hello world", client=c)
    assert out == {"documentId": "doc-1", "title": "Notes", "link": "https://docs.google.com/document/d/doc-1/edit"}
    assert calls[1][0] == "/v1/documents/doc-1:batchUpdate"
    assert calls[1][1]["requests"][0]["insertText"] == {"location": {"index": 1}, "text": "hello world"}


def test_docs_create_empty_text_skips_batch_update():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"documentId": "doc-2", "title": "Empty"})

    with _client(handler) as c:
        gdocs.create(CREDS, "Empty", client=c)
    assert calls == ["/v1/documents"]


# ── Calendar event creation ───────────────────────────────────────────────────


def test_create_event_timed_and_all_day():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.read()))
        return httpx.Response(
            200,
            json={
                "id": "e1",
                "summary": seen[-1]["summary"],
                "start": seen[-1]["start"],
                "end": seen[-1]["end"],
                "htmlLink": "http://cal/e1",
            },
        )

    with _client(handler) as c:
        gcal.create_event(
            CREDS, "Focus", "2026-07-04T09:00:00", "2026-07-04T10:00:00", timezone_name="America/Los_Angeles", client=c
        )
        gcal.create_event(CREDS, "Trip", "2026-07-10", "2026-07-12", description="pack", client=c)
    assert seen[0]["start"] == {"dateTime": "2026-07-04T09:00:00", "timeZone": "America/Los_Angeles"}
    assert seen[1]["start"] == {"date": "2026-07-10"} and seen[1]["description"] == "pack"
    assert all("attendees" not in body for body in seen)


def test_create_event_tool_has_no_attendees_surface():
    schema = plugin.calendar_create_event.args
    assert "attendees" not in schema and "attendee" not in json.dumps(schema).lower()
    assert (
        "never sends" in plugin.calendar_create_event.description.lower()
        or "cannot invite" in plugin.calendar_create_event.description.lower()
    )


def test_create_event_tool_reports_link(monkeypatch):
    monkeypatch.setattr(plugin, "_CREDS", auth.Creds("c", "s", "r"))
    monkeypatch.setattr(
        gcal,
        "create_event",
        lambda *a, **kw: {
            "title": "Focus",
            "start": "2026-07-04T09:00:00",
            "end": "2026-07-04T10:00:00",
            "link": "http://cal/e1",
            "id": "e1",
            "attendees": [],
            "location": "",
        },
    )
    out = plugin.calendar_create_event.invoke(
        {"title": "Focus", "start": "2026-07-04T09:00:00", "end": "2026-07-04T10:00:00"}
    )
    assert "http://cal/e1" in out and "no attendees invited" in out


# ── Scopes ────────────────────────────────────────────────────────────────────


def test_default_scopes_cover_all_services():
    from google_plugin import oauth

    scopes = " ".join(oauth.DEFAULT_SCOPES)
    for needle in (
        "gmail.modify",
        "auth/calendar",
        "drive.readonly",
        "contacts.readonly",
        "contacts.other.readonly",
        "auth/documents",
    ):
        assert needle in scopes, needle
