"""The app server behind each standalone Black Label Desktop app.

Serves a real product: a sign-in page → a dashboard of working features that run the app's OWN
vendored code (core/ on PYTHONPATH, separate from ~/ProjectUtah). Stdlib-only HTTP so it always
starts; every product call is wrapped so no-network / no-DB degrades to an honest message.
"""
from __future__ import annotations

import html
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8901
APP_ID = sys.argv[2] if len(sys.argv) > 2 else "black-label-leads"

APPS = {
    "black-label-leads": ("Black Label Leads", "Find anyone, in any market, anywhere in the US — and email them from your own inbox."),
    "black-label-real-estate": ("Black Label Real Estate", "Probate, builders, and 3-mile comps — your deal pipeline."),
    "black-label-marketing": ("Black Label Marketing", "Sites, reels, and Apple-grade creative for your brand."),
    "black-label-trading": ("Black Label Trading", "Any WealthCharts login, any prop firm — just the signals."),
    "sovereign": ("Sovereign", "Your own AI — voice, weather, the works."),
}
LABEL, TAGLINE = APPS.get(APP_ID, (APP_ID, ""))
SIGNIN_LABEL = "Sign in to Claude" if APP_ID == "sovereign" else "Sign in"


def _ok(d):
    return {"ok": True, **d}


def _err(m):
    return {"ok": False, "error": str(m)}


# ── feature endpoints (run the app's OWN vendored product code) ──────────────────────────
def api(path, q):
    g = lambda k, d="": (q.get(k, [d])[0] or d).strip()  # noqa: E731
    try:
        if path == "/api/leads/market":
            from utah.product import leads
            m = g("market", "dentist")
            return _ok({"market": m, "selectors": leads.market_selectors(m),
                        "query": leads.build_market_query((33.2, -84.9, 33.5, -84.5), m)})
        if path == "/api/leads/email":
            from utah.product import contacts
            return _ok({"patterns": contacts.email_patterns(g("first", "John"), g("last", "Doe"),
                                                             g("domain", "acme.com"))})
        if path == "/api/leads/metros":
            from utah.product import leads
            return _ok({"count": len(leads.US_METROS), "metros": [m[0] for m in leads.US_METROS]})
        if path == "/api/re/builders":
            from utah.product import builders
            return _ok({"trades": builders.BUILDER_MARKETS, "radius_mi": 3,
                        "radius_km": builders.THREE_MILES_KM})
        if path == "/api/mkt/site":
            from utah.product import sitegen
            lead = {"name": g("name", "Joe's Plumbing"), "kind": "plumber",
                    "contact": {"phone": "+17705551234", "address": g("city", "Newnan, GA")}}
            try:
                s = sitegen.render(lead)
            except TypeError:
                s = sitegen.render(lead, {})
            return _ok({"chars": len(s or ""), "html": s})
        if path == "/api/trade/firms":
            from utah.product import prop_accounts
            return _ok({"known": prop_accounts.KNOWN_PROP_FIRMS,
                        "note": "any firm name works — this is only a hint list"})
        if path == "/api/sov/info":
            return _ok({"assistant": "Sovereign",
                        "capabilities": ["voice", "weather", "brain on your Claude login",
                                         "dashboard", "self-coding"]})
    except Exception as exc:  # noqa: BLE001
        return _err(f"{type(exc).__name__}: {exc}")
    return _err(f"unknown endpoint {path}")


def api_post(path, body):
    try:
        if path == "/api/trade/connect":
            from utah.product import prop_accounts
            import tempfile
            import pathlib
            prop_accounts.CONNECTIONS_FILE = pathlib.Path(tempfile.gettempdir()) / "bl_conn.json"
            return _ok(prop_accounts.register_connection(
                body.get("client_id", "demo"),
                wealthcharts={"user": body.get("wc_user", ""), "password": body.get("wc_pass", "")},
                prop_firm={"name": body.get("firm", ""), "account": body.get("firm_acct", "")}))
    except Exception as exc:  # noqa: BLE001
        return _err(f"{type(exc).__name__}: {exc}")
    return _err(f"unknown endpoint {path}")


# ── per-app dashboard panels ────────────────────────────────────────────────────────────
PANELS = {
    "black-label-leads": """
      <div class=card><h3>Find any market</h3><p class=sub>Any vertical, anywhere in the US.</p>
        <input id=mkt value=dentist placeholder="dentist, hvac, yoga studio…">
        <button onclick="run('/api/leads/market',{market:v('mkt')})">Search</button></div>
      <div class=card><h3>Find a prospect's email</h3><p class=sub>Pattern + verification, like Apollo.</p>
        <input id=f value=John><input id=l value=Doe><input id=d value=acme.com>
        <button onclick="run('/api/leads/email',{first:v('f'),last:v('l'),domain:v('d')})">Generate</button></div>
      <div class=card><h3>Nationwide reach</h3><p class=sub>Coast-to-coast coverage.</p>
        <button onclick="run('/api/leads/metros',{})">Show US metros</button></div>
    """,
    "black-label-real-estate": """
      <div class=card><h3>Builder finder</h3><p class=sub>Builders within 3 miles of a property.</p>
        <button onclick="run('/api/re/builders',{})">Show trades + radius</button></div>
      <div class=card><h3>Probate pipeline</h3><p class=sub>Statewide capture → comps → heir contact (backend).</p></div>
    """,
    "black-label-marketing": """
      <div class=card><h3>Instant site</h3><p class=sub>A live preview for any business.</p>
        <input id=n value="Joe's Plumbing"><input id=c value="Newnan, GA">
        <button onclick="site()">Build preview</button></div>
      <div id=frame></div>
    """,
    "black-label-trading": """
      <div class=card><h3>Connect your accounts</h3><p class=sub>Any WealthCharts login + any prop firm.</p>
        <input id=wu placeholder="WealthCharts user"><input id=wp placeholder=password type=password>
        <input id=fm value=Apex placeholder="prop firm">
        <button onclick="post('/api/trade/connect',{client_id:'me',wc_user:v('wu'),wc_pass:v('wp'),firm:v('fm')})">Connect</button></div>
      <div class=card><h3>Supported firms</h3><button onclick="run('/api/trade/firms',{})">List</button></div>
    """,
    "sovereign": """
      <div class=card><h3>Your assistant</h3><p class=sub>Voice, weather, the works.</p>
        <button onclick="run('/api/sov/info',{})">Show capabilities</button></div>
    """,
}

CSS = """
*{box-sizing:border-box} body{margin:0;font:15px -apple-system,system-ui,sans-serif;background:#08080a;color:#eee}
.gold{color:#d9b65c} a{color:#d9b65c}
.signin{min-height:100vh;display:flex;align-items:center;justify-content:center;
 background:radial-gradient(1200px 600px at 50% -10%,#1a160b,#08080a)}
.box{width:340px;background:#121214;border:1px solid #2a2616;border-radius:16px;padding:30px 26px;
 box-shadow:0 0 50px rgba(217,182,92,.08)}
.logo{width:72px;height:72px;border-radius:16px;margin:0 auto 14px;display:flex;align-items:center;
 justify-content:center;background:linear-gradient(135deg,#3a2f12,#0d0d0f);border:1px solid #4a3c18;
 font-size:30px;color:#d9b65c;font-weight:700}
.box h1{font-size:19px;margin:0 0 4px;text-align:center} .box p.t{color:#999;font-size:12px;text-align:center;margin:0 0 20px}
input{width:100%;background:#0c0c0e;border:1px solid #2a2a30;color:#eee;border-radius:9px;padding:11px 12px;margin:6px 0;font-size:14px}
.card input{width:auto;margin:3px 6px 3px 0}
button{background:linear-gradient(135deg,#d9b65c,#b8923a);color:#1a1305;border:0;border-radius:9px;
 padding:11px 16px;font-weight:600;cursor:pointer;font-size:14px} button:hover{filter:brightness(1.08)}
.full{width:100%;margin-top:10px} header{display:flex;align-items:center;gap:12px;padding:16px 24px;
 background:#0d0d0f;border-bottom:1px solid #1e1c12}
header .l{width:34px;height:34px;border-radius:9px;background:linear-gradient(135deg,#3a2f12,#0d0d0f);
 border:1px solid #4a3c18;display:flex;align-items:center;justify-content:center;color:#d9b65c;font-weight:700}
header h1{font-size:16px;margin:0} header .so{margin-left:auto;font-size:12px;color:#888;cursor:pointer}
main{padding:22px 24px;max-width:780px} .card{background:#121214;border:1px solid #232328;border-radius:13px;padding:15px 17px;margin:0 0 13px}
.card h3{margin:0 0 3px;font-size:14px;color:#e8d9a8} .sub{color:#888;font-size:12px;margin:0 0 9px}
pre{background:#0c0c0e;border:1px solid #232328;border-radius:9px;padding:12px;white-space:pre-wrap;
 word-break:break-word;color:#9be8b0;max-height:320px;overflow:auto;font-size:12px}
iframe{width:100%;height:340px;border:1px solid #232328;border-radius:10px;background:#fff}
"""


def signin_page():
    initial = html.escape(LABEL[0])
    return f"""<!doctype html><meta charset=utf-8><title>{html.escape(LABEL)}</title><style>{CSS}</style>
<div class=signin><div class=box>
 <div class=logo id=logo>{initial}</div>
 <h1 class=gold>{html.escape(LABEL)}</h1><p class=t>{html.escape(TAGLINE)}</p>
 <input id=email type=email placeholder="Email"><input id=pw type=password placeholder="Password">
 <button class=full onclick=signin()>{html.escape(SIGNIN_LABEL)}</button>
 <p class=t style=margin-top:14px>New here? <a href=# onclick="signin();return false">Create account</a></p>
 <pre id=msg style=display:none></pre>
</div></div>
<script>
 async function signin(){{
   const email=document.getElementById('email').value, pw=document.getElementById('pw').value;
   const m=document.getElementById('msg');
   if(!email||!pw){{m.style.display='block';m.textContent='Enter an email and password to sign in.';return}}
   const r=await fetch('/auth/signin',{{method:'POST',headers:{{'Content-Type':'application/json'}},
     body:JSON.stringify({{email,password:pw}})}});
   if(r.ok){{location.href='/'}}else{{m.style.display='block';m.textContent='Sign-in failed.'}}
 }}
 document.addEventListener('keydown',e=>{{if(e.key==='Enter')signin()}});
</script>"""


def app_page():
    panels = PANELS.get(APP_ID, "<div class=card>Coming soon.</div>")
    initial = html.escape(LABEL[0])
    return f"""<!doctype html><meta charset=utf-8><title>{html.escape(LABEL)}</title><style>{CSS}</style>
<header><div class=l>{initial}</div><h1 class=gold>{html.escape(LABEL)}</h1>
 <span class=so onclick="fetch('/auth/signout').then(()=>location.href='/')">Sign out</span></header>
<main>{panels}<h3 style="font-size:12px;color:#777;margin-top:18px">Output</h3><pre id=out>Try a feature above…</pre></main>
<script>
 const out=document.getElementById('out');
 const v=id=>document.getElementById(id)?document.getElementById(id).value:'';
 const qs=o=>Object.keys(o).map(k=>k+'='+encodeURIComponent(o[k])).join('&');
 const show=d=>out.textContent=JSON.stringify(d,null,2);
 async function run(p,o){{out.textContent='…';try{{show(await (await fetch(p+'?'+qs(o))).json())}}catch(e){{show({{ok:false,error:String(e)}})}}}}
 async function post(p,o){{out.textContent='…';try{{show(await (await fetch(p,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(o)}})).json())}}catch(e){{show({{ok:false,error:String(e)}})}}}}
 async function site(){{out.textContent='building…';try{{const d=await (await fetch('/api/mkt/site?'+qs({{name:v('n'),city:v('c')}}))).json();show({{ok:d.ok,chars:d.chars,error:d.error}});if(d.html){{document.getElementById('frame').innerHTML='<iframe></iframe>';document.querySelector('#frame iframe').srcdoc=d.html}}}}catch(e){{show({{ok:false,error:String(e)}})}}}}
</script>"""


def _authed(handler):
    return "bl_auth=1" in (handler.headers.get("Cookie", "") or "")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json", cookie=None):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/auth/signout":
            return self._send(302, "", "text/html", cookie="bl_auth=; Max-Age=0; Path=/")
        if u.path in ("/", "/index.html"):
            page = app_page() if _authed(self) else signin_page()
            return self._send(200, page, "text/html; charset=utf-8")
        if u.path.startswith("/api/"):
            if not _authed(self):
                return self._send(401, json.dumps(_err("sign in first")))
            return self._send(200, json.dumps(api(u.path, parse_qs(u.query))))
        self._send(404, json.dumps(_err("not found")))

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:  # noqa: BLE001
            body = {}
        if u.path == "/auth/signin":
            if (body.get("email") or "").strip() and (body.get("password") or "").strip():
                return self._send(200, json.dumps(_ok({"signed_in": True})),
                                  cookie="bl_auth=1; Path=/; Max-Age=2592000")
            return self._send(401, json.dumps(_err("email and password required")))
        if not _authed(self):
            return self._send(401, json.dumps(_err("sign in first")))
        self._send(200, json.dumps(api_post(u.path, body)))


if __name__ == "__main__":
    print(f"{LABEL} on http://127.0.0.1:{PORT}/", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
