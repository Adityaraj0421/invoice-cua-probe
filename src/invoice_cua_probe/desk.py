"""The page the computer-use model sees: rendered document beside the payable form."""

from __future__ import annotations

from pathlib import Path

from invoice_cua_probe.extract import pdf_text_layer

DESK_CSS = """
  :root { color-scheme: light; --ink: #1f1a17; --muted: #6b625b; --line: #ddd6cb; --paper: #f3efe6; --card: #fffcf7; --mark: #9a3412; }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; background: var(--paper); color: var(--ink);
    font-family: ui-sans-serif, "Segoe UI", Helvetica, Arial, sans-serif; }
  .desk { display: flex; height: 100vh; }
  .document { flex: 1; min-width: 0; overflow: auto; padding: 16px 18px 24px; }
  .document h1 { font-size: 0.95rem; font-weight: 600; margin: 0 0 10px; }
  .sheet { position: relative; width: min(860px, 100%); background: white; border: 1px solid var(--line); }
  .sheet img { display: block; width: 100%; height: auto; user-select: none; }
  .text-layer { position: absolute; inset: 0; }
  .text-layer span {
    position: absolute; color: transparent; background: transparent; white-space: pre;
    overflow: hidden; user-select: text; line-height: 1; cursor: text;
  }
  .message { width: min(860px, 100%); height: calc(100vh - 52px); border: 1px solid var(--line); background: white; }
  .form-pane { width: 420px; flex: none; border-left: 1px solid var(--line); overflow: auto; padding: 22px 20px 28px; background: var(--paper); }
  .mark { width: 36px; height: 3px; background: var(--mark); margin-bottom: 14px; }
  .form-pane h1 { font-size: 1.45rem; letter-spacing: -0.02em; margin: 0 0 8px; }
  .lede { margin: 0 0 16px; color: var(--muted); line-height: 1.4; font-size: 0.92rem; }
  label { display: block; font-size: 0.75rem; letter-spacing: 0.04em; text-transform: uppercase; color: var(--muted); margin: 12px 0 6px; }
  input { width: 100%; border: 1px solid var(--line); background: white; color: var(--ink); border-radius: 8px; padding: 10px 12px; font: inherit; }
  button { margin-top: 18px; width: 100%; border: 0; background: var(--ink); color: #fffcf7; border-radius: 8px; padding: 12px 14px; font: inherit; cursor: pointer; }
"""


def render_desk(fixture_id: str, folder: Path) -> str:
    document = _document(folder)
    if document.suffix.lower() == ".pdf":
        body = _pdf_sheet(fixture_id, document)
    else:
        body = f'<h1>Supplier email</h1><iframe class="message" id="message" title="Supplier email" src="/raw/{_esc(fixture_id)}"></iframe>'
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Payable desk { _esc(fixture_id) }</title>
<style>{DESK_CSS}</style>
</head>
<body>
<div class="desk">
  <section class="document">{body}</section>
  <section class="form-pane">
    <div class="mark"></div>
    <h1>Payable entry</h1>
    <p class="lede">Local eval target. Recording a payable here does not send a payment.</p>
    <form method="post" action="/submit">
      <input type="hidden" name="fixture_id" value="{_esc(fixture_id)}">
      <label for="payee">Payee</label>
      <input id="payee" name="payee" autocomplete="off">
      <label for="iban">IBAN or account</label>
      <input id="iban" name="iban" autocomplete="off" spellcheck="false">
      <label for="amount">Amount</label>
      <input id="amount" name="amount" inputmode="decimal" autocomplete="off">
      <label for="reference">Reference</label>
      <input id="reference" name="reference" autocomplete="off">
      <button id="submit" type="submit">Record payable</button>
    </form>
  </section>
</div>
</body>
</html>
"""


def _pdf_sheet(fixture_id: str, pdf_path: Path) -> str:
    layer = pdf_text_layer(pdf_path)
    spans = []
    for line in layer["lines"]:
        spans.append(
            "<span style=\"left:{left}%;top:{top}%;width:{width}%;height:{height}%\">{text}</span>".format(
                left=line["left"],
                top=line["top"],
                width=max(line["width"], 1),
                height=max(line["height"], 0.6),
                text=_esc(line["text"]),
            )
        )
    return f"""
    <h1>Supplier invoice</h1>
    <div class="sheet">
      <img src="/preview/{_esc(fixture_id)}" alt="Rendered invoice">
      <div id="text-layer" class="text-layer">{''.join(spans)}</div>
    </div>
    """


def _document(folder: Path) -> Path:
    documents = [path for path in sorted(folder.iterdir()) if path.suffix.lower() in {".pdf", ".html", ".htm"}]
    if len(documents) != 1:
        raise FileNotFoundError(f"No single document in {folder.name}")
    return documents[0]


def _esc(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
