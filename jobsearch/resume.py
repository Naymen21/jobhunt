"""Resume text extraction: PDF with pypdf, DOCX with the standard library's zip + XML (no extra dependency)."""
from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree

from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_BYTES = 10 * 1024 * 1024
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class ResumeError(Exception):
    pass


def kind_of(filename: str, data: bytes) -> str:
    name = (filename or "").lower()
    if data[:5] == b"%PDF-" or name.endswith(".pdf"):
        return "pdf"
    if data[:2] == b"PK" and name.endswith(".docx"):
        return "docx"
    if name.endswith(".doc"):
        raise ResumeError("Old .doc files aren't supported. Save it as .docx or PDF and upload that.")
    raise ResumeError("Upload a PDF or a .docx file.")


def extract_text(filename: str, data: bytes) -> tuple[str, str]:
    """(kind, text). Raises ResumeError with a message for the owner."""
    if not data:
        raise ResumeError("The file is empty.")
    if len(data) > MAX_BYTES:
        raise ResumeError("That file is over 10 MB.")
    kind = kind_of(filename, data)
    text = _pdf_text(data) if kind == "pdf" else _docx_text(data)
    text = _tidy(text)
    if not text:
        raise ResumeError("No text could be read from this file. If it's a scanned image, export a text PDF instead.")
    return kind, text


def _pdf_text(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages[:20])
    except (PdfReadError, ValueError, KeyError) as e:
        raise ResumeError(f"Couldn't read this PDF ({type(e).__name__}).")


def _docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError):
        raise ResumeError("Couldn't read this .docx file.")
    root = ElementTree.fromstring(xml)
    lines = []
    for para in root.iter(f"{_W}p"):
        parts = []
        for node in para.iter():
            if node.tag == f"{_W}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{_W}tab":
                parts.append("\t")
            elif node.tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
        lines.append("".join(parts))
    return "\n".join(lines)


def _tidy(text: str) -> str:
    text = text.replace("\r", "").replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", l).strip() for l in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
