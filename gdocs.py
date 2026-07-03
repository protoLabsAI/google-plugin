"""Docs service — CREATE-only. Built on the shared auth core.

Deliberately narrow: creating a NEW doc in the user's own Drive is private until
they share it (the Docs analog of draft-only email). Editing existing docs —
which may be shared with others — stays out of scope. Scope: documents.
"""

from __future__ import annotations

from .auth import Creds, request

BASE = "https://docs.googleapis.com/v1/documents"


def create(creds: Creds, title: str, text: str = "", *, client=None) -> dict:
    doc = request(creds, "POST", BASE, json={"title": title}, client=client)
    doc_id = doc.get("documentId", "")
    if text:
        request(creds, "POST", f"{BASE}/{doc_id}:batchUpdate", json={
            "requests": [{"insertText": {"location": {"index": 1}, "text": text}}],
        }, client=client)
    return {"documentId": doc_id, "title": doc.get("title", title),
            "link": f"https://docs.google.com/document/d/{doc_id}/edit"}
