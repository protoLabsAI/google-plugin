"""Calendar service — read-only. Built on the shared auth core.

Named ``gcal`` (not ``calendar``) so it can never shadow the stdlib ``calendar``
module — stdlib ``email`` imports ``calendar``, so a root-level ``calendar.py``
breaks any Python started from this directory.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .auth import Creds, request

BASE = "https://www.googleapis.com/calendar/v3/calendars"


def _summary(e: dict) -> dict:
    start, end = e.get("start") or {}, e.get("end") or {}
    return {
        "id": e.get("id", ""),
        "title": e.get("summary", ""),
        "start": start.get("dateTime") or start.get("date", ""),
        "end": end.get("dateTime") or end.get("date", ""),
        "attendees": [a.get("email", "") for a in (e.get("attendees") or [])],
        "location": e.get("location", ""),
        "link": e.get("htmlLink", ""),
    }


def list_upcoming(creds: Creds, days: int = 7, calendar_id: str = "primary",
                  *, client=None, now_iso: str | None = None) -> list[dict]:
    start = datetime.now(timezone.utc) if now_iso is None else datetime.fromisoformat(now_iso)
    data = request(creds, "GET", f"{BASE}/{calendar_id}/events", params={
        "timeMin": start.isoformat(),
        "timeMax": (start + timedelta(days=min(int(days), 90))).isoformat(),
        "singleEvents": "true", "orderBy": "startTime", "maxResults": 50,
    }, client=client)
    return [_summary(e) for e in (data.get("items") or [])]


def event_detail(creds: Creds, event_id: str, calendar_id: str = "primary", *, client=None) -> dict:
    e = request(creds, "GET", f"{BASE}/{calendar_id}/events/{event_id}", client=client)
    s = _summary(e)
    s["description"] = e.get("description", "")
    s["attendees"] = [
        {"email": a.get("email", ""), "name": a.get("displayName", ""), "status": a.get("responseStatus", "")}
        for a in (e.get("attendees") or [])
    ]
    org = e.get("organizer") or {}
    s["organizer"] = {"email": org.get("email", ""), "name": org.get("displayName", "")}
    return s
