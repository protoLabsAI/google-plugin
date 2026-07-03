# google-plugin

**Google Workspace** for a [protoAgent](https://github.com/protoLabsAI/protoAgent) agent — built to grow. Ships **Gmail (read + draft)**, **Calendar (read)**, and **Drive (read)** today over a service-agnostic OAuth/REST core, so Docs / Sheets / further services are additive modules, not a rewrite.

Pull-mode posture: the agent lists, searches, reads, **drafts**, and can **mark mail read** — it never sends, archives, deletes, or auto-replies. A human reviews drafts in the Drafts folder and sends them.

## Tools
- `gmail_list_unread(label, max)` · `gmail_search(query, max)` · `gmail_get_thread(thread_id)` — read.
- `gmail_create_draft(body, thread_id | to+subject, …)` — **draft only, never sends**.
- `gmail_mark_read(message_ids | thread_id)` — clears UNREAD only; never archives/deletes.
- `calendar_list_upcoming(days, calendar_id)` · `calendar_event_detail(event_id, calendar_id)` — read.
- `drive_search(query, max)` · `drive_read(file_id, max_chars)` — read; Docs export as text, Sheets as CSV, Slides as text.

## Architecture
`auth.py` is a service-agnostic OAuth-refresh + REST core; one module per service (`gmail.py`, `gcal.py`, `gdrive.py`). Adding Docs/Sheets is a new module + tools on the same core.

## Connect (one-click OAuth)
Set `google.client_id` + `client_secret` in **Settings ▸ Plugins ▸ Google**, open the **Google** panel, hit **Connect Google**, approve on Google's consent screen — done. The plugin runs the authorization-code flow itself (public callback at `/plugins/google/oauth/callback`, gated by a single-use state nonce) and writes the refresh token into the untracked `secrets.yaml`; it takes effect immediately, no restart.

One-time Google Cloud setup (5 minutes):
1. [console.cloud.google.com](https://console.cloud.google.com) → a project → enable the **Gmail API**, **Google Calendar API** (and **Drive API** if you'll use it).
2. **OAuth consent screen**: user type **Internal** if you're on Google Workspace (no verification, no token expiry); personal Gmail must use External + add yourself as a test user (note: refresh tokens for External apps in *Testing* status expire after 7 days — publish to production to avoid re-connecting weekly).
3. **Credentials ▸ Create credentials ▸ OAuth client ID ▸ Web application**, authorized redirect URIs: `http://localhost:7870/plugins/google/oauth/callback` (add `:7871` for the dev instance; the URI must exactly match the origin you open the console on).
4. Paste the client ID + secret into the plugin settings.

Default scopes requested: `gmail.modify`, `calendar`, `drive.readonly` (override via the `oauth_scopes` setting — request the set you intend to grow into so adding a service needs no re-consent).

Manual fallback (headless / no browser): mint a refresh token yourself and set `google.refresh_token` (or `GOOGLE_REFRESH_TOKEN`); env fallbacks `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` work too.

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
