"""Gmail service — read, draft (never send), and mark-read. Built on the shared auth core.

One of several Workspace service modules; mirrors the protoWorkstacean Gmail tools.
Mark-read is the one mailbox mutation beyond drafts: it only clears the UNREAD
label — it never archives, deletes, or sends.
"""

from __future__ import annotations

import base64
from email.utils import formatdate

from .auth import Creds, GoogleError, request

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


def _attachments(payload: dict) -> list[dict]:
    """Real attachments (named parts with an attachmentId), depth-first."""
    out, stack = [], [payload]
    while stack:
        part = stack.pop(0)
        body = part.get("body", {})
        if part.get("filename") and body.get("attachmentId"):
            out.append({
                "filename": part["filename"],
                "mimeType": part.get("mimeType", ""),
                "size": body.get("size", 0),
                "attachmentId": body["attachmentId"],
            })
        stack.extend(part.get("parts", []) or [])
    return out


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
        atts = _attachments(m.get("payload", {}))
        if atts:
            s["attachments"] = atts
        out.append(s)
    return out


def get_attachment(creds: Creds, message_id: str, attachment_id: str, *, client=None) -> bytes:
    """Fetch one attachment's raw bytes (ids come from get_thread's attachments)."""
    data = request(creds, "GET", f"{BASE}/messages/{message_id}/attachments/{attachment_id}", client=client)
    return base64.urlsafe_b64decode(str(data.get("data", "")).encode())


def list_labels(creds: Creds, *, client=None) -> list[dict]:
    data = request(creds, "GET", f"{BASE}/labels", client=client)
    return [{"id": lb.get("id", ""), "name": lb.get("name", ""), "type": lb.get("type", "")}
            for lb in (data.get("labels") or [])]


def modify_labels(creds: Creds, message_ids: list[str] | None = None, thread_id: str = "",
                  add: list[str] | None = None, remove: list[str] | None = None, *, client=None) -> dict:
    """Add/remove labels by NAME on messages (batchModify) or a whole thread.

    Unknown labels being ADDED are created (a new label is harmless); unknown
    labels being REMOVED are an error. Posture guard: adding TRASH or SPAM is
    refused — this plugin never deletes or spams mail (archiving = removing INBOX).
    """
    add = [a for a in (add or []) if a]
    remove = [r for r in (remove or []) if r]
    forbidden = sorted({a.upper() for a in add} & {"TRASH", "SPAM"})
    if forbidden:
        raise GoogleError(f"refusing to add {forbidden} — this plugin never deletes or spams mail")
    existing = {lb["name"].lower(): lb["id"] for lb in list_labels(creds, client=client)}
    add_ids, created = [], []
    for name in add:
        lid = existing.get(name.lower())
        if lid is None:
            made = request(creds, "POST", f"{BASE}/labels", json={"name": name}, client=client)
            lid = made["id"]
            created.append(name)
        add_ids.append(lid)
    remove_ids = []
    for name in remove:
        lid = existing.get(name.lower())
        if lid is None:
            known = ", ".join(sorted(existing)) or "none"
            raise GoogleError(f"no label named {name!r} to remove — labels: {known}")
        remove_ids.append(lid)
    payload: dict = {}
    if add_ids:
        payload["addLabelIds"] = add_ids
    if remove_ids:
        payload["removeLabelIds"] = remove_ids
    if thread_id:
        data = request(creds, "POST", f"{BASE}/threads/{thread_id}/modify", json=payload, client=client)
        return {"threadId": thread_id, "modified": len(data.get("messages") or []) or 1, "created": created}
    ids = [i for i in (message_ids or []) if i][:1000]
    request(creds, "POST", f"{BASE}/messages/batchModify", json={**payload, "ids": ids}, client=client)
    return {"modified": len(ids), "created": created}


def list_drafts(creds: Creds, max_results: int = 20, *, client=None) -> list[dict]:
    listing = request(creds, "GET", f"{BASE}/drafts",
                      params={"maxResults": min(int(max_results), 100)}, client=client)
    out = []
    for d in listing.get("drafts") or []:
        full = request(creds, "GET", f"{BASE}/drafts/{d['id']}", params={"format": "metadata"}, client=client)
        s = _summary(full.get("message") or {})
        s["draftId"] = d["id"]
        out.append(s)
    return out


def update_draft(creds: Creds, draft_id: str, body: str, to: str = "", subject: str = "", *, client=None) -> dict:
    """Replace a draft's body (and optionally to/subject), preserving reply headers
    and thread. Still a DRAFT — never sends."""
    cur = request(creds, "GET", f"{BASE}/drafts/{draft_id}",
                  params={"format": "metadata",
                          "metadataHeaders": ["To", "Subject", "In-Reply-To", "References"]}, client=client)
    msg = cur.get("message") or {}
    h = msg.get("payload", {}).get("headers", [])
    to = to or _header(h, "To")
    subject = subject or _header(h, "Subject")
    message: dict = {"raw": build_draft_raw(body, to, subject, _header(h, "In-Reply-To"), _header(h, "References"))}
    if msg.get("threadId"):
        message["threadId"] = msg["threadId"]
    d = request(creds, "PUT", f"{BASE}/drafts/{draft_id}", json={"message": message}, client=client)
    return {"draftId": d.get("id", draft_id), "to": to, "subject": subject, "sent": False}


def mark_read(creds: Creds, message_ids: list[str] | None = None, thread_id: str = "", *, client=None) -> dict:
    """Clear the UNREAD label from specific messages (batchModify) or a whole thread.

    The only mutation this performs is removing UNREAD — nothing is archived,
    deleted, or sent. Needs the gmail.modify scope.
    """
    if thread_id:
        data = request(creds, "POST", f"{BASE}/threads/{thread_id}/modify",
                       json={"removeLabelIds": ["UNREAD"]}, client=client)
        return {"threadId": thread_id, "marked": len(data.get("messages") or []) or 1}
    ids = [i for i in (message_ids or []) if i][:1000]  # batchModify caps at 1000
    request(creds, "POST", f"{BASE}/messages/batchModify",
            json={"ids": ids, "removeLabelIds": ["UNREAD"]}, client=client)  # 204 on success
    return {"marked": len(ids)}


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
