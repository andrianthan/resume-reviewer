"""PDF → text extraction via PyMuPDF."""
from __future__ import annotations

import io


def pdf_to_markdown(pdf_bytes: bytes) -> str:
    """Convert PDF bytes to plain text (markdown-ish: preserves line breaks).

    Uses PyMuPDF (fitz). Falls back to a stub error if not installed.
    """
    try:
        import fitz  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "PyMuPDF not installed. Run: pip install pymupdf"
        ) from e

    doc = fitz.open(stream=io.BytesIO(pdf_bytes), filetype="pdf")
    pages: list[str] = []
    for page in doc:
        pages.append(page.get_text())
    doc.close()
    return "\n\n".join(pages).strip()
