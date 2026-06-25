"""Console view for the Google Workspace plugin — connection + at-a-glance summary.

Public page at /plugins/google/view (declared in manifest public_paths); data from
the GATED /api/plugins/google/* routes with the operator bearer.
"""

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Google Workspace</title>
<style>
  :root{--bg:#0a0f14;--fg:#e6e6e6;--muted:#9aa0aa;--card:#11161c;--line:#1f2630;--accent:#9b87f2}
  html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);
    font-family:ui-sans-serif,system-ui,-apple-system,sans-serif;font-size:14px}
  .wrap{max-width:720px;margin:0 auto;padding:24px}
  h1{font-size:18px;margin:0 0 2px} .sub{color:var(--muted);margin:0 0 20px;font-size:13px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:16px}
  .row{display:flex;align-items:center;justify-content:space-between;padding:7px 0;border-bottom:1px solid var(--line)}
  .row:last-child{border-bottom:none} .k{color:var(--muted)}
  .badge{font-weight:600} .ok{color:#46c46a} .no{color:#e5687a}
  .item{padding:8px 0;border-bottom:1px solid var(--line)} .item:last-child{border-bottom:none}
  .from{color:var(--accent);font-weight:600} .meta{color:var(--muted);font-size:12px}
  .empty{color:var(--muted);padding:8px 0} .err{color:#e5687a;font-size:13px}
  h2{font-size:13px;color:var(--muted);margin:0 0 8px;text-transform:uppercase;letter-spacing:.04em}
</style></head><body><div class="wrap">
  <h1>Google Workspace</h1>
  <p class="sub">Gmail (read + draft) and Calendar (read) — drafts only, never sends.</p>
  <div class="card" id="status"><div class="empty">Loading…</div></div>
  <div class="card"><h2>Unread mail</h2><div id="mail"><div class="empty">—</div></div></div>
  <div class="card"><h2>Upcoming events</h2><div id="cal"><div class="empty">—</div></div></div>
</div>
<script>
  var BASE = location.pathname.replace(/\\/plugins\\/google\\/view.*$/, "");
  var TOKEN = "";
  function authed(){ return TOKEN ? {Authorization:"Bearer "+TOKEN} : {}; }
  function esc(s){ return (s||"").replace(/[&<>]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;"}[c];}); }

  async function load(){
    try{
      var r = await fetch(BASE+"/api/plugins/google/status", {headers:authed()});
      if(!r.ok){ document.getElementById("status").innerHTML='<div class="err">Status '+r.status+'</div>'; return; }
      var s = await r.json();
      document.getElementById("status").innerHTML =
        '<div class="row"><span class="k">Credentials</span><span class="badge '+(s.configured?"ok":"no")+'">'+
        (s.configured?"configured":"not set — fill client_id / client_secret / refresh_token")+'</span></div>';
      if(s.configured){ loadMail(); loadCal(); }
      else { document.getElementById("mail").innerHTML='<div class="empty">Set credentials to load.</div>';
             document.getElementById("cal").innerHTML='<div class="empty">Set credentials to load.</div>'; }
    }catch(e){ document.getElementById("status").innerHTML='<div class="err">'+e+'</div>'; }
  }
  async function loadMail(){
    var box=document.getElementById("mail");
    try{
      var d = await (await fetch(BASE+"/api/plugins/google/unread", {headers:authed()})).json();
      if(d.error){ box.innerHTML='<div class="err">'+esc(d.error)+'</div>'; return; }
      if(!d.messages||!d.messages.length){ box.innerHTML='<div class="empty">Inbox zero.</div>'; return; }
      box.innerHTML = d.messages.map(function(m){
        return '<div class="item"><span class="from">'+esc(m.from)+'</span><div>'+esc(m.subject)+
               '</div><div class="meta">'+esc(m.date)+'</div></div>'; }).join("");
    }catch(e){ box.innerHTML='<div class="err">'+e+'</div>'; }
  }
  async function loadCal(){
    var box=document.getElementById("cal");
    try{
      var d = await (await fetch(BASE+"/api/plugins/google/upcoming", {headers:authed()})).json();
      if(d.error){ box.innerHTML='<div class="err">'+esc(d.error)+'</div>'; return; }
      if(!d.events||!d.events.length){ box.innerHTML='<div class="empty">Nothing scheduled.</div>'; return; }
      box.innerHTML = d.events.map(function(e){
        return '<div class="item"><span class="from">'+esc(e.title)+'</span><div class="meta">'+esc(e.start)+'</div></div>'; }).join("");
    }catch(e){ box.innerHTML='<div class="err">'+e+'</div>'; }
  }
  function applyTheme(t){ if(!t)return; if(t.bg)document.body.style.background=t.bg; if(t.fg)document.body.style.color=t.fg; }
  window.addEventListener("message", function(e){
    var m=e.data||{};
    if(m.type==="protoagent:init"){ TOKEN=m.token||""; applyTheme(m.theme); load(); }
    else if(m.type==="protoagent:theme"){ applyTheme(m.theme); }
  });
  setTimeout(function(){ if(!TOKEN) load(); }, 800);
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
