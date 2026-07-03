"""Google Workspace plugin — Gmail + Calendar today, built to grow.

Ports Ava's Google tools from protoWorkstacean. Deliberately NOT pigeonholed as
"Gmail + Calendar": a service-agnostic auth core (``auth.py``) backs one module per
service (``gmail.py``, ``gcal.py`` — Drive / Docs / Sheets drop in the same way),
so expanding to the wider Workspace is new tool functions, not a re-architecture.

Pull-mode posture: the agent lists, searches, reads, and DRAFTS — it never sends or
auto-replies; a human reviews drafts and sends them. Credentials come from plugin
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

    out = _run(gmail.list_messages, _creds(), f"label:{label} is:unread", max)
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
def calendar_event_detail(event_id: str, calendar_id: str = "primary") -> str:
    """Read full detail for one calendar event (description, attendees + RSVP, organizer). Read-only.

    Args:
        event_id: the event id.
        calendar_id: calendar id (default "primary").
    """
    from . import gcal

    out = _run(gcal.event_detail, _creds(), event_id, calendar_id)
    return out if isinstance(out, str) else json.dumps(out, indent=2)


# Registered tools, grouped by service. Append new service tools here as they land.
TOOLS = [
    gmail_list_unread, gmail_search, gmail_get_thread, gmail_create_draft,
    calendar_list_upcoming, calendar_event_detail,
]


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

    # Console view: public page (/plugins/google/view) + gated data (/api/plugins/google/*).
    try:
        from . import gcal, gmail
        from .view import build_router

        page, data = build_router(_creds, gmail, gcal)
        registry.register_router(page)  # default prefix /plugins/google (public via public_paths)
        registry.register_router(data, prefix="/api/plugins/google")
    except Exception:  # noqa: BLE001 — view is best-effort
        log.exception("[google] mounting view router failed")

    if not _CREDS.configured():
        log.info("[google] tools registered but credentials not set — they return a setup hint until configured")
