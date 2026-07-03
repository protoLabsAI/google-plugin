"""Console view for the Google Workspace plugin — connection + at-a-glance summary.

Public page at /plugins/google/view (declared in manifest public_paths); links the
host design-system plugin-kit so it's themed from the operator's live `--pl-*`
tokens, and uses the kit's `apiFetch` to read the gated /api/plugins/google/* routes.

Also owns the one-click OAuth connect routes (see oauth.py): the gated START
(operator-only, mints the consent URL) and the public CALLBACK (Google's redirect
lands here; validated by the single-use state nonce START minted).
"""

import html

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Google Workspace</title>
<script>
  window.__base = location.pathname.split("/plugins/")[0];
  document.write('<link rel="stylesheet" href="' + window.__base + '/_ds/plugin-kit.css">');
</script>
<style>
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;background:var(--pl-color-bg-raised);color:var(--pl-color-fg);
    font-family:var(--pl-font-sans,ui-sans-serif,system-ui,sans-serif);font-size:13px}
  .wrap{max-width:760px;margin:0 auto;padding:20px}
  h1{font-size:17px;margin:0 0 2px} .sub{color:var(--pl-color-fg-muted);margin:0 0 18px;font-size:12px}
  .pl-card{margin-bottom:14px}
  h2{font-size:11px;color:var(--pl-color-fg-muted);margin:0 0 10px;text-transform:uppercase;letter-spacing:.05em}
  .row{display:flex;align-items:center;justify-content:space-between;padding:7px 0;gap:10px}
  .k{color:var(--pl-color-fg-muted)}
  .item{padding:8px 0;border-bottom:1px solid var(--pl-color-border)} .item:last-child{border-bottom:none}
  .from{color:var(--pl-color-accent);font-weight:var(--pl-font-weight-semibold,600)}
  .meta{color:var(--pl-color-fg-muted);font-size:12px}
  .empty{color:var(--pl-color-fg-muted);padding:8px 0} .err{color:var(--pl-color-status-error);font-size:12px}
</style></head><body>
<div class="wrap">
  <h1>Google Workspace</h1>
  <p class="sub">Gmail (read + draft) and Calendar (read) — drafts only, never sends.</p>
  <div class="pl-card" id="status"><div class="empty">Loading…</div></div>
  <div class="pl-card"><h2>Unread mail</h2><div id="mail"><div class="empty">—</div></div></div>
  <div class="pl-card"><h2>Upcoming events</h2><div id="cal"><div class="empty">—</div></div></div>
</div>
<script type="module">
  "use strict";
  let kit;
  try { kit = await import(window.__base + "/_ds/plugin-kit.js"); }
  catch (e) { kit = { initPluginView(cb){ cb && cb(); }, apiFetch:(p,i)=>fetch(window.__base+p,i) }; }
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s==null?"":s).replace(/[&<>"]/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;" }[c]));
  let pollTimer = null;

  function statusRows(s){
    const badge = s.configured
      ? '<span class="pl-badge pl-badge--success">connected' + (s.email ? " — " + esc(s.email) : "") + '</span>'
      : (s.has_client
        ? '<button class="pl-btn pl-btn--primary" id="connect">Connect Google</button>'
        : '<span class="pl-badge pl-badge--error">set client_id / client_secret in Settings ▸ Plugins ▸ Google</span>');
    let rows = '<div class="row"><span class="k">Account</span>' + badge + '</div>';
    if (s.configured) rows += '<div class="row"><span class="k"></span><button class="pl-btn" id="connect">Reconnect</button></div>';
    return rows;
  }

  async function connect(){
    try{
      const r = await kit.apiFetch("/api/plugins/google/oauth/start", {method:"POST"});
      const d = await r.json();
      if(d.error){ $("status").insertAdjacentHTML("beforeend", '<div class="err">'+esc(d.error)+'</div>'); return; }
      window.open(d.url, "_blank");
      // Poll while the operator approves in the other tab; refresh once connected.
      let tries = 0;
      clearInterval(pollTimer);
      pollTimer = setInterval(async () => {
        if (++tries > 60) { clearInterval(pollTimer); return; }
        const s = await kit.apiFetch("/api/plugins/google/status").then(r=>r.json()).catch(()=>null);
        if (s && s.configured) { clearInterval(pollTimer); load(); }
      }, 3000);
    }catch(e){ $("status").insertAdjacentHTML("beforeend", '<div class="err">'+esc(e)+'</div>'); }
  }

  async function load(){
    try{
      const r = await kit.apiFetch("/api/plugins/google/status");
      if(!r.ok){ $("status").innerHTML='<div class="err">Status '+r.status+'</div>'; return; }
      const s = await r.json();
      $("status").innerHTML = statusRows(s);
      const btn = $("connect");
      if (btn) btn.addEventListener("click", connect);
      if(s.configured){ loadMail(); loadCal(); }
      else { $("mail").innerHTML='<div class="empty">Connect to load.</div>';
             $("cal").innerHTML='<div class="empty">Connect to load.</div>'; }
    }catch(e){ $("status").innerHTML='<div class="err">'+esc(e)+'</div>'; }
  }
  async function loadMail(){
    const box=$("mail");
    try{
      const d = await kit.apiFetch("/api/plugins/google/unread").then(r=>r.json());
      if(d.error){ box.innerHTML='<div class="err">'+esc(d.error)+'</div>'; return; }
      if(!d.messages || !d.messages.length){ box.innerHTML='<div class="empty">Inbox zero.</div>'; return; }
      box.innerHTML = d.messages.map(m => '<div class="item"><span class="from">'+esc(m.from)+'</span><div>'+esc(m.subject)+'</div><div class="meta">'+esc(m.date)+'</div></div>').join("");
    }catch(e){ box.innerHTML='<div class="err">'+esc(e)+'</div>'; }
  }
  async function loadCal(){
    const box=$("cal");
    try{
      const d = await kit.apiFetch("/api/plugins/google/upcoming").then(r=>r.json());
      if(d.error){ box.innerHTML='<div class="err">'+esc(d.error)+'</div>'; return; }
      if(!d.events || !d.events.length){ box.innerHTML='<div class="empty">Nothing scheduled.</div>'; return; }
      box.innerHTML = d.events.map(e => '<div class="item"><span class="from">'+esc(e.title)+'</span><div class="meta">'+esc(e.start)+'</div></div>').join("");
    }catch(e){ box.innerHTML='<div class="err">'+esc(e)+'</div>'; }
  }
  kit.initPluginView(load);
  load();
</script></body></html>"""


_CALLBACK_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Google Workspace — {title}</title>
<style>body{{font-family:ui-sans-serif,system-ui,sans-serif;display:grid;place-items:center;height:100vh;margin:0}}
main{{text-align:center;max-width:32rem;padding:1rem}}</style></head>
<body><main><h1>{title}</h1><p>{detail}</p></main></body></html>"""


def _callback_html(title: str, detail: str, status: int = 200):
    from fastapi.responses import HTMLResponse

    return HTMLResponse(_CALLBACK_PAGE.format(title=html.escape(title), detail=html.escape(detail)), status_code=status)


def _redirect_uri(request) -> str:
    """The callback URL on the origin the operator is actually using.

    Derived from the live request so :7870 / :7871 / a LAN hostname all work —
    Google requires an EXACT match with a registered redirect URI, so whatever
    this returns must be in the OAuth client's authorized redirect URIs.
    """
    return str(request.base_url).rstrip("/") + "/plugins/google/oauth/callback"


def build_router(creds_fn, gmail_mod, calendar_mod, *, scopes_fn=None, on_refresh_token=None):
    """Page (/plugins/google/view + OAuth callback) + gated data (/api/plugins/google/*).

    ``creds_fn`` returns the live Creds; the gmail/calendar modules do the fetch.
    ``scopes_fn`` returns the configured scope override (blank = oauth.DEFAULT_SCOPES);
    ``on_refresh_token`` lets the callback swap the plugin's in-memory creds so the
    connect takes effect without a restart. Mounted as two routers by register()
    (page public via public_paths; data gated)."""
    from fastapi import APIRouter, Request
    from fastapi.responses import HTMLResponse

    from . import oauth

    page = APIRouter()

    @page.get("/view")
    async def view():
        return HTMLResponse(PAGE)

    @page.get("/oauth/callback")
    async def oauth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
        if error:
            return _callback_html("Connect cancelled", f"Google returned: {error}. Close this tab and retry.", 400)
        if not oauth.claim_state(state):
            return _callback_html(
                "Connect expired", "This link's state nonce is unknown or stale — restart from the Google panel.", 400
            )
        c = creds_fn()
        try:
            payload = oauth.exchange(c.client_id, c.client_secret, code, _redirect_uri(request))
        except oauth.OAuthFlowError as exc:
            return _callback_html("Connect failed", str(exc), 400)
        token = payload["refresh_token"]
        if on_refresh_token is not None:
            on_refresh_token(token)
        if oauth.persist_refresh_token("google", token):
            return _callback_html("Google connected ✓", "Close this tab and return to the console.")
        return _callback_html(
            "Google connected (this session only)",
            "The refresh token couldn't be written to secrets.yaml — it's active until restart. "
            "Paste it into Settings ▸ Plugins ▸ Google to keep it.",
        )

    data = APIRouter()

    @data.post("/oauth/start")
    async def oauth_start(request: Request) -> dict:
        c = creds_fn()
        if not (c.client_id and c.client_secret):
            return {"error": "Set client_id + client_secret first (Settings ▸ Plugins ▸ Google)."}
        scopes = (scopes_fn() if scopes_fn else "") or ""
        return {"url": oauth.begin(c.client_id, _redirect_uri(request), scopes)}

    @data.get("/status")
    async def status() -> dict:
        c = creds_fn()
        out = {"configured": c.configured(), "has_client": bool(c.client_id and c.client_secret)}
        if out["configured"]:
            try:
                out["email"] = gmail_mod.profile(c).get("emailAddress", "")
            except Exception:  # noqa: BLE001 — status must render even if the API call fails
                pass
        return out

    @data.get("/unread")
    async def unread() -> dict:
        c = creds_fn()
        if not c.configured():
            return {"messages": []}
        try:
            return {"messages": gmail_mod.list_messages(c, "label:INBOX is:unread", 10)}
        except Exception as exc:  # noqa: BLE001
            return {"messages": [], "error": f"{type(exc).__name__}: {exc}"}

    @data.get("/upcoming")
    async def upcoming() -> dict:
        c = creds_fn()
        if not c.configured():
            return {"events": []}
        try:
            return {"events": calendar_mod.list_upcoming(c, 7)}
        except Exception as exc:  # noqa: BLE001
            return {"events": [], "error": f"{type(exc).__name__}: {exc}"}

    return page, data
