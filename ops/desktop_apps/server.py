"""Local test server behind each Black Label Desktop app.

Each `.app` launches this with its APP_ID; it serves a small browser UI that runs the REAL
product code (imported from ~/ProjectUtah via the launcher's PYTHONPATH). Stdlib-only for the
HTTP layer so it always starts; product calls are wrapped so a missing dep / no-network / no-DB
degrades to an honest message instead of crashing the app.
"""
from __future__ import annotations

import html
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

APP_ID = sys.argv[2] if len(sys.argv) > 2 else "black-label-leads"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8901

APPS = {
    "black-label-leads": "Black Label Leads",
    "black-label-real-estate": "Black Label Real Estate",
    "black-label-marketing": "Black Label Marketing",
    "black-label-trading": "Black Label Trading",
    "sovereign": "Sovereign",
}
LABEL = APPS.get(APP_ID, APP_ID)


def _ok(data):
    return {"ok": True, **data}


def _err(msg):
    return {"ok": False, "error": str(msg)}


# ── per-product handlers (call the REAL utah code; never raise out) ──────────────────────
def api(path: str, q: dict) -> dict:
    g = lambda k: (q.get(k, [""])[0] or "").strip()  # noqa: E731
    try:
        if path == "/api/leads/market":
            from utah.product import leads
            m = g("market") or "dentist"
            return _ok({"market": m, "selectors": leads.market_selectors(m),
                        "query": leads.build_market_query((33.2, -84.9, 33.5, -84.5), m)})
        if path == "/api/leads/patterns":
            from utah.product import contacts
            pats = contacts.email_patterns(g("first") or "John", g("last") or "Doe",
                                           g("domain") or "acme.com")
            return _ok({"patterns": pats})
        if path == "/api/leads/verify":
            from utah.product import contacts
            res = contacts.guess_email(g("first") or "John", g("last") or "Doe",
                                       g("domain") or "acme.com")
            return _ok({"guess": res})
        if path == "/api/leads/metros":
            from utah.product import leads
            return _ok({"count": len(leads.US_METROS),
                        "metros": [m[0] for m in leads.US_METROS], "us_bbox": leads.US_BBOX})
        if path == "/api/re/builders":
            from utah.product import builders
            return _ok({"markets": builders.BUILDER_MARKETS,
                        "radius_km": builders.THREE_MILES_KM, "radius_mi": 3})
        if path == "/api/trade/firms":
            from utah.product import prop_accounts
            return _ok({"known": prop_accounts.KNOWN_PROP_FIRMS,
                        "note": "any firm name is accepted — this list is only a hint"})
        if path == "/api/mkt/site":
            from utah.product import sitegen
            lead = {"name": g("name") or "Joe's Plumbing", "kind": "plumber",
                    "contact": {"phone": "+17705551234",
                                "address": g("city") or "Newnan, GA"}}
            try:
                site_html = sitegen.render(lead)
            except TypeError:
                site_html = sitegen.render(lead, {})  # tolerate (lead, footer) signature
            return _ok({"chars": len(site_html or ""), "html": site_html})
        if path == "/api/sov/info":
            return _ok({"assistant": "Sovereign",
                        "capabilities": ["voice", "weather", "brain on the Claude subscription",
                                         "dashboard", "self-coding"],
                        "claude_login": "populate in the app's Sign-in screen (token → Keychain)"})
    except Exception as exc:  # noqa: BLE001 — a test panel must never crash the app
        return _err(f"{type(exc).__name__}: {exc}")
    return _err(f"unknown endpoint {path}")


def api_post(path: str, body: dict) -> dict:
    try:
        if path == "/api/leads/register":
            from utah import mail
            import tempfile
            import pathlib
            mail.CLIENT_ACCOUNTS_FILE = pathlib.Path(tempfile.gettempdir()) / "bll_client_test.json"
            return _ok(mail.register_client_account(
                body.get("client_id", "demo"),
                {"from": body.get("from", ""), "app_password": body.get("app_password", "")}))
        if path == "/api/trade/connect":
            from utah.product import prop_accounts
            import tempfile
            import pathlib
            prop_accounts.CONNECTIONS_FILE = pathlib.Path(tempfile.gettempdir()) / "blt_conn_test.json"
            return _ok(prop_accounts.register_connection(
                body.get("client_id", "demo"),
                wealthcharts={"user": body.get("wc_user", ""), "password": body.get("wc_pass", "")},
                prop_firm={"name": body.get("firm", ""), "account": body.get("firm_acct", "")}))
    except Exception as exc:  # noqa: BLE001
        return _err(f"{type(exc).__name__}: {exc}")
    return _err(f"unknown endpoint {path}")


# ── per-app UI panels (plain HTML + fetch; no frameworks) ───────────────────────────────
PANELS = {
    "black-label-leads": """
      <div class=panel><h2>Find any market</h2>
        <input id=mkt placeholder="e.g. dentist, hvac, yoga studio" value="dentist">
        <button onclick="run('/api/leads/market',{market:v('mkt')})">Search market</button></div>
      <div class=panel><h2>Find a person's email (pattern + verify)</h2>
        <input id=first placeholder=First value=John><input id=last placeholder=Last value=Doe>
        <input id=dom placeholder=domain.com value=acme.com>
        <button onclick="run('/api/leads/patterns',{first:v('first'),last:v('last'),domain:v('dom')})">Patterns</button>
        <button onclick="run('/api/leads/verify',{first:v('first'),last:v('last'),domain:v('dom')})">Verify (live MX)</button></div>
      <div class=panel><h2>Nationwide coverage</h2>
        <button onclick="run('/api/leads/metros',{})">Show US metros</button></div>
      <div class=panel><h2>Client's own email (autonomous send)</h2>
        <input id=cf placeholder="client email"><input id=cp placeholder="app password">
        <button onclick="post('/api/leads/register',{client_id:'demo',from:v('cf'),app_password:v('cp')})">Register sender</button></div>
    """,
    "black-label-real-estate": """
      <div class=panel><h2>Builder finder</h2>
        <button onclick="run('/api/re/builders',{})">Show builder trades + 3-mile radius</button></div>
      <div class=panel><h2>Probate + 3-mile enrich</h2>
        <p class=muted>Live probate capture + comps/ownership/debt run against the backend DB.</p></div>
    """,
    "black-label-marketing": """
      <div class=panel><h2>Generate a site (Apple-grade pipeline drives video next)</h2>
        <input id=nm placeholder="business name" value="Joe's Plumbing">
        <input id=ci placeholder="city" value="Newnan, GA">
        <button onclick="site()">Build site preview</button></div>
      <div id=siteframe></div>
    """,
    "black-label-trading": """
      <div class=panel><h2>Connect: any WealthCharts login + any prop firm</h2>
        <input id=wu placeholder="WealthCharts user"><input id=wp placeholder="WC password">
        <input id=fm placeholder="prop firm (any name)" value="Apex"><input id=fa placeholder="firm account">
        <button onclick="post('/api/trade/connect',{client_id:'demo',wc_user:v('wu'),wc_pass:v('wp'),firm:v('fm'),firm_acct:v('fa')})">Connect</button></div>
      <div class=panel><h2>Known firms (hint only)</h2>
        <button onclick="run('/api/trade/firms',{})">List</button></div>
    """,
    "sovereign": """
      <div class=panel><h2>Your assistant</h2>
        <button onclick="run('/api/sov/info',{})">Show capabilities + Claude-login</button></div>
    """,
}


def page() -> str:
    panels = PANELS.get(APP_ID, "<div class=panel>No panels.</div>")
    return f"""<!doctype html><html><head><meta charset=utf-8><title>{html.escape(LABEL)}</title>
<style>
 body{{font:15px -apple-system,system-ui,sans-serif;margin:0;background:#0b0b0d;color:#eee}}
 header{{padding:22px 28px;background:#111;border-bottom:1px solid #222;display:flex;align-items:center;gap:14px}}
 header h1{{margin:0;font-size:20px;letter-spacing:.5px}}
 .badge{{font-size:11px;color:#9ae6b4;border:1px solid #2f6b46;border-radius:99px;padding:2px 9px}}
 main{{padding:24px 28px;max-width:760px}}
 .panel{{background:#141417;border:1px solid #26262b;border-radius:12px;padding:16px 18px;margin:0 0 14px}}
 .panel h2{{margin:0 0 10px;font-size:14px;color:#cbd5e0;font-weight:600}}
 input{{background:#0d0d10;border:1px solid #2a2a30;color:#eee;border-radius:8px;padding:8px 10px;margin:3px 6px 3px 0;width:170px}}
 button{{background:#2563eb;color:#fff;border:0;border-radius:8px;padding:8px 14px;cursor:pointer;margin:3px 0}}
 button:hover{{background:#1d4ed8}}
 pre{{background:#0d0d10;border:1px solid #26262b;border-radius:8px;padding:12px;white-space:pre-wrap;word-break:break-word;color:#a7f3d0;max-height:340px;overflow:auto}}
 .muted{{color:#888}} iframe{{width:100%;height:360px;border:1px solid #26262b;border-radius:8px;background:#fff}}
</style></head><body>
<header><h1>{html.escape(LABEL)}</h1><span class=badge>local test build · runs the real code</span></header>
<main>{panels}<h2 style="font-size:13px;color:#888;margin-top:20px">Output</h2><pre id=out>Click a button…</pre></main>
<script>
 const out=document.getElementById('out');
 const v=id=>document.getElementById(id)?document.getElementById(id).value:'';
 function show(d){{out.textContent=JSON.stringify(d,null,2)}}
 function qs(o){{return Object.keys(o).map(k=>k+'='+encodeURIComponent(o[k])).join('&')}}
 async function run(p,o){{out.textContent='…';try{{const r=await fetch(p+'?'+qs(o));show(await r.json())}}catch(e){{show({{ok:false,error:String(e)}})}}}}
 async function post(p,o){{out.textContent='…';try{{const r=await fetch(p,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(o)}});show(await r.json())}}catch(e){{show({{ok:false,error:String(e)}})}}}}
 async function site(){{out.textContent='building…';try{{const r=await fetch('/api/mkt/site?'+qs({{name:v('nm'),city:v('ci')}}));const d=await r.json();show({{ok:d.ok,chars:d.chars,error:d.error}});if(d.html){{document.getElementById('siteframe').innerHTML='<iframe></iframe>';document.querySelector('#siteframe iframe').srcdoc=d.html}}}}catch(e){{show({{ok:false,error:String(e)}})}}}}
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body, ctype="application/json"):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self._send(200, page(), "text/html; charset=utf-8")
        if u.path.startswith("/api/"):
            return self._send(200, json.dumps(api(u.path, parse_qs(u.query))))
        self._send(404, json.dumps(_err("not found")))

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:  # noqa: BLE001
            body = {}
        self._send(200, json.dumps(api_post(u.path, body)))


if __name__ == "__main__":
    print(f"{LABEL} test server on http://127.0.0.1:{PORT}/", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
