"""Drive service — read-only search + fetch-as-text. Built on the shared auth core.

The additive-module promise made good: this file + two tool wrappers is the whole
cost of a new Workspace service. Google-native files export as text (Docs →
text/plain, Sheets → text/csv, Slides → text/plain); regular text files fetch raw
media. Binary files return their metadata with a readable refusal, never mojibake.
"""

from __future__ import annotations

from .auth import Creds, request

BASE = "https://www.googleapis.com/drive/v3/files"

# Google-native types and the text shape they export as.
_EXPORT = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}
_TEXTY_PREFIXES = ("text/",)
_TEXTY_EXACT = {
    "application/json", "application/xml", "application/javascript",
    "application/x-yaml", "application/yaml", "application/csv",
}
_FIELDS = "id,name,mimeType,modifiedTime,size,owners(emailAddress),webViewLink"


def _summary(f: dict) -> dict:
    return {
        "id": f.get("id", ""),
        "name": f.get("name", ""),
        "mimeType": f.get("mimeType", ""),
        "modified": f.get("modifiedTime", ""),
        "size": f.get("size", ""),
        "owners": [o.get("emailAddress", "") for o in (f.get("owners") or [])],
        "link": f.get("webViewLink", ""),
    }


def _is_texty(mime: str) -> bool:
    return mime.startswith(_TEXTY_PREFIXES) or mime in _TEXTY_EXACT


def search(creds: Creds, query: str, max_results: int = 20, *, client=None) -> list[dict]:
    """Full-text search (covers title + content). ``query`` is free text, escaped
    into Drive's ``fullText contains`` syntax. No orderBy — Drive rejects sorting
    on fullText queries."""
    escaped = query.replace("\\", "\\\\").replace("'", "\\'")
    data = request(creds, "GET", BASE, params={
        "q": f"fullText contains '{escaped}' and trashed = false",
        "pageSize": min(int(max_results), 50),
        "fields": f"files({_FIELDS})",
    }, client=client)
    return [_summary(f) for f in (data.get("files") or [])]


def read(creds: Creds, file_id: str, max_chars: int = 20000, *, client=None) -> dict:
    """Fetch one file as text. Docs/Sheets/Slides export; text files download raw;
    binary files return metadata + a readable refusal in ``error``."""
    meta = request(creds, "GET", f"{BASE}/{file_id}", params={"fields": _FIELDS}, client=client)
    out = _summary(meta)
    mime = out["mimeType"]
    if mime in _EXPORT:
        text = request(creds, "GET", f"{BASE}/{file_id}/export",
                       params={"mimeType": _EXPORT[mime]}, client=client, raw=True)
    elif _is_texty(mime):
        text = request(creds, "GET", f"{BASE}/{file_id}", params={"alt": "media"}, client=client, raw=True)
    else:
        out["error"] = f"binary file ({mime}) — not readable as text; open it via the link instead"
        return out
    out["truncated"] = len(text) > int(max_chars)
    out["content"] = text[: int(max_chars)]
    return out
