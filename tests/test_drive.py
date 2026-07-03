"""Drive service — search escaping, export-vs-media routing, binary refusal. No network."""

from __future__ import annotations

import httpx
import pytest

from google_plugin import auth, gdrive
from google_plugin.auth import Creds

CREDS = Creds("cid", "csecret", "rtok")

DOC = {
    "id": "f-doc",
    "name": "Plan",
    "mimeType": "application/vnd.google-apps.document",
    "modifiedTime": "2026-07-01T00:00:00Z",
    "owners": [{"emailAddress": "josh@x.com"}],
    "webViewLink": "http://drive/f-doc",
}
TXT = {"id": "f-txt", "name": "notes.txt", "mimeType": "text/plain", "size": "9"}
PNG = {"id": "f-png", "name": "logo.png", "mimeType": "image/png", "size": "512"}


def _handler(request: httpx.Request) -> httpx.Response:
    path, params = request.url.path, dict(request.url.params)
    if request.url.host == "oauth2.googleapis.com":
        return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
    if path.endswith("/files"):
        assert params["q"] == "fullText contains 'q3 plan \\'draft\\'' and trashed = false"
        assert params["pageSize"] == "5"
        return httpx.Response(200, json={"files": [DOC, TXT]})
    if path.endswith("/export"):
        assert params["mimeType"] == "text/plain"
        return httpx.Response(200, text="DOC TEXT " * 10)
    if path.endswith("/f-doc"):
        return httpx.Response(200, json=DOC)
    if path.endswith("/f-txt"):
        if params.get("alt") == "media":
            return httpx.Response(200, text="RAW NOTES")
        return httpx.Response(200, json=TXT)
    if path.endswith("/f-png"):
        return httpx.Response(200, json=PNG)
    return httpx.Response(404, json={"error": {"message": f"not found: {path}"}})


@pytest.fixture()
def client():
    auth._TOKEN_CACHE.clear()
    c = httpx.Client(transport=httpx.MockTransport(_handler))
    yield c
    c.close()


def test_search_escapes_query_and_summarizes(client):
    files = gdrive.search(CREDS, "q3 plan 'draft'", 5, client=client)
    assert [f["id"] for f in files] == ["f-doc", "f-txt"]
    assert files[0]["owners"] == ["josh@x.com"] and files[0]["link"] == "http://drive/f-doc"


def test_read_exports_google_doc_as_text(client):
    out = gdrive.read(CREDS, "f-doc", client=client)
    assert out["content"].startswith("DOC TEXT") and out["truncated"] is False
    assert out["name"] == "Plan"


def test_read_fetches_plain_text_via_media(client):
    out = gdrive.read(CREDS, "f-txt", client=client)
    assert out["content"] == "RAW NOTES"


def test_read_truncates_to_max_chars(client):
    out = gdrive.read(CREDS, "f-doc", max_chars=4, client=client)
    assert out["content"] == "DOC " and out["truncated"] is True


def test_read_refuses_binary_with_metadata(client):
    out = gdrive.read(CREDS, "f-png", client=client)
    assert "binary file (image/png)" in out["error"]
    assert "content" not in out and out["name"] == "logo.png"
