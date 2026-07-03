# google-plugin

**Google Workspace** for a [protoAgent](https://github.com/protoLabsAI/protoAgent) agent — built to grow. Ships **Gmail (read + draft)** and **Calendar (read)** today over a service-agnostic OAuth/REST core, so Drive / Docs / Sheets are additive modules, not a rewrite.

Pull-mode posture: the agent lists, searches, reads, and **drafts** — it never sends or auto-replies. A human reviews drafts in the Drafts folder and sends them.

## Tools
- `gmail_list_unread(label, max)` · `gmail_search(query, max)` · `gmail_get_thread(thread_id)` — read.
- `gmail_create_draft(body, thread_id | to+subject, …)` — **draft only, never sends**.
- `calendar_list_upcoming(days, calendar_id)` · `calendar_event_detail(event_id, calendar_id)` — read.

## Architecture
`auth.py` is a service-agnostic OAuth-refresh + REST core; one module per service (`gmail.py`, `calendar.py`). Adding Drive/Docs/Sheets is a new module + tools on the same core.

## Config
`google.client_id` / `client_secret` / `refresh_token` (or `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REFRESH_TOKEN`). **Mint the refresh token with the full scope set you intend to grow into** (e.g. `gmail.modify`, `calendar`, `drive`, `documents`, `spreadsheets`) so adding a service needs no re-consent.

## Install
```bash
python -m server plugin install https://github.com/protoLabsAI/google-plugin
# then add `google` to plugins.enabled and set the credentials, then restart
```

## Test
Host-free — no protoAgent checkout needed:
```bash
pip install -r requirements-dev.txt && pytest -q
```

Ported from the `gmail_*` / `calendar_*` tools of protoWorkstacean's Ava agent.
