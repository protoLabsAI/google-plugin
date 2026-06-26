"""Console view for the Google Workspace plugin — connection + at-a-glance summary.

Public page at /plugins/google/view (declared in manifest public_paths); links the
host design-system plugin-kit so it's themed from the operator's live `--pl-*`
tokens, and uses the kit's `apiFetch` to read the gated /api/plugins/google/* routes.
"""

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
  .row{display:flex;align-items:center;justify-content:space-between;padding:7px 0}
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

  async function load(){
    try{
      const r = await kit.apiFetch("/api/plugins/google/status");
      if(!r.ok){ $("status").innerHTML='<div class="err">Status '+r.status+'</div>'; return; }
      const s = await r.json();
      $("status").innerHTML = '<div class="row"><span class="k">Credentials</span><span class="pl-badge pl-badge--'+
        (s.configured?'success':'error')+'">'+(s.configured?'configured':'not set — fill client_id / client_secret / refresh_token')+'</span></div>';
      if(s.configured){ loadMail(); loadCal(); }
      else { $("mail").innerHTML='<div class="empty">Set credentials to load.</div>';
             $("cal").innerHTML='<div class="empty">Set credentials to load.</div>'; }
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


def build_router(creds_fn, gmail_mod, calendar_mod):
    """Page (/plugins/google/view) + gated data (/api/plugins/google/*).

    ``creds_fn`` returns the live Creds; the gmail/calendar modules do the fetch.
    Mounted as two routers by register() (page public via public_paths; data gated)."""
    from fastapi import APIRouter
    from fastapi.responses import HTMLResponse

    page = APIRouter()

    @page.get("/view")
    async def view():
        return HTMLResponse(PAGE)

    data = APIRouter()

    @data.get("/status")
    async def status() -> dict:
        return {"configured": creds_fn().configured()}

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
