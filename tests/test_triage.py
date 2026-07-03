"""v0.4.0 batch — labels/archive, attachments, draft revision, free-busy, event search."""

from __future__ import annotations

import base64
import json

import google_plugin as plugin
import httpx
import pytest

from google_plugin import auth, gcal, gmail
from google_plugin.auth import Creds, GoogleError

CREDS = Creds("cid", "csecret", "rtok")

LABELS = {
    "labels": [
        {"id": "INBOX", "name": "INBOX", "type": "system"},
        {"id": "UNREAD", "name": "UNREAD", "type": "system"},
        {"id": "Label_7", "name": "Receipts", "type": "user"},
    ]
}


def _client(handler):
    auth._TOKEN_CACHE.clear()
    return httpx.Client(transport=httpx.MockTransport(handler))


def _token_or(handler):
    def h(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        return handler(request)

    return h


# ── Labels / archive ──────────────────────────────────────────────────────────


def test_modify_labels_resolves_names_and_creates_missing():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/labels") and request.method == "GET":
            return httpx.Response(200, json=LABELS)
        if path.endswith("/labels") and request.method == "POST":
            body = json.loads(request.read())
            calls.append(("create", body["name"]))
            return httpx.Response(200, json={"id": "Label_9", "name": body["name"]})
        if path.endswith("/messages/batchModify"):
            calls.append(("batch", json.loads(request.read())))
            return httpx.Response(204)
        return httpx.Response(404, json={"error": {"message": path}})

    with _client(_token_or(handler)) as c:
        out = gmail.modify_labels(CREDS, ["m1", "m2"], add=["receipts", "Travel"], remove=["INBOX"], client=c)
    assert out == {"modified": 2, "created": ["Travel"]}
    assert ("create", "Travel") in calls  # "receipts" matched Receipts case-insensitively, not re-created
    batch = dict(calls)["batch"]
    assert batch == {"addLabelIds": ["Label_7", "Label_9"], "removeLabelIds": ["INBOX"], "ids": ["m1", "m2"]}


def test_modify_labels_refuses_trash_and_spam():
    with pytest.raises(GoogleError, match="never deletes"):
        gmail.modify_labels(CREDS, ["m1"], add=["Trash"])


def test_modify_labels_unknown_remove_errors_with_hint():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=LABELS)

    with _client(_token_or(handler)) as c:
        with pytest.raises(GoogleError, match="no label named 'Bogus'.*receipts"):
            gmail.modify_labels(CREDS, ["m1"], remove=["Bogus"], client=c)


def test_label_tool_archive_flag_and_targets(monkeypatch):
    monkeypatch.setattr(plugin, "_CREDS", Creds("c", "s", "r"))
    assert "message_ids" in plugin.gmail_label.invoke({"add": ["X"]})
    assert "labels to add" in plugin.gmail_label.invoke({"message_ids": ["m1"]})
    seen = {}
    monkeypatch.setattr(
        gmail,
        "modify_labels",
        lambda creds, ids, tid, add, remove, **kw: seen.update(remove=remove) or {"modified": 1, "created": []},
    )
    out = plugin.gmail_label.invoke({"message_ids": ["m1"], "archive": True})
    assert seen["remove"] == ["INBOX"] and "1 message(s)" in out


# ── Attachments ───────────────────────────────────────────────────────────────


def test_get_thread_lists_attachments():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "messages": [
                    {
                        "id": "m1",
                        "threadId": "t1",
                        "snippet": "s",
                        "payload": {
                            "headers": [],
                            "parts": [
                                {"mimeType": "text/plain", "body": {"data": base64.urlsafe_b64encode(b"hi").decode()}},
                                {
                                    "mimeType": "application/pdf",
                                    "filename": "invoice.pdf",
                                    "body": {"attachmentId": "att-1", "size": 999},
                                },
                            ],
                        },
                    }
                ]
            },
        )

    with _client(_token_or(handler)) as c:
        msgs = gmail.get_thread(CREDS, "t1", client=c)
    assert msgs[0]["attachments"] == [
        {"filename": "invoice.pdf", "mimeType": "application/pdf", "size": 999, "attachmentId": "att-1"}
    ]


def test_get_attachment_tool_text_vs_binary(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin, "_CREDS", Creds("c", "s", "r"))
    monkeypatch.setattr(plugin, "_attachments_dir", lambda: tmp_path)
    monkeypatch.setattr(gmail, "get_attachment", lambda creds, mid, aid, **kw: b"col1,col2\n1,2")
    out = json.loads(
        plugin.gmail_get_attachment.invoke({"message_id": "m1", "attachment_id": "a1", "filename": "data.csv"})
    )
    assert out["content"] == "col1,col2\n1,2" and out["truncated"] is False

    monkeypatch.setattr(gmail, "get_attachment", lambda creds, mid, aid, **kw: b"\x89PNG...")
    out = json.loads(
        plugin.gmail_get_attachment.invoke({"message_id": "m1", "attachment_id": "a1", "filename": "../evil/logo.png"})
    )
    assert out["savedTo"].startswith(str(tmp_path)) and "evil" not in out["savedTo"]  # path-sanitized
    assert out["bytes"] == 7


# ── Draft revision ────────────────────────────────────────────────────────────


def test_list_drafts_summarizes():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/drafts"):
            return httpx.Response(200, json={"drafts": [{"id": "d1"}]})
        return httpx.Response(
            200,
            json={
                "message": {
                    "id": "m1",
                    "threadId": "t1",
                    "snippet": "draft snip",
                    "payload": {"headers": [{"name": "To", "value": "x@y.com"}, {"name": "Subject", "value": "Hello"}]},
                }
            },
        )

    with _client(_token_or(handler)) as c:
        drafts = gmail.list_drafts(CREDS, client=c)
    assert drafts[0]["draftId"] == "d1" and drafts[0]["to"] == "x@y.com" and drafts[0]["subject"] == "Hello"


def test_update_draft_preserves_headers_and_thread():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "message": {
                        "id": "m1",
                        "threadId": "t1",
                        "payload": {
                            "headers": [
                                {"name": "To", "value": "bob@x.com"},
                                {"name": "Subject", "value": "Re: hi"},
                                {"name": "In-Reply-To", "value": "<abc@x>"},
                            ]
                        },
                    }
                },
            )
        seen["put"] = json.loads(request.read())
        return httpx.Response(200, json={"id": "d1"})

    with _client(_token_or(handler)) as c:
        out = gmail.update_draft(CREDS, "d1", "new body", client=c)
    assert out == {"draftId": "d1", "to": "bob@x.com", "subject": "Re: hi", "sent": False}
    assert seen["put"]["message"]["threadId"] == "t1"
    raw = base64.urlsafe_b64decode(seen["put"]["message"]["raw"].encode()).decode()
    assert raw.endswith("new body") and "In-Reply-To: <abc@x>" in raw


# ── Calendar: free/busy + search ──────────────────────────────────────────────


def test_free_busy_extracts_busy_blocks():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/freeBusy")
        body = json.loads(request.read())
        assert body["items"] == [{"id": "primary"}]
        return httpx.Response(
            200,
            json={
                "calendars": {"primary": {"busy": [{"start": "2026-07-04T09:00:00Z", "end": "2026-07-04T10:00:00Z"}]}}
            },
        )

    with _client(_token_or(handler)) as c:
        out = gcal.free_busy(CREDS, days=3, client=c, now_iso="2026-07-03T00:00:00+00:00")
    assert out["busy"] == [{"start": "2026-07-04T09:00:00Z", "end": "2026-07-04T10:00:00Z"}]
    assert out["timeMin"] == "2026-07-03T00:00:00+00:00"


def test_search_events_passes_query_and_window():
    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        assert params["q"] == "dentist" and params["orderBy"] == "startTime"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "e9",
                        "summary": "Dentist",
                        "start": {"dateTime": "2026-07-10T15:00:00Z"},
                        "end": {"dateTime": "2026-07-10T16:00:00Z"},
                    }
                ]
            },
        )

    with _client(_token_or(handler)) as c:
        events = gcal.search_events(CREDS, "dentist", client=c, now_iso="2026-07-03T00:00:00+00:00")
    assert events[0]["title"] == "Dentist"
