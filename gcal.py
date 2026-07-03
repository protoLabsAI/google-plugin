"""Calendar service — read-only. Built on the shared auth core.

Named ``gcal`` (not ``calendar``) so it can never shadow the stdlib ``calendar``
module — stdlib ``email`` imports ``calendar``, so a root-level ``calendar.py``
breaks any Python started from this directory.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .auth import Creds, request

API = "https://www.googleapis.com/calendar/v3"
BASE = f"{API}/calendars"


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


def free_busy(creds: Creds, days: int = 7, calendar_id: str = "primary",
              *, client=None, now_iso: str | None = None) -> dict:
    """Busy blocks over the next N days — the agent derives free slots from the gaps."""
    start = datetime.now(timezone.utc) if now_iso is None else datetime.fromisoformat(now_iso)
    end = start + timedelta(days=min(int(days), 90))
    data = request(creds, "POST", f"{API}/freeBusy", json={
        "timeMin": start.isoformat(), "timeMax": end.isoformat(), "items": [{"id": calendar_id}],
    }, client=client)
    cal = (data.get("calendars") or {}).get(calendar_id) or {}
    out = {"timeMin": start.isoformat(), "timeMax": end.isoformat(), "busy": cal.get("busy") or []}
    if cal.get("errors"):
        out["errors"] = cal["errors"]
    return out


def search_events(creds: Creds, query: str, days_back: int = 30, days_ahead: int = 180,
                  calendar_id: str = "primary", max_results: int = 20,
                  *, client=None, now_iso: str | None = None) -> list[dict]:
    """Text search over events in a window around now (past and future)."""
    now = datetime.now(timezone.utc) if now_iso is None else datetime.fromisoformat(now_iso)
    data = request(creds, "GET", f"{BASE}/{calendar_id}/events", params={
        "q": query,
        "timeMin": (now - timedelta(days=min(int(days_back), 365))).isoformat(),
        "timeMax": (now + timedelta(days=min(int(days_ahead), 365))).isoformat(),
        "singleEvents": "true", "orderBy": "startTime", "maxResults": min(int(max_results), 50),
    }, client=client)
    return [_summary(e) for e in (data.get("items") or [])]


def create_event(creds: Creds, title: str, start: str, end: str, description: str = "",
                 location: str = "", timezone_name: str = "", calendar_id: str = "primary",
                 *, client=None) -> dict:
    """Create an event on the user's OWN calendar. Deliberately takes NO attendees —
    inviting people emails them, which crosses the plugin's never-send line.

    ``start``/``end``: ISO datetimes (offset or ``timezone_name`` required) or bare
    YYYY-MM-DD dates for all-day events.
    """
    def _when(value: str) -> dict:
        if "T" not in value:
            return {"date": value}
        out = {"dateTime": value}
        if timezone_name:
            out["timeZone"] = timezone_name
        return out

    body: dict = {"summary": title, "start": _when(start), "end": _when(end)}
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    e = request(creds, "POST", f"{BASE}/{calendar_id}/events", json=body, client=client)
    return _summary(e)


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
