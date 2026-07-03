"""Contacts service (People API) — read-only search. Built on the shared auth core.

Searches BOTH saved contacts and "other contacts" (addresses Google auto-collects
from past correspondence — usually where most of them live), merged and deduped.
Scopes: contacts.readonly + contacts.other.readonly.
"""

from __future__ import annotations

from .auth import Creds, request

BASE = "https://people.googleapis.com/v1"

# (endpoint, readMask) — otherContacts supports a narrower mask.
_SOURCES = (
    ("people:searchContacts", "names,emailAddresses,organizations"),
    ("otherContacts:search", "names,emailAddresses"),
)

# The People search cache wants a warmup request (empty query) before real
# queries return fresh results — Google's own recommendation. Once per token.
_WARMED: set[str] = set()


def _person(p: dict) -> dict:
    names = p.get("names") or [{}]
    orgs = p.get("organizations") or [{}]
    return {
        "name": names[0].get("displayName", ""),
        "emails": [e.get("value", "") for e in (p.get("emailAddresses") or []) if e.get("value")],
        "org": orgs[0].get("name", ""),
    }


def search(creds: Creds, query: str, max_results: int = 10, *, client=None) -> list[dict]:
    if creds.refresh_token not in _WARMED:
        for path, mask in _SOURCES:
            request(creds, "GET", f"{BASE}/{path}",
                    params={"query": "", "readMask": mask, "pageSize": 1}, client=client)
        _WARMED.add(creds.refresh_token)
    out, seen = [], set()
    for path, mask in _SOURCES:
        data = request(creds, "GET", f"{BASE}/{path}",
                       params={"query": query, "readMask": mask,
                               "pageSize": min(int(max_results), 30)}, client=client)
        for r in data.get("results") or []:
            person = _person(r.get("person") or {})
            key = (person["name"].lower(), tuple(sorted(person["emails"])))
            if key in seen or not (person["emails"] or person["name"]):
                continue
            seen.add(key)
            out.append(person)
    return out[: int(max_results)]
