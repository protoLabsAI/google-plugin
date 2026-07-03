"""Google Workspace plugin — Gmail + Calendar today, built to grow.

Ports Ava's Google tools from protoWorkstacean. Deliberately NOT pigeonholed as
"Gmail + Calendar": a service-agnostic auth core (``auth.py``) backs one module per
service (``gmail.py``, ``gcal.py`` — Drive / Docs / Sheets drop in the same way),
so expanding to the wider Workspace is new tool functions, not a re-architecture.

Pull-mode posture: the agent lists, searches, reads, DRAFTS, and does mailbox
hygiene (mark read, label, archive) — it never sends, deletes, or auto-replies;
a human reviews drafts and sends them. Credentials come from plugin
config (``google.*``) with env fallbacks (``GOOGLE_CLIENT_ID`` /
``GOOGLE_CLIENT_SECRET`` / ``GOOGLE_REFRESH_TOKEN``). Mint the refresh token with the
full scope set you intend to grow into so adding a service needs no re-consent.

NOTE: no top-level relative imports here — pytest imports a repo-root ``__init__.py``
as a nameless top-level module during package setup, where ``from . import x``
explodes. Service imports live inside the functions that use them (pinned by a test).
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING

from langchain_core.tools import tool

if TYPE_CHECKING:
    from .auth import Creds

log = logging.getLogger("protoagent.plugins.google")

_CREDS: Creds | None = None


def _creds() -> Creds:
    global _CREDS
    if _CREDS is None:
        from .auth import Creds

        _CREDS = Creds("", "", "")
    return _CREDS


def _run(fn, *args, **kwargs):
    """Call a service fn, turning config/API errors into a readable tool string."""
    from .auth import GoogleError

    if not _creds().configured():
        return ("Google isn't configured. Set google.client_id / client_secret / refresh_token "
                "(or GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN).")
    try:
        return fn(*args, **kwargs)
    except GoogleError as exc:
        return f"Google error: {exc}"
    except Exception as exc:  # noqa: BLE001 — surface API failures to the agent, don't crash the turn
        log.warning("[google] %s failed: %s", getattr(fn, "__name__", fn), exc)
        return f"Google request failed: {type(exc).__name__}: {exc}"


# ── Gmail (read + draft) ──────────────────────────────────────────────────────

@tool
def gmail_list_unread(label: str = "INBOX", max: int = 20) -> str:
    """List unread Gmail messages in a label (default INBOX). Read-only.

    Args:
        label: Gmail label name (e.g. INBOX, "Personal/Work").
        max: max messages (default 20, cap 100).
    """
    from . import gmail

    # Quote the label: an unquoted multi-word label ("Priority Inbox") would split
    # into label:Priority + free-text "Inbox" in Gmail's query syntax.
    quoted = '"%s"' % label.replace('"', "")
    out = _run(gmail.list_messages, _creds(), f"label:{quoted} is:unread", max)
    return out if isinstance(out, str) else json.dumps({"label": label, "count": len(out), "messages": out}, indent=2)


@tool
def gmail_search(query: str, max: int = 20) -> str:
    """Search Gmail with a query string (from:, subject:, after:, has:attachment, …). Read-only.

    Args:
        query: a Gmail search query.
        max: max messages (default 20, cap 100).
    """
    from . import gmail

    out = _run(gmail.list_messages, _creds(), query, max)
    return out if isinstance(out, str) else json.dumps({"query": query, "count": len(out), "messages": out}, indent=2)


@tool
def gmail_get_thread(thread_id: str) -> str:
    """Read a full Gmail thread (all messages + plain-text bodies). Read-only.

    Args:
        thread_id: the Gmail thread id.
    """
    from . import gmail

    out = _run(gmail.get_thread, _creds(), thread_id)
    return out if isinstance(out, str) else json.dumps({"threadId": thread_id, "count": len(out), "messages": out}, indent=2)


@tool
def gmail_create_draft(body: str, thread_id: str = "", to: str = "", subject: str = "",
                       in_reply_to: str = "", references: str = "") -> str:
    """Create a Gmail DRAFT (never sends — lands in Drafts for a human to review and send).

    Reply into a thread with thread_id (headers auto-resolve), or start a new draft with
    explicit to + subject.

    Args:
        body: plain-text body.
        thread_id: reply into this thread (optional).
        to: recipient email (required when thread_id is omitted).
        subject: subject (required when thread_id is omitted).
        in_reply_to: Message-ID being replied to (optional).
        references: References header (optional).
    """
    from . import gmail

    if not thread_id and not (to and subject):
        return "Provide either thread_id (to reply) or both to + subject (for a new draft)."
    out = _run(gmail.create_draft, _creds(), body, thread_id, to, subject, in_reply_to, references)
    if isinstance(out, str):
        return out
    return f"Draft created (id {out['draftId']}) to {out['to'] or '(thread)'} — \"{out['subject']}\". In Drafts; not sent."


@tool
def gmail_label(message_ids: list[str] | None = None, thread_id: str = "",
                add: list[str] | None = None, remove: list[str] | None = None,
                archive: bool = False) -> str:
    """Add/remove Gmail labels (by name) on messages or a whole thread; archive=True
    removes INBOX (archives — mail stays searchable under All Mail). Labels being
    added are created if missing. Never deletes: adding TRASH/SPAM is refused.

    Args:
        message_ids: message ids (from gmail_list_unread / gmail_search).
        thread_id: apply to every message in this thread instead.
        add: label names to add (e.g. ["Receipts"]).
        remove: label names to remove (e.g. ["INBOX"] — same as archive=True).
        archive: also remove INBOX.
    """
    from . import gmail

    if not thread_id and not message_ids:
        return "Provide message_ids (from gmail_list_unread / gmail_search) or a thread_id."
    remove = list(remove or []) + (["INBOX"] if archive else [])
    if not (add or remove):
        return "Provide labels to add/remove, or archive=True."
    out = _run(gmail.modify_labels, _creds(), message_ids, thread_id, add, remove)
    if isinstance(out, str):
        return out
    what = " + ".join(filter(None, [f"+{', '.join(add)}" if add else "", f"-{', '.join(remove)}" if remove else ""]))
    created = f" (created: {', '.join(out['created'])})" if out.get("created") else ""
    target = f"thread {out['threadId']}" if out.get("threadId") else f"{out['modified']} message(s)"
    return f"Labels {what} applied to {target}.{created}"


def _attachments_dir():
    """Where binary attachments land — the agent workspace when hosted, tmp otherwise."""
    from pathlib import Path

    try:
        from infra.paths import instance_paths  # host-only, lazy

        d = instance_paths().workspace_dir / "attachments"
    except Exception:  # noqa: BLE001 — no host (tests) ⇒ tmp
        import tempfile

        d = Path(tempfile.gettempdir()) / "google-plugin-attachments"
    d.mkdir(parents=True, exist_ok=True)
    return d


_TEXTY_EXTS = {".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml", ".log", ".eml", ".html", ".htm", ".ics"}


@tool
def gmail_get_attachment(message_id: str, attachment_id: str, filename: str = "attachment.bin",
                         max_chars: int = 20000) -> str:
    """Fetch one email attachment (ids from gmail_get_thread's attachments). Text
    files return their content; binaries are saved into the agent workspace and the
    path is returned.

    Args:
        message_id: the message the attachment belongs to.
        attachment_id: the attachment id from gmail_get_thread.
        filename: original filename (drives text-vs-binary handling and the saved name).
        max_chars: truncate text content to this many characters (default 20000).
    """
    import os

    from . import gmail

    out = _run(gmail.get_attachment, _creds(), message_id, attachment_id)
    if isinstance(out, str):
        return out
    safe = os.path.basename(filename).strip() or "attachment.bin"
    ext = os.path.splitext(safe)[1].lower()
    if ext in _TEXTY_EXTS:
        text = out.decode("utf-8", "replace")
        return json.dumps({"filename": safe, "truncated": len(text) > int(max_chars),
                           "content": text[: int(max_chars)]}, indent=2)
    dest = _attachments_dir() / safe
    if dest.exists():
        dest = dest.with_name(f"{message_id[:8]}-{safe}")
    dest.write_bytes(out)
    return json.dumps({"filename": safe, "savedTo": str(dest), "bytes": len(out)}, indent=2)


@tool
def gmail_list_drafts(max: int = 20) -> str:
    """List Gmail drafts (id, to, subject, snippet) — e.g. to find one to revise. Read-only.

    Args:
        max: max drafts (default 20, cap 100).
    """
    from . import gmail

    out = _run(gmail.list_drafts, _creds(), max)
    return out if isinstance(out, str) else json.dumps({"count": len(out), "drafts": out}, indent=2)


@tool
def gmail_update_draft(draft_id: str, body: str, to: str = "", subject: str = "") -> str:
    """Rewrite an existing Gmail draft's body (and optionally to/subject). Reply
    headers and thread are preserved. Still a DRAFT — never sends.

    Args:
        draft_id: the draft id (from gmail_list_drafts or gmail_create_draft).
        body: the new plain-text body (replaces the old one).
        to: override recipient (optional).
        subject: override subject (optional).
    """
    from . import gmail

    out = _run(gmail.update_draft, _creds(), draft_id, body, to, subject)
    if isinstance(out, str):
        return out
    return f"Draft {out['draftId']} updated — to {out['to'] or '(thread)'}, \"{out['subject']}\". In Drafts; not sent."


@tool
def gmail_mark_read(message_ids: list[str] | None = None, thread_id: str = "") -> str:
    """Mark Gmail messages as read (clears the UNREAD label — nothing is archived,
    deleted, or sent). Pass message_ids from gmail_list_unread/gmail_search, OR a
    thread_id to mark a whole thread.

    Args:
        message_ids: message ids to mark read (up to 1000).
        thread_id: mark every message in this thread instead.
    """
    from . import gmail

    if not thread_id and not message_ids:
        return "Provide message_ids (from gmail_list_unread / gmail_search) or a thread_id."
    out = _run(gmail.mark_read, _creds(), message_ids, thread_id)
    if isinstance(out, str):
        return out
    if out.get("threadId"):
        return f"Marked thread {out['threadId']} read ({out['marked']} message(s))."
    return f"Marked {out['marked']} message(s) read."


# ── Calendar (read-only) ──────────────────────────────────────────────────────

@tool
def calendar_list_upcoming(days: int = 7, calendar_id: str = "primary") -> str:
    """List upcoming calendar events over the next N days. Read-only.

    Args:
        days: lookahead window in days (default 7, cap 90).
        calendar_id: calendar id (default "primary").
    """
    from . import gcal

    out = _run(gcal.list_upcoming, _creds(), days, calendar_id)
    return out if isinstance(out, str) else json.dumps({"calendarId": calendar_id, "days": days, "count": len(out), "events": out}, indent=2)


@tool
def calendar_availability(days: int = 7, calendar_id: str = "primary") -> str:
    """Busy time blocks over the next N days (free/busy) — derive free slots from the
    gaps. Read-only.

    Args:
        days: lookahead window in days (default 7, cap 90).
        calendar_id: calendar id (default "primary").
    """
    from . import gcal

    out = _run(gcal.free_busy, _creds(), days, calendar_id)
    return out if isinstance(out, str) else json.dumps(out, indent=2)


@tool
def calendar_search(query: str, days_back: int = 30, days_ahead: int = 180,
                    calendar_id: str = "primary", max: int = 20) -> str:
    """Search calendar events by text in a window around today (past + future). Read-only.

    Args:
        query: free-text search (title, description, attendees).
        days_back: how far back to look (default 30, cap 365).
        days_ahead: how far ahead to look (default 180, cap 365).
        calendar_id: calendar id (default "primary").
        max: max events (default 20, cap 50).
    """
    from . import gcal

    out = _run(gcal.search_events, _creds(), query, days_back, days_ahead, calendar_id, max)
    return out if isinstance(out, str) else json.dumps({"query": query, "count": len(out), "events": out}, indent=2)


@tool
def calendar_event_detail(event_id: str, calendar_id: str = "primary") -> str:
    """Read full detail for one calendar event (description, attendees + RSVP, organizer). Read-only.

    Args:
        event_id: the event id.
        calendar_id: calendar id (default "primary").
    """
    from . import gcal

    out = _run(gcal.event_detail, _creds(), event_id, calendar_id)
    return out if isinstance(out, str) else json.dumps(out, indent=2)


@tool
def calendar_create_event(title: str, start: str, end: str, description: str = "",
                          location: str = "", timezone: str = "", calendar_id: str = "primary") -> str:
    """Create an event on the user's OWN calendar. Cannot invite attendees — that
    would email people, and this plugin never sends. Reversible (delete in Calendar).

    Args:
        title: event title.
        start: ISO datetime (e.g. 2026-07-04T09:00:00-07:00) or YYYY-MM-DD for all-day.
        end: same format as start.
        description: optional notes.
        location: optional location.
        timezone: IANA tz (e.g. America/Los_Angeles) if start/end lack an offset.
        calendar_id: calendar id (default "primary").
    """
    from . import gcal

    out = _run(gcal.create_event, _creds(), title, start, end, description, location, timezone, calendar_id)
    if isinstance(out, str):
        return out
    return f"Event created: \"{out['title']}\" {out['start']} → {out['end']} — {out['link']} (no attendees invited)."


# ── Contacts (read-only) ──────────────────────────────────────────────────────

@tool
def contacts_search(query: str, max: int = 10) -> str:
    """Find people (name, email addresses, org) in the user's Google contacts —
    including auto-collected past correspondents. Read-only. Use before drafting
    mail when you only know a person's name.

    Args:
        query: a name, email fragment, or company.
        max: max people (default 10, cap 30).
    """
    from . import gpeople

    out = _run(gpeople.search, _creds(), query, max)
    return out if isinstance(out, str) else json.dumps({"query": query, "count": len(out), "people": out}, indent=2)


# ── Docs (create-only) ────────────────────────────────────────────────────────

@tool
def docs_create(title: str, text: str = "") -> str:
    """Create a NEW Google Doc in the user's Drive with optional initial text.
    Private until the user shares it; existing docs are never edited.

    Args:
        title: document title.
        text: initial plain-text content (optional).
    """
    from . import gdocs

    out = _run(gdocs.create, _creds(), title, text)
    if isinstance(out, str):
        return out
    return f"Doc created: \"{out['title']}\" — {out['link']}"


# ── Drive (read-only) ─────────────────────────────────────────────────────────

@tool
def drive_search(query: str, max: int = 20) -> str:
    """Search Google Drive by content and title (full-text). Read-only.

    Args:
        query: free-text search (matches file names and contents).
        max: max files (default 20, cap 50).
    """
    from . import gdrive

    out = _run(gdrive.search, _creds(), query, max)
    return out if isinstance(out, str) else json.dumps({"query": query, "count": len(out), "files": out}, indent=2)


@tool
def drive_read(file_id: str, max_chars: int = 20000) -> str:
    """Read one Drive file as text (Docs → text, Sheets → CSV, Slides → text; plain
    text files raw; binary files return metadata + link only). Read-only.

    Args:
        file_id: the Drive file id (from drive_search).
        max_chars: truncate the content to this many characters (default 20000).
    """
    from . import gdrive

    out = _run(gdrive.read, _creds(), file_id, max_chars)
    return out if isinstance(out, str) else json.dumps(out, indent=2)


# Registered tools, grouped by service. Append new service tools here as they land.
TOOLS = [
    gmail_list_unread, gmail_search, gmail_get_thread, gmail_create_draft, gmail_mark_read,
    gmail_label, gmail_get_attachment, gmail_list_drafts, gmail_update_draft,
    calendar_list_upcoming, calendar_event_detail, calendar_availability, calendar_search,
    calendar_create_event, contacts_search, docs_create,
    drive_search, drive_read,
]


def _set_refresh_token(token: str) -> None:
    """Swap the live refresh token in place (the OAuth callback's connect-now hook)."""
    global _CREDS
    from .auth import Creds

    c = _creds()
    _CREDS = Creds(c.client_id, c.client_secret, token)


def register(registry) -> None:
    """Entry point — called once per graph build with the live config."""
    global _CREDS
    from .auth import Creds

    cfg = registry.config or {}
    _CREDS = Creds(
        client_id=cfg.get("client_id") or os.environ.get("GOOGLE_CLIENT_ID", ""),
        client_secret=cfg.get("client_secret") or os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        refresh_token=cfg.get("refresh_token") or os.environ.get("GOOGLE_REFRESH_TOKEN", ""),
    )
    for t in TOOLS:
        registry.register_tool(t)

    # Console view + one-click OAuth connect: public page (/plugins/google/view,
    # /plugins/google/oauth/callback) + gated data (/api/plugins/google/*).
    try:
        from . import gcal, gmail
        from .view import build_router

        page, data = build_router(
            _creds, gmail, gcal,
            scopes_fn=lambda: (registry.live_config() or {}).get("oauth_scopes", ""),
            on_refresh_token=_set_refresh_token,
        )
        registry.register_router(page)  # default prefix /plugins/google (public via public_paths)
        registry.register_router(data, prefix="/api/plugins/google")
    except Exception:  # noqa: BLE001 — view is best-effort
        log.exception("[google] mounting view router failed")

    if not _CREDS.configured():
        log.info("[google] tools registered but credentials not set — they return a setup hint until configured")
