"""Render HireReady's resume Markdown (# name, header lines, ## sections, ### roles, - bullets, **bold**) to a one-column PDF."""

import re
from pathlib import Path

from fpdf import FPDF

FONTS = Path(__file__).resolve().parent / "fonts"
INK, MUTED, RULE = (20, 20, 20), (90, 90, 90), (170, 170, 170)
BULLET = re.compile(r"^\s*[-*•]\s+")


def _runs(pdf: FPDF, text: str, size: float, line_h: float):
    for i, chunk in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
        if chunk:
            pdf.set_font("Noto", "B" if i % 2 else "", size)
            pdf.write(line_h, chunk)


def markdown_to_pdf(markdown: str) -> bytes:
    pdf = FPDF(format="Letter", unit="pt")
    pdf.add_font("Noto", "", FONTS / "NotoSans-Regular.ttf")
    pdf.add_font("Noto", "B", FONTS / "NotoSans-Bold.ttf")
    pdf.set_margins(54, 48, 54)
    pdf.set_auto_page_break(True, 48)
    pdf.add_page()
    pdf.set_text_color(*INK)
    margin, width = pdf.l_margin, pdf.epw
    in_header = False  # plain lines right after "# Name" are the centered contact block

    for line in markdown.splitlines():
        s = line.strip()
        if not s:
            continue
        m = re.match(r"^(#{1,3})\s+(.*)", s)
        level, text = (len(m.group(1)), m.group(2).replace("**", "")) if m else (0, s)
        if level == 1:
            pdf.set_font("Noto", "B", 20)
            pdf.multi_cell(width, 26, text, align="C", new_x="LMARGIN", new_y="NEXT")
            in_header = True
        elif level == 2:
            in_header = False
            pdf.ln(8)
            pdf.set_font("Noto", "B", 11)
            pdf.multi_cell(width, 15, text.upper(), new_x="LMARGIN", new_y="NEXT")
            pdf.set_draw_color(*RULE)
            pdf.line(margin, pdf.get_y() + 1, margin + width, pdf.get_y() + 1)
            pdf.ln(5)
        elif level == 3:
            pdf.ln(3)
            pdf.set_font("Noto", "B", 10.5)
            pdf.multi_cell(width, 14, text, new_x="LMARGIN", new_y="NEXT")
        elif in_header:
            pdf.set_text_color(*MUTED)
            pdf.set_font("Noto", "", 9.5)
            pdf.multi_cell(width, 13, text.replace("**", ""), align="C", new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(*INK)
        elif BULLET.match(s):
            pdf.set_font("Noto", "", 10)
            pdf.set_x(margin + 6)
            pdf.write(13.5, "•")
            pdf.set_left_margin(margin + 18)
            pdf.set_x(margin + 18)
            _runs(pdf, BULLET.sub("", s), 10, 13.5)
            pdf.set_left_margin(margin)
            pdf.ln(15)
        else:
            _runs(pdf, s, 10, 13.5)
            pdf.ln(16)
    return bytes(pdf.output())
