"""PDF → text extraction.

Uses `pypdf` (pure Python, no native DLLs — works on any platform
including Windows where PyMuPDF's mupdf.dll can be missing).
"""
from __future__ import annotations

import io


def pdf_to_markdown(pdf_bytes: bytes) -> str:
    """Convert PDF bytes to plain text (preserves line breaks)."""
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise RuntimeError(
            "pypdf not installed. Run: pip install pypdf"
        ) from e

    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return "\n\n".join(p for p in pages if p).strip()
