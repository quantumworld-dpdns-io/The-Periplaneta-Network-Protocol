"""
Render docs/netsim/ABSTRACT.md to a single-page PDF at 12 pt minimum font size,
then verify the constraints (page count == 1, no font below 12 pt).

    python -m netsim.make_abstract_pdf            # -> docs/netsim/ABSTRACT.pdf

Layout: A4, 15 mm margins, single column, 12 pt body / 12 pt captions, one
figure (the noise-0.15 design curves) if it fits. If the text does not fit on
one page the script exits non-zero and says by how much, instead of shrinking
the font below the limit.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "netsim" / "ABSTRACT.md"
FIG = ROOT / "docs" / "netsim" / "design_curves_noise0.15.png"
OUT = ROOT / "docs" / "netsim" / "ABSTRACT.pdf"

MIN_PT = 12.0
BODY = ParagraphStyle("body", fontName="Times-Roman", fontSize=MIN_PT, leading=MIN_PT * 1.12,
                      alignment=TA_JUSTIFY, spaceAfter=3)
TITLE = ParagraphStyle("title", fontName="Times-Bold", fontSize=15, leading=18, spaceAfter=4)
NOTE = ParagraphStyle("note", parent=BODY, fontName="Times-Italic", textColor=colors.HexColor("#444444"), spaceAfter=5)
CAP = ParagraphStyle("cap", parent=BODY, fontName="Times-Italic", alignment=0, spaceAfter=2)


def md_inline(s: str) -> str:
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", s)
    s = re.sub(r"`(.+?)`", r"<font face='Courier' size='12'>\1</font>", s)
    return s


def blocks(md: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for para in re.split(r"\n\s*\n", md.strip()):
        p = " ".join(line.strip() for line in para.splitlines())
        if p.startswith("# "):
            out.append(("title", p[2:]))
        elif p.startswith("*") and p.endswith("*") and not p.startswith("**"):
            out.append(("note", p.strip("*")))
        else:
            out.append(("body", p))
    return out


def build(include_figure: bool, fig_width_mm: float) -> int:
    doc = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=13 * mm, bottomMargin=13 * mm, title="How much sensing is enough?",
                            author="The Periplaneta Protocol contributors")
    story = []
    for kind, txt in blocks(SRC.read_text()):
        style = {"title": TITLE, "note": NOTE, "body": BODY}[kind]
        story.append(Paragraph(md_inline(txt), style))
        if kind == "body" and txt.startswith("**Key findings") and include_figure and FIG.exists():
            w = fig_width_mm * mm
            img = Image(str(FIG))
            img.drawWidth, img.drawHeight = w, w * img.imageHeight / img.imageWidth
            story += [Spacer(1, 2), img,
                      Paragraph("Figure: design curves at measurement-noise sd 0.15. Top, mean lead time before the first collapse "
                                "at the source; bottom, probability of alarming before it; columns are layouts, lines are "
                                "detector × network rule. Matched per-run false-alarm probability 0.05.", CAP)]
    pages = []
    doc.build(story, onFirstPage=lambda c, d: pages.append(1), onLaterPages=lambda c, d: pages.append(1))
    return len(pages)


def check_fonts_min_size(path: Path, min_pt: float) -> list[float]:
    """Return every font size used below `min_pt` by scanning content streams (Tf operators)."""
    import zlib
    data = path.read_bytes()
    sizes: list[float] = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        raw = m.group(1)
        try:
            txt = zlib.decompress(raw)
        except Exception:
            txt = raw
        sizes += [float(x) for x in re.findall(rb"/F\d+\s+([\d.]+)\s+Tf", txt)]
    return sorted({s for s in sizes if s < min_pt})


def main() -> int:
    # try with the figure at decreasing widths, then without it
    attempts = [(True, 115.0), (True, 100.0), (True, 85.0), (True, 70.0), (False, 0.0)]
    for include, width in attempts:
        n = build(include, width)
        if n == 1:
            small = check_fonts_min_size(OUT, MIN_PT)
            if small:
                print(f"FAIL: fonts below {MIN_PT} pt found: {small}")
                return 1
            print(f"wrote {OUT.relative_to(ROOT)}: 1 page, min font {MIN_PT} pt, figure {'included at %.0f mm' % width if include else 'omitted'}")
            return 0
        print(f"  {n} pages with figure={include} width={width} mm; retrying")
    print("FAIL: abstract does not fit on one page at 12 pt even without the figure; shorten the text")
    return 1


if __name__ == "__main__":
    sys.exit(main())
