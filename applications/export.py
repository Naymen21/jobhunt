"""Cover letter files: PDF (fpdf2) and DOCX (python-docx), both free libraries. Named like
Alex_CoverLetter_RiotGames.pdf. Your contact details go in the header; the letter text is used as written."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from docx import Document
from docx.shared import Inches, Pt
from fpdf import FPDF
from fpdf.fonts import FontFace

FONT_DIRS = [Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/dejavu"), Path("/Library/Fonts")]
FONT_FILES = [("arial.ttf", "arialbd.ttf"), ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf")]


@dataclass
class Letterhead:
    name: str
    email: str = ""
    phone: str = ""
    location: str = ""
    link: str = ""

    def contact_line(self) -> str:
        return "  ·  ".join(x for x in (self.location, self.email, format_phone(self.phone), self.link) if x)


def format_phone(phone: str) -> str:
    """6125550142 -> (612) 555-0142. Anything that isn't a plain US number is left as typed."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}" if len(digits) == 10 else (phone or "")


def file_name(person: str, company: str, ext: str) -> str:
    first = re.sub(r"[^A-Za-z0-9]", "", (person or "Me").split()[0]) or "Me"
    co = re.sub(r"[^A-Za-z0-9]", "", "".join(w[:1].upper() + w[1:] for w in (company or "Company").split())) or "Company"
    return f"{first}_CoverLetter_{co}.{ext}"


def paragraphs(text: str) -> list[str]:
    return [" ".join(p.split()) for p in re.split(r"\n\s*\n", (text or "").strip()) if p.strip()] or [""]


def _font_paths() -> Optional[tuple[Path, Path]]:
    for folder in FONT_DIRS:
        for regular, bold in FONT_FILES:
            if (folder / regular).exists() and (folder / bold).exists():
                return folder / regular, folder / bold
    return None


def _latin1(text: str) -> str:
    """Only for the built-in PDF font fallback, which can't draw curly quotes or dashes."""
    swaps = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "·": "|", "…": "..."}
    return "".join(swaps.get(c, c) for c in text).encode("latin-1", "replace").decode("latin-1")


def to_pdf(letter: str, head: Letterhead, on: Optional[date] = None, table: Optional[dict] = None) -> bytes:
    """`table` ({"opening", "rows": [{"need", "bring"}], "closing"}) exports the requirements-table version."""
    pdf = FPDF(format="Letter")
    pdf.set_margins(25, 22, 25)
    pdf.set_auto_page_break(True, margin=22)
    pdf.add_page()
    fonts = _font_paths()
    if fonts:
        pdf.add_font("Body", "", str(fonts[0]))
        pdf.add_font("Body", "B", str(fonts[1]))
        family, clean = "Body", (lambda s: s)
    else:
        family, clean = "Helvetica", _latin1
    pdf.set_font(family, "B", 16)
    pdf.cell(0, 8, clean(head.name), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(family, "", 9.5)
    pdf.set_text_color(90, 90, 90)
    pdf.cell(0, 5, clean(head.contact_line()), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(8)
    pdf.set_font(family, "", 11)
    pdf.cell(0, 6, clean(f"{(on or date.today()):%B} {(on or date.today()).day}, {(on or date.today()).year}"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)
    if table:
        greeting, signoff = _greeting(letter), _signoff(letter, head.name)
        for para in [greeting, table["opening"]]:
            if para:
                pdf.multi_cell(0, 5.8, clean(para), align="L", new_x="LMARGIN", new_y="NEXT")
                pdf.ln(3.2)
        pdf.set_font(family, "", 10.5)
        with pdf.table(col_widths=(38, 62), line_height=5.6, padding=2.2, text_align="LEFT",
                       headings_style=FontFace(emphasis="BOLD", fill_color=(253, 240, 213))) as t:
            head_row = t.row()
            head_row.cell(clean("What you're looking for"))
            head_row.cell(clean("What I bring"))
            for r in table["rows"]:
                row = t.row()
                row.cell(clean(r["need"]))
                row.cell(clean(r["bring"]))
        pdf.set_font(family, "", 11)
        pdf.ln(4)
        for para in [table["closing"]] + signoff:
            if para:
                pdf.multi_cell(0, 5.8, clean(para), align="L", new_x="LMARGIN", new_y="NEXT")
                pdf.ln(1.8)
        return bytes(pdf.output())
    for para in paragraphs(letter):
        pdf.multi_cell(0, 5.8, clean(para), align="L", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3.2)
    return bytes(pdf.output())


def _greeting(letter: str) -> str:
    first = paragraphs(letter)[0]
    return first if first.lower().startswith(("dear", "hi ", "hello")) and len(first) < 80 else "Dear hiring team,"


def _signoff(letter: str, name: str) -> list[str]:
    return ["Sincerely,", name]


def to_docx(letter: str, head: Letterhead, on: Optional[date] = None, table: Optional[dict] = None) -> bytes:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)
    name = doc.add_paragraph()
    run = name.add_run(head.name)
    run.bold, run.font.size = True, Pt(16)
    contact = doc.add_paragraph(head.contact_line())
    for r in contact.runs:
        r.font.size = Pt(9.5)
    on = on or date.today()
    doc.add_paragraph(f"{on:%B} {on.day}, {on.year}")
    if table:
        doc.add_paragraph(_greeting(letter))
        doc.add_paragraph(table["opening"])
        grid = doc.add_table(rows=1, cols=2)
        grid.style = "Table Grid"
        for cell, text in zip(grid.rows[0].cells, ("What you're looking for", "What I bring")):
            cell.text = ""
            cell.paragraphs[0].add_run(text).bold = True
        for r in table["rows"]:
            cells = grid.add_row().cells
            cells[0].text, cells[1].text = r["need"], r["bring"]
        for row in grid.rows:
            row.cells[0].width, row.cells[1].width = Inches(2.3), Inches(4.2)
        doc.add_paragraph("")
        doc.add_paragraph(table["closing"])
        doc.add_paragraph("Sincerely,")
        doc.add_paragraph(head.name)
    else:
        for para in paragraphs(letter):
            doc.add_paragraph(para)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
