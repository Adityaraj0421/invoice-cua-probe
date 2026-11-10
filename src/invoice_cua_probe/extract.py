"""Read a fixture as either every stored string, or only text that is painted."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

import pypdfium2 as pdfium
from pdfminer.high_level import extract_pages
from pdfminer.layout import LAParams, LTAnno, LTChar, LTTextLine
from PIL import Image

from invoice_cua_probe.textutil import decode_tags, strip_tag_chars

logging.getLogger("pdfminer").setLevel(logging.ERROR)

MIN_VISIBLE_PT = 2.0
WHITE_COLORS = {"#fff", "#ffffff", "white", "rgb(255,255,255)"}
VOID_TAGS = {"meta", "br", "img", "hr", "link", "input", "source", "wbr"}
SKIP_TAGS = {"script", "style", "head"}


@dataclass(frozen=True)
class PdfLine:
    text: str
    size: float
    near_white: bool
    bbox: tuple[float, float, float, float]


def line_is_visible(size: float, near_white: bool, has_ink: bool) -> bool:
    if size < MIN_VISIBLE_PT:
        return False
    if near_white:
        return False
    if not has_ink:
        return False
    return True


def style_is_hidden(style: str) -> bool:
    """Heuristic for the hiding styles used by the fixtures.

    Matches the Sublime 22 Oct 2026 span (1px type, zero-size box, white color,
    overflow hidden, aria-hidden is checked separately) plus display:none,
    visibility:hidden, opacity near 0, and off-screen offsets.
    """

    decls = _declarations(style)
    if decls.get("display") == "none":
        return True
    if decls.get("visibility") in {"hidden", "collapse"}:
        return True
    opacity = decls.get("opacity")
    if opacity is not None:
        try:
            if float(opacity) <= 0.05:
                return True
        except ValueError:
            pass
    font_px = _css_px(decls.get("font-size", ""))
    if font_px is not None and font_px <= 1.5:
        return True
    font_size = decls.get("font-size", "")
    if font_size.endswith("pt"):
        try:
            if float(font_size[:-2]) < MIN_VISIBLE_PT:
                return True
        except ValueError:
            pass
    for key in ("height", "width", "max-height", "max-width"):
        length = _css_px(decls.get(key, ""))
        if length is not None and length <= 1:
            return True
    for key in ("left", "top", "text-indent"):
        length = _css_px(decls.get(key, ""))
        if length is not None and length <= -500:
            return True
    color = _norm_color(decls.get("color", ""))
    background = _norm_color(decls.get("background-color", decls.get("background", "")))
    if color in WHITE_COLORS and background in WHITE_COLORS:
        return True
    return False


def pdf_text_layer(path: Path) -> dict:
    """Every text-layer line, as percentages of the page, including text that is not painted.

    The desk page positions these spans over the rendered preview. Their color is
    transparent, so a screenshot stays what a person sees. Selecting or copying
    still returns the text, which is how hidden PDF text can reach a computer-use model.
    """

    laparams = LAParams(all_texts=True, detect_vertical=False)
    pages = list(extract_pages(str(path), laparams=laparams))
    if not pages:
        return {"width": 0.0, "height": 0.0, "lines": []}
    page = pages[0]
    width = float(page.width) or 1.0
    height = float(page.height) or 1.0
    lines = []
    for line in _walk_lines(page):
        x0, y0, x1, y1 = line.bbox
        lines.append(
            {
                "text": line.text,
                "left": _pct(x0 / width),
                "top": _pct((height - y1) / height),
                "width": _pct(max(x1 - x0, 0.0) / width),
                "height": _pct(max(y1 - y0, 0.0) / height),
            }
        )
    return {"width": width, "height": height, "lines": lines}


def _pct(value: float) -> float:
    return max(0.0, min(100.0, round(value * 100, 4)))


def document_text(path: Path, *, visible_only: bool) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        raw = _pdf_text(path, visible_only=visible_only)
    elif suffix in {".html", ".htm"}:
        html = path.read_text(encoding="utf-8")
        raw = _html_text(html, visible_only=visible_only)
    else:
        raise ValueError(f"Unsupported fixture type: {path.name}")
    if visible_only:
        return strip_tag_chars(raw)
    decoded = decode_tags(raw if suffix != ".html" else path.read_text(encoding="utf-8"))
    combined = raw if not decoded.strip() else raw + "\n" + decoded
    return strip_tag_chars(combined)


def _pdf_text(path: Path, *, visible_only: bool) -> str:
    lines = list(_pdf_lines(path))
    if not visible_only:
        return "\n".join(line.text for line in lines if line.text.strip())
    ink = _ink_by_line(path, lines)
    kept = [
        line.text
        for line in lines
        if line.text.strip() and line_is_visible(line.size, line.near_white, ink.get(id(line), False))
    ]
    return "\n".join(kept)


def _pdf_lines(path: Path):
    laparams = LAParams(all_texts=True, detect_vertical=False)
    for page in extract_pages(str(path), laparams=laparams):
        yield from _walk_lines(page)


def _walk_lines(node):
    if isinstance(node, LTTextLine):
        built = _line_from(node)
        if built is not None:
            yield built
        return
    if isinstance(node, (LTChar, LTAnno, str)):
        return
    if hasattr(node, "__iter__"):
        for child in node:
            yield from _walk_lines(child)


def _line_from(line: LTTextLine) -> PdfLine | None:
    parts: list[str] = []
    sizes: list[float] = []
    colors: list[object] = []
    for char in line:
        if isinstance(char, LTChar):
            parts.append(char.get_text())
            sizes.append(float(char.size))
            colors.append(char.graphicstate.ncolor)
        elif isinstance(char, LTAnno):
            parts.append(char.get_text())
    text = "".join(parts).strip()
    if not text or not sizes:
        return None
    ordered = sorted(sizes)
    size = ordered[len(ordered) // 2]
    x0, y0, x1, y1 = line.bbox
    return PdfLine(
        text=text,
        size=size,
        near_white=all(_near_white(color) for color in colors),
        bbox=(float(x0), float(y0), float(x1), float(y1)),
    )


def _near_white(color: object) -> bool:
    if isinstance(color, (int, float)):
        return float(color) >= 0.92
    if isinstance(color, (tuple, list)):
        if len(color) == 3 and all(isinstance(channel, (int, float)) for channel in color):
            return all(float(channel) >= 0.92 for channel in color)
        if len(color) == 4 and all(isinstance(channel, (int, float)) for channel in color):
            return all(float(channel) <= 0.08 for channel in color)
    return False


def _ink_by_line(path: Path, lines: list[PdfLine]) -> dict[int, bool]:
    pdf = pdfium.PdfDocument(str(path))
    ink: dict[int, bool] = {}
    try:
        page = pdf[0]
        scale = 2
        bitmap = page.render(scale=scale, fill_color=(255, 255, 255, 255))
        try:
            image = bitmap.to_pil().convert("RGB")
        finally:
            bitmap.close()
        width_pt, height_pt = page.get_size()
        page.close()
        for line in lines:
            ink[id(line)] = _crop_has_ink(image, line.bbox, height_pt, scale)
        image.close()
    finally:
        pdf.close()
    return ink


def _crop_has_ink(
    image: Image.Image,
    bbox: tuple[float, float, float, float],
    page_height: float,
    scale: int,
) -> bool:
    x0, y0, x1, y1 = bbox
    left = max(0, int(x0 * scale) - 1)
    right = min(image.width, int(x1 * scale) + 2)
    top = max(0, int((page_height - y1) * scale) - 1)
    bottom = min(image.height, int((page_height - y0) * scale) + 2)
    if right <= left or bottom <= top:
        return False
    crop = image.crop((left, top, right, bottom)).convert("L")
    total = crop.size[0] * crop.size[1]
    if total == 0:
        return False
    dark = sum(crop.histogram()[:200])
    return (dark / total) >= 0.008


class _Collector(HTMLParser):
    def __init__(self, *, visible_only: bool) -> None:
        super().__init__(convert_charrefs=True)
        self.visible_only = visible_only
        self.parts: list[str] = []
        self.skip_depth = 0
        self.hidden_stack = [False]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in VOID_TAGS:
            if tag == "br" and not (self.visible_only and self.hidden_stack[-1]):
                self.parts.append("\n")
            return
        hidden = self.hidden_stack[-1] or _tag_hidden(tag, attrs)
        if tag in SKIP_TAGS:
            self.skip_depth += 1
            hidden = True
        self.hidden_stack.append(hidden)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in VOID_TAGS:
            return
        if len(self.hidden_stack) > 1:
            self.hidden_stack.pop()
        if tag in SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
        if tag in {"p", "div", "tr", "li", "h1", "h2", "table"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth or not data:
            return
        if self.visible_only and self.hidden_stack[-1]:
            return
        self.parts.append(data)


def _tag_hidden(tag: str, attrs: list[tuple[str, str | None]]) -> bool:
    mapping = {key.lower(): (value or "") for key, value in attrs}
    if "hidden" in mapping:
        return True
    if mapping.get("aria-hidden", "").strip().lower() == "true":
        return True
    return style_is_hidden(mapping.get("style", ""))


def _html_text(html: str, *, visible_only: bool) -> str:
    collector = _Collector(visible_only=visible_only)
    collector.feed(html)
    collector.close()
    return "".join(collector.parts)


def _declarations(style: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for part in style.split(";"):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        found[key.strip().lower()] = value.strip().lower()
    return found


def _css_px(value: str) -> float | None:
    text = value.strip().lower()
    if not text:
        return None
    if text.endswith("px"):
        text = text[:-2].strip()
    elif text.endswith(("pt", "em", "rem", "%", "vh", "vw")):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _norm_color(value: str) -> str:
    return value.replace(" ", "").lower()
