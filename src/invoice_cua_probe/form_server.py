"""Localhost payable form. It records a POST and never sends a payment."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from invoice_cua_probe.desk import _document, render_desk
from invoice_cua_probe.models import FIELDS

HOST = "127.0.0.1"


def page_shell(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{
    color-scheme: light;
    --ink: #1f1a17;
    --muted: #6b625b;
    --line: #ddd6cb;
    --paper: #f3efe6;
    --card: #fffcf7;
    --mark: #9a3412;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--paper);
    color: var(--ink);
    font-family: ui-sans-serif, "Segoe UI", Helvetica, Arial, sans-serif;
  }}
  main {{
    width: 520px;
    margin: 0 auto;
    padding: 28px 20px 24px;
    background: var(--paper);
  }}
  .mark {{
    width: 36px;
    height: 3px;
    background: var(--mark);
    margin-bottom: 18px;
  }}
  h1 {{
    font-size: 1.7rem;
    font-weight: 620;
    letter-spacing: -0.02em;
    margin: 0 0 8px;
  }}
  p.lede {{
    margin: 0 0 28px;
    color: var(--muted);
    line-height: 1.45;
  }}
  form, .card {{
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 14px;
    padding: 22px 22px 20px;
  }}
  label {{
    display: block;
    font-size: 0.78rem;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--muted);
    margin: 14px 0 6px;
  }}
  input {{
    width: 100%;
    border: 1px solid var(--line);
    background: white;
    color: var(--ink);
    border-radius: 8px;
    padding: 10px 12px;
    font: inherit;
    font-size: 1rem;
  }}
  input:focus {{
    outline: 2px solid var(--mark);
    border-color: transparent;
  }}
  button {{
    margin-top: 20px;
    width: 100%;
    border: 0;
    background: var(--ink);
    color: #fffcf7;
    border-radius: 8px;
    padding: 12px 14px;
    font: inherit;
    font-size: 1rem;
    cursor: pointer;
  }}
  .fixture {{
    margin-top: 16px;
    color: var(--muted);
    font-size: 0.85rem;
  }}
  dl {{ margin: 8px 0 0; }}
  dt {{
    margin-top: 14px;
    font-size: 0.78rem;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--muted);
  }}
  dd {{
    margin: 4px 0 0;
    font-family: ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
    font-size: 0.95rem;
    line-height: 1.35;
    overflow-wrap: break-word;
  }}
</style>
</head>
<body>
<main>
  <div class="mark"></div>
  {body}
</main>
</body>
</html>
"""


def render_form(fixture_id: str) -> str:
    safe_id = _esc(fixture_id)
    body = f"""
    <h1>Payable entry</h1>
    <p class="lede">Local eval target. Recording a payable here does not send a payment.</p>
    <form method="post" action="/submit">
      <input type="hidden" name="fixture_id" value="{safe_id}">
      <label for="payee">Payee</label>
      <input id="payee" name="payee" autocomplete="off">
      <label for="iban">IBAN or account</label>
      <input id="iban" name="iban" autocomplete="off" spellcheck="false">
      <label for="amount">Amount</label>
      <input id="amount" name="amount" inputmode="decimal" autocomplete="off">
      <label for="reference">Reference</label>
      <input id="reference" name="reference" autocomplete="off">
      <button id="submit" type="submit">Record payable</button>
      <p class="fixture">Fixture {_esc(fixture_id)}</p>
    </form>
    """
    return page_shell("Payable entry", body)


def render_confirmation(fixture_id: str, values: dict[str, str] | None) -> str:
    if not values:
        body = """
        <h1>Nothing recorded</h1>
        <p class="lede">This fixture has no submission on the local form.</p>
        """
        return page_shell("Nothing recorded", body)
    rows = []
    labels = {
        "payee": "Payee",
        "iban": "IBAN or account",
        "amount": "Amount",
        "reference": "Reference",
    }
    for field in FIELDS:
        rows.append(f"<dt>{labels[field]}</dt><dd>{_esc(values.get(field, ''))}</dd>")
    body = f"""
    <h1>Recorded payable</h1>
    <p class="lede">Nothing was paid. These are the values submitted to this localhost form.</p>
    <div class="card" id="confirmation">
      <dl>
        {''.join(rows)}
      </dl>
      <p class="fixture">Fixture {_esc(fixture_id)}</p>
    </div>
    """
    return page_shell("Recorded payable", body)


class FormServer:
    def __init__(self, port: int = 0, fixtures_dir: Path | None = None) -> None:
        self.submissions: dict[str, dict[str, str]] = {}
        self.fixtures_dir = fixtures_dir
        handler = _handler_factory(self.submissions, fixtures_dir)
        self.httpd = ThreadingHTTPServer((HOST, port), handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://{HOST}:{self.port}"

    def form_url(self, fixture_id: str) -> str:
        return f"{self.base_url}/form?fixture={quote(fixture_id)}"

    def desk_url(self, fixture_id: str) -> str:
        return f"{self.base_url}/desk?fixture={quote(fixture_id)}"

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.thread.join(timeout=3)
        self.httpd.server_close()

    def __enter__(self) -> "FormServer":
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()


def _handler_factory(submissions: dict[str, dict[str, str]], fixtures_dir: Path | None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self._json({"ok": True, "host": HOST})
                return
            if parsed.path == "/submissions":
                self._json(submissions)
                return
            if parsed.path.startswith("/submissions/"):
                fixture_id = unquote(parsed.path.split("/", 2)[-1])
                record = submissions.get(fixture_id)
                if record is None:
                    self._json({"error": "not found"}, status=404)
                    return
                self._json(record)
                return
            if parsed.path == "/desk":
                fixture_id = parse_qs(parsed.query).get("fixture", [""])[0]
                folder = _fixture_folder(fixtures_dir, fixture_id)
                if folder is None:
                    self._text("Unknown fixture.", status=404)
                    return
                try:
                    self._html(render_desk(fixture_id, folder))
                except FileNotFoundError:
                    self._text("Fixture document is missing.", status=404)
                return
            if parsed.path.startswith("/preview/"):
                folder = _fixture_folder(fixtures_dir, unquote(parsed.path.split("/", 2)[-1]))
                if folder is None or not (folder / "preview.png").is_file():
                    self._text("No preview.", status=404)
                    return
                self._bytes((folder / "preview.png").read_bytes(), "image/png")
                return
            if parsed.path.startswith("/raw/"):
                folder = _fixture_folder(fixtures_dir, unquote(parsed.path.split("/", 2)[-1]))
                if folder is None:
                    self._text("Unknown fixture.", status=404)
                    return
                try:
                    document = _document(folder)
                except FileNotFoundError:
                    self._text("Fixture document is missing.", status=404)
                    return
                kind = "application/pdf" if document.suffix.lower() == ".pdf" else "text/html; charset=utf-8"
                self._bytes(document.read_bytes(), kind)
                return
            if parsed.path in {"/", "/form"}:
                fixture_id = parse_qs(parsed.query).get("fixture", ["manual"])[0]
                if not _safe_id(fixture_id):
                    self._text("Unknown fixture id.", status=400)
                    return
                self._html(render_form(fixture_id))
                return
            if parsed.path.startswith("/confirmation/"):
                fixture_id = unquote(parsed.path.split("/", 2)[-1])
                self._html(render_confirmation(fixture_id, submissions.get(fixture_id)))
                return
            self._text("Not found.", status=404)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != "/submit":
                self._text("Not found.", status=404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            data = parse_qs(raw, keep_blank_values=True)
            fixture_id = (data.get("fixture_id") or ["manual"])[0]
            if not _safe_id(fixture_id):
                self._text("Unknown fixture id.", status=400)
                return
            record = {field: (data.get(field) or [""])[0] for field in FIELDS}
            submissions[fixture_id] = record
            self.send_response(303)
            self.send_header("Location", f"/confirmation/{quote(fixture_id)}")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _html(self, body: str, status: int = 200) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def _bytes(self, payload: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _text(self, body: str, status: int = 200) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _json(self, payload: dict, status: int = 200) -> None:
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return Handler


def _fixture_folder(fixtures_dir: Path | None, fixture_id: str) -> Path | None:
    if fixtures_dir is None or not _safe_id(fixture_id):
        return None
    root = fixtures_dir.resolve()
    folder = (root / fixture_id).resolve()
    if folder.parent != root or not folder.is_dir():
        return None
    return folder


def _safe_id(value: str) -> bool:
    if not value or len(value) > 80:
        return False
    return all(char.islower() or char.isdigit() or char == "-" for char in value)


def _esc(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
