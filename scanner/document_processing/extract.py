"""PDF text extraction with honest status reporting.

status values: extracted | no_text_layer | ocr_unavailable | failed | too_large
OCR is attempted ONLY when the PDF has no text layer AND pytesseract + pdf2image + tesseract are installed.
Personal data (emails, phone numbers) is redacted before anything is stored."""
from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass, field
from typing import Optional

MAX_PDF_BYTES = 15 * 1024 * 1024
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<![\d/.-])(?:\+?91[-\s]?)?[6-9]\d{9}(?![\d])")
_DEVANAGARI = re.compile("[ऀ-ॿ]")


@dataclass
class ExtractionResult:
    status: str
    text: str = ""
    pages: Optional[int] = None
    sha256: Optional[str] = None
    error: Optional[str] = None
    meta: dict = field(default_factory=dict)


def redact(text: str) -> str:
    text = _EMAIL.sub("<email-redacted>", text)
    return _PHONE.sub("<phone-redacted>", text)


def english_lines(text: str) -> str:
    """GeM bid PDFs are bilingual (Hindi/English, each label printed twice). Keep the English lines."""
    out = []
    for line in text.split("\n"):
        line = line.strip()
        if line and re.search("[A-Za-z0-9]", line) and not _DEVANAGARI.search(line):
            out.append(line)
    s = " ".join(out)
    s = re.sub(r"\s+/\s*", " /", s)
    return re.sub(r"\s+", " ", s)


def _ocr_available() -> bool:
    try:
        import pytesseract  # noqa: F401
        from pdf2image import convert_from_bytes  # noqa: F401
        import shutil
        return shutil.which("tesseract") is not None
    except Exception:
        return False


def extract_pdf(data: bytes) -> ExtractionResult:
    sha = hashlib.sha256(data).hexdigest()
    if len(data) > MAX_PDF_BYTES:
        return ExtractionResult(status="too_large", sha256=sha, error=f"{len(data)} bytes exceeds limit")
    if not data[:5].startswith(b"%PDF"):
        return ExtractionResult(status="failed", sha256=sha, error="response is not a PDF")
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        pages = len(reader.pages)
        raw = "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception as e:  # corrupt or encrypted PDF
        return ExtractionResult(status="failed", sha256=sha, error=f"pdf parse error: {e}"[:500])
    if len(raw.strip()) < 40:
        if not _ocr_available():
            return ExtractionResult(status="ocr_unavailable", pages=pages, sha256=sha,
                                    error="no text layer; OCR not installed in this deployment")
        try:
            import pytesseract
            from pdf2image import convert_from_bytes
            imgs = convert_from_bytes(data, dpi=200, first_page=1, last_page=min(pages, 10))
            raw = "\n".join(pytesseract.image_to_string(i) for i in imgs)
        except Exception as e:
            return ExtractionResult(status="failed", pages=pages, sha256=sha, error=f"ocr error: {e}"[:500])
        if len(raw.strip()) < 40:
            return ExtractionResult(status="no_text_layer", pages=pages, sha256=sha, error="OCR produced no text")
        text = redact(english_lines(raw))
        return ExtractionResult(status="extracted", text=text, pages=pages, sha256=sha, meta={"ocr": True})
    text = redact(english_lines(raw))
    return ExtractionResult(status="extracted", text=text, pages=pages, sha256=sha, meta={"ocr": False})
