#!/usr/bin/env python3
"""Interactive local setup wizard for the gateway's .env file.

Run with `python3 setup_wizard.py` (stdlib only, no pip install needed —
this has to work before you've built anything). Opens a local-only web
form, walks through Tailscale + backend + STT config, and writes the
answers straight to `.env`. Safe to re-run any time to edit an existing
`.env` — it prefills from whatever's already there.
"""
from __future__ import annotations

import html
import http.server
import os
import socketserver
import threading
import urllib.parse
import webbrowser

PORT = 8765
ROOT = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(ROOT, ".env")
EXAMPLE_PATH = os.path.join(ROOT, ".env.example")

FIELDS = [
    "TAILSCALE_AUTH_KEY",
    "TAILSCALE_HOSTNAME",
    "BACKEND",
    "HERMES_BASE_URL",
    "HERMES_API_KEY",
    "LM_STUDIO_BASE_URL",
    "LM_STUDIO_MODEL",
    "SOGNI_BASE_URL",
    "SOGNI_STT_PATH",
    "SOGNI_API_KEY",
    "SOGNI_ENGINE",
    "SOGNI_LANGUAGE",
]

DEFAULTS = {
    "TAILSCALE_HOSTNAME": "agent-relay-even-g2",
    "BACKEND": "hermes",
    "SOGNI_STT_PATH": "/transcribe",
    "SOGNI_ENGINE": "qwen3",
    "SOGNI_LANGUAGE": "en",
}


def parse_existing_env() -> dict[str, str]:
    path = ENV_PATH if os.path.exists(ENV_PATH) else EXAMPLE_PATH
    values: dict[str, str] = {}
    if not os.path.exists(path):
        return values
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip()
    return values


def render_form() -> str:
    values = {**DEFAULTS, **parse_existing_env()}
    v = lambda k: html.escape(values.get(k, ""))
    is_hermes = values.get("BACKEND", "hermes") != "lm_studio"

    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>Agent Relay gateway setup</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 640px;
         margin: 40px auto; padding: 0 20px; color: #1a1a1a; }}
  h1 {{ font-size: 1.4em; }}
  h2 {{ font-size: 1.05em; margin-top: 2em; border-bottom: 1px solid #ddd; padding-bottom: 4px; }}
  label {{ display: block; margin-top: 14px; font-weight: 600; font-size: 0.9em; }}
  .hint {{ font-weight: 400; color: #666; font-size: 0.85em; margin-top: 2px; }}
  input, select {{ width: 100%; box-sizing: border-box; padding: 7px 9px;
                   margin-top: 4px; border: 1px solid #ccc; border-radius: 6px;
                   font-size: 0.95em; }}
  .backend-block {{ display: none; padding-left: 12px; border-left: 2px solid #eee; }}
  .backend-block.active {{ display: block; }}
  button {{ margin-top: 28px; padding: 10px 20px; font-size: 1em; border: 0;
           border-radius: 6px; background: #1a1a1a; color: white; cursor: pointer; }}
</style>
</head>
<body>
<h1>Agent Relay gateway setup</h1>
<p>Fills in <code>.env</code> for you. Re-run this any time to edit an
existing setup — it prefills from what's already there.</p>

<form method="POST" action="/">

  <h2>Tailscale</h2>
  <label>Auth key
    <span class="hint">Reusable key — get one at
      <a href="https://login.tailscale.com/admin/settings/keys" target="_blank">login.tailscale.com/admin/settings/keys</a>
      (must be reusable, not single-use — the container restarts during development)</span>
  </label>
  <input name="TAILSCALE_AUTH_KEY" value="{v('TAILSCALE_AUTH_KEY')}" required>

  <label>Hostname on your tailnet
    <span class="hint">This becomes the address you enter in the glasses app's setup screen</span>
  </label>
  <input name="TAILSCALE_HOSTNAME" value="{v('TAILSCALE_HOSTNAME')}">

  <h2>AI backend</h2>
  <label>
    <input type="radio" name="BACKEND" value="hermes" style="width:auto;display:inline"
      {"checked" if is_hermes else ""} onchange="showBackend('hermes')">
    Hermes (native multi-session agent) —
    <a href="https://github.com/NousResearch" target="_blank">github.com/NousResearch</a>
  </label>
  <label>
    <input type="radio" name="BACKEND" value="lm_studio" style="width:auto;display:inline"
      {"checked" if not is_hermes else ""} onchange="showBackend('lm_studio')">
    LM Studio / Ollama / any OpenAI-compatible server —
    <a href="https://lmstudio.ai/" target="_blank">lmstudio.ai</a> /
    <a href="https://ollama.com/" target="_blank">ollama.com</a>
  </label>

  <div id="hermes-block" class="backend-block">
    <label>Hermes base URL
      <span class="hint">e.g. http://your-hermes-host.your-tailnet.ts.net:8642</span>
    </label>
    <input name="HERMES_BASE_URL" value="{v('HERMES_BASE_URL')}">
    <label>Hermes API key
      <span class="hint">from your own Hermes instance's config/env</span>
    </label>
    <input name="HERMES_API_KEY" value="{v('HERMES_API_KEY')}">
  </div>

  <div id="lm_studio-block" class="backend-block">
    <label>Server base URL
      <span class="hint">LM Studio default port 1234, Ollama's OpenAI-compatible API is on 11434</span>
    </label>
    <input name="LM_STUDIO_BASE_URL" value="{v('LM_STUDIO_BASE_URL')}">
    <label>Model name
      <span class="hint">optional if your server only has one model loaded</span>
    </label>
    <input name="LM_STUDIO_MODEL" value="{v('LM_STUDIO_MODEL')}">
  </div>

  <h2>Speech-to-text (Sogni)</h2>
  <label>Base URL
    <span class="hint">watch out for port 3000 — often taken by other dev tooling</span>
  </label>
  <input name="SOGNI_BASE_URL" value="{v('SOGNI_BASE_URL')}">
  <label>Transcribe path</label>
  <input name="SOGNI_STT_PATH" value="{v('SOGNI_STT_PATH')}">
  <label>API key</label>
  <input name="SOGNI_API_KEY" value="{v('SOGNI_API_KEY')}">
  <label>Engine</label>
  <input name="SOGNI_ENGINE" value="{v('SOGNI_ENGINE')}">
  <label>Language</label>
  <input name="SOGNI_LANGUAGE" value="{v('SOGNI_LANGUAGE')}">

  <button type="submit">Write .env</button>
</form>

<script>
function showBackend(which) {{
  document.getElementById('hermes-block').classList.toggle('active', which === 'hermes');
  document.getElementById('lm_studio-block').classList.toggle('active', which === 'lm_studio');
}}
showBackend('{"hermes" if is_hermes else "lm_studio"}');
</script>
</body></html>"""


DONE_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Done</title>
<style>body { font-family: -apple-system, system-ui, sans-serif; max-width: 640px;
             margin: 40px auto; padding: 0 20px; }
       code { background: #f0f0f0; padding: 2px 6px; border-radius: 4px; }</style>
</head><body>
<h1>.env written</h1>
<p>Next step, in a terminal:</p>
<pre><code>docker compose up --build</code></pre>
<p>Then find your gateway's tailnet address with
<code>docker compose exec gateway tailscale status</code> and enter it in the
glasses app's setup screen.</p>
<p>You can close this tab. Re-run <code>python3 setup_wizard.py</code> any
time to change these settings.</p>
</body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, body: str, status: int = 200) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path != "/":
            self._send("Not found", 404)
            return
        self._send(render_form())

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8")
        parsed = urllib.parse.parse_qs(raw)
        get = lambda k: parsed.get(k, [""])[0].strip()

        lines = [
            "# Written by setup_wizard.py — a normal .env file, edit freely.",
            "",
        ]
        for key in FIELDS:
            lines.append(f"{key}={get(key)}")
        with open(ENV_PATH, "w") as f:
            f.write("\n".join(lines) + "\n")

        self._send(DONE_HTML)
        threading.Thread(target=self.server.shutdown, daemon=True).start()


def main() -> None:
    with socketserver.TCPServer(("127.0.0.1", PORT), Handler) as httpd:
        url = f"http://127.0.0.1:{PORT}/"
        print(f"Setup wizard running at {url}")
        print("Opening in your browser... (Ctrl+C to cancel without saving)")
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
        httpd.serve_forever()
    print("\nDone. Next: docker compose up --build")


if __name__ == "__main__":
    main()
