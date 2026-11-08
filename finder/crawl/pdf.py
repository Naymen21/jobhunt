"""Text extraction from PDFs found on the company's own site."""
from __future__ import annotations

import io
import logging

MAX_PAGES = 40

logging.getLogger("pypdf").setLevel(logging.ERROR)  # malformed PDFs are common; don't spam the console


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            reader.decrypt("")
        return "\n".join((page.extract_text() or "") for page in reader.pages[:MAX_PAGES])
    except Exception:  # pypdf raises many exception types on bad files
        return ""
