"""Gmail service — read + draft (never send). Built on the shared auth core.

One of several Workspace service modules; mirrors the protoWorkstacean Gmail tools.
"""

from __future__ import annotations

import base64
from email.utils import formatdate

from .auth import Creds, request

BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


def _header(headers: list[dict], name: str) -> str:
    for h in headers or []:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _summary(msg: dict) -> dict:
    headers = msg.get("payload", {}).get("headers", [])
    return {
        "messageId": msg.get("id", ""),
        "threadId": msg.get("threadId", ""),
        "from": _header(headers, "From"),
        "to": _header(headers, "To"),
        "subject": _header(headers, "Subject"),
        "date": _header(headers, "Date"),
        "snippet": msg.get("snippet", ""),
    }


def _decode(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data.encode()).decode("utf-8", "replace")
    except Exception:
        return ""


def _body(payload: dict, limit: int = 8192) -> str:
    """First text/plain part (fall back to text/html), depth-first."""
    stack = [payload]
    html = ""
    while stack:
        part = stack.pop(0)
        mime, body = part.get("mimeType", ""), part.get("body", {})
        data = body.get("data")
        if mime == "text/plain" and data:
            return _decode(data)[:limit]
        if mime == "text/html" and data and not html:
            html = _decode(data)[:limit]
        stack.extend(part.get("parts", []) or [])
    return html[:limit]


def profile(creds: Creds, *, client=None) -> dict:
    """The connected account's profile — {"emailAddress": ...}. Read-only."""
    return request(creds, "GET", f"{BASE}/profile", client=client)


def list_messages(creds: Creds, q: str, max_results: int, *, client=None) -> list[dict]:
    listing = request(creds, "GET", f"{BASE}/messages",
                      params={"q": q, "maxResults": min(int(max_results), 100)}, client=client)
    out = []
    for m in listing.get("messages") or []:
        full = request(creds, "GET", f"{BASE}/messages/{m['id']}",
                       params={"format": "metadata",
                               "metadataHeaders": ["From", "To", "Subject", "Date"]}, client=client)
        out.append(_summary(full))
    return out


def get_thread(creds: Creds, thread_id: str, *, client=None) -> list[dict]:
    data = request(creds, "GET", f"{BASE}/threads/{thread_id}", params={"format": "full"}, client=client)
    out = []
    for m in data.get("messages", []):
        s = _summary(m)
        s["body"] = _body(m.get("payload", {}))
        out.append(s)
    return out


def build_draft_raw(body: str, to: str = "", subject: str = "",
                    in_reply_to: str = "", references: str = "") -> str:
    """RFC-822 → base64url, the shape drafts.create wants."""
    lines = []
    if to:
        lines.append(f"To: {to}")
    if subject:
        lines.append(f"Subject: {subject}")
    if in_reply_to:
        lines.append(f"In-Reply-To: {in_reply_to}")
    if references:
        lines.append(f"References: {references}")
    lines += [f"Date: {formatdate(localtime=True)}", "MIME-Version: 1.0",
              "Content-Type: text/plain; charset=utf-8"]
    raw = ("\r\n".join(lines) + "\r\n\r\n" + (body or "")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode()


def create_draft(creds: Creds, body: str, thread_id: str = "", to: str = "", subject: str = "",
                 in_reply_to: str = "", references: str = "", *, client=None) -> dict:
    """Create a DRAFT (never sends). Resolves reply headers from the thread when omitted."""
    if thread_id and not (to and subject):
        t = request(creds, "GET", f"{BASE}/threads/{thread_id}",
                    params={"format": "metadata",
                            "metadataHeaders": ["From", "Subject", "Message-ID", "References"]}, client=client)
        msgs = t.get("messages", [])
        if msgs:
            h = msgs[-1].get("payload", {}).get("headers", [])
            to = to or _header(h, "From")
            subject = subject or _header(h, "Subject")
            in_reply_to = in_reply_to or _header(h, "Message-ID")
            references = references or _header(h, "References") or in_reply_to
    message: dict = {"raw": build_draft_raw(body, to, subject, in_reply_to, references)}
    if thread_id:
        message["threadId"] = thread_id
    d = request(creds, "POST", f"{BASE}/drafts", json={"message": message}, client=client)
    msg = d.get("message") or {}
    return {"draftId": d.get("id", ""), "messageId": msg.get("id", ""),
            "threadId": msg.get("threadId", thread_id), "to": to, "subject": subject, "sent": False}
