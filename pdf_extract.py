"""PDF text extraction with table recovery for paper survey.

IEEE Xplore PDFs often embed result tables as images (not text), so
pdfplumber/pypdf miss Acc@sum cell values. We:
  1) try pdfplumber lattice/text tables (strict filter),
  2) OCR pages that mention TABLE via pdftoppm + RapidOCR.
"""
from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict


MAX_PAGES = 60
MAX_CHARS = 200000

_TABLE_SETTINGS = [
    {},
    {
        "vertical_strategy": "lines",
        "horizontal_strategy": "lines",
        "intersection_tolerance": 5,
        "snap_tolerance": 3,
        "join_tolerance": 3,
    },
    {
        "vertical_strategy": "text",
        "horizontal_strategy": "text",
        "min_words_vertical": 3,
        "min_words_horizontal": 1,
        "intersection_tolerance": 5,
        "text_tolerance": 3,
        "text_x_tolerance": 2,
        "text_y_tolerance": 3,
    },
]

_JUNK_RE = re.compile(
    r"(accepted for inclusion|Restrictions apply|Downloaded on|"
    r"IEEE Xplore|future issue of this journal|Content is final)",
    re.I,
)

_ocr_engine = None


def sanitize_text(s: str) -> str:
    if not s:
        return s
    # NUL truncates SQLite TEXT length()/C reads — strip them
    s = s.replace("\x00", "")
    return s.encode("utf-8", "surrogatepass").decode("utf-8", "replace")


def _cell(v) -> str:
    if v is None:
        return ""
    return re.sub(r"\s+", " ", str(v)).strip()


def _table_to_tsv(rows) -> str:
    lines = []
    for row in rows or []:
        if not row:
            continue
        cells = [_cell(c) for c in row]
        if not any(cells):
            continue
        lines.append("\t".join(cells))
    return "\n".join(lines)


def _decimal_hits(text: str) -> int:
    return len(re.findall(r"\d+\.\d{2,}", text or ""))


def _looks_useful_table(tsv: str) -> bool:
    """Keep numeric result grids; drop IEEE header/footer fake tables."""
    if not tsv or tsv.count("\n") < 1:
        return False
    if _JUNK_RE.search(tsv):
        return False
    lines = [ln for ln in tsv.splitlines() if ln.strip()]
    if len(lines) < 2:
        return False
    cols = max((ln.count("\t") + 1) for ln in lines)
    if cols < 2:
        return False
    # Require real numeric cells (accuracies), not just page numbers
    if _decimal_hits(tsv) < 4:
        return False
    # Reject mostly long prose cells
    cells = [c for ln in lines for c in ln.split("\t") if c]
    if not cells:
        return False
    longish = sum(1 for c in cells if len(c) > 40)
    if longish / len(cells) > 0.45:
        return False
    return True


def _dedupe_blocks(blocks: list[str]) -> list[str]:
    seen = set()
    out = []
    for b in blocks:
        key = re.sub(r"\s+", " ", b.split("\n", 1)[-1])[:280]
        if key in seen:
            continue
        seen.add(key)
        out.append(b)
    return out


def _get_ocr():
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr_engine = RapidOCR()
    return _ocr_engine


def extract_tables_pdfplumber(data: bytes, max_pages: int = MAX_PAGES) -> list[str]:
    import pdfplumber

    blocks = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for pi, page in enumerate(pdf.pages[:max_pages]):
            found = []
            for settings in _TABLE_SETTINGS:
                try:
                    tables = (
                        page.extract_tables()
                        if not settings
                        else page.extract_tables(table_settings=settings)
                    ) or []
                except Exception:
                    tables = []
                for ti, table in enumerate(tables):
                    tsv = _table_to_tsv(table)
                    if not _looks_useful_table(tsv):
                        continue
                    found.append(f"【PDF表格 page={pi + 1} table={ti + 1}】\n{tsv}")
                if found:
                    break
            blocks.extend(found)
    return _dedupe_blocks(blocks)


def _pages_with_table_caption(data: bytes, max_pages: int = MAX_PAGES) -> list[int]:
    """1-based page numbers whose text layer mentions TABLE …"""
    import pdfplumber

    # IEEE PDFs often glue letters: "TABLEI" / "TABL EI"
    cre = re.compile(r"(?i)T\s*A\s*B\s*L\s*E\s*[IVXLCDM0-9]+")
    pages = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for i, page in enumerate(pdf.pages[:max_pages]):
            try:
                t = page.extract_text() or ""
            except Exception:
                t = ""
            if cre.search(t):
                pages.append(i + 1)
                continue
            # Tables-as-images: many small XObjects on a page with Acc@ / Fed*
            try:
                n_img = len(page.images or [])
            except Exception:
                n_img = 0
            if n_img >= 12 and re.search(r"(?i)Acc@|FedAvg|PmcmFL|missingness", t):
                pages.append(i + 1)
    return pages


def extract_tables_ocr(data: bytes, max_pages: int = MAX_PAGES) -> list[str]:
    """Rasterize TABLE pages and OCR — needed when cells are images."""
    if not shutil.which("pdftoppm"):
        return []
    pages = _pages_with_table_caption(data, max_pages=max_pages)
    if not pages:
        # fall back: OCR middle third of the paper (experiments)
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            n = min(len(pdf.pages), max_pages)
        if n <= 0:
            return []
        lo = max(1, n // 3)
        hi = min(n, lo + 5)
        pages = list(range(lo, hi + 1))

    blocks = []
    with tempfile.TemporaryDirectory(prefix="wb_pdf_ocr_") as td:
        pdf_path = os.path.join(td, "in.pdf")
        with open(pdf_path, "wb") as f:
            f.write(data)
        for pno in pages:
            prefix = os.path.join(td, f"p{pno}")
            try:
                subprocess.run(
                    ["pdftoppm", "-f", str(pno), "-l", str(pno),
                     "-png", "-r", "160", pdf_path, prefix],
                    check=True, capture_output=True, timeout=120,
                )
            except (subprocess.SubprocessError, OSError):
                continue
            # pdftoppm names: prefix-1.png or prefix-07.png
            pngs = sorted(
                fn for fn in os.listdir(td)
                if fn.startswith(f"p{pno}-") and fn.endswith(".png")
            )
            if not pngs:
                continue
            try:
                from PIL import Image
                import numpy as np
                png_path = os.path.join(td, pngs[0])
                img = np.array(Image.open(png_path).convert("RGB"))
                ocr = _get_ocr()
                result, _ = ocr(img)
            except Exception:
                continue
            lines = [row[1] for row in (result or []) if row and row[1]]
            if not lines:
                continue
            text = "\n".join(lines)
            if _decimal_hits(text) < 4 and not re.search(r"(?i)Acc@|PmcmFL|FedAvg", text):
                continue
            # Keep OCR text as a readable block (not perfect TSV, but has cell digits)
            blocks.append(
                f"【PDF表格OCR page={pno}】\n{text}"
            )
    return _dedupe_blocks(blocks)


def extract_text_pypdf(data: bytes, max_pages: int = MAX_PAGES) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts = []
    for p in reader.pages[:max_pages]:
        try:
            parts.append(p.extract_text() or "")
        except Exception:
            parts.append("")
    return "\n".join(parts)


def extract_text_pdfplumber(data: bytes, max_pages: int = MAX_PAGES) -> str:
    import pdfplumber

    parts = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages[:max_pages]:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                parts.append("")
    return "\n".join(parts)


def extract_pdf(data: bytes) -> dict:
    """Return {raw_text, table_count, engine, chars, ocr_pages}."""
    if not data:
        raise ValueError("空 PDF")

    body = ""
    engine = "none"
    try:
        body_pl = extract_text_pdfplumber(data)
        engine = "pdfplumber"
    except Exception:
        body_pl = ""
    try:
        body_py = extract_text_pypdf(data)
        if len(body_py.strip()) > len(body_pl.strip()):
            body, engine = body_py, "pypdf" if not body_pl else "pypdf+pdfplumber"
        else:
            body = body_pl or body_py
            if body_py and not body_pl:
                engine = "pypdf"
    except Exception:
        body = body_pl
        if not body:
            raise

    tables = []
    try:
        tables = extract_tables_pdfplumber(data)
    except Exception:
        tables = []

    ocr_blocks = []
    # If structural extract found few real numeric tables, run OCR
    if len(tables) < 2 or max((_decimal_hits(b) for b in tables), default=0) < 8:
        try:
            ocr_blocks = extract_tables_ocr(data)
            if ocr_blocks:
                engine = (engine + "+ocr") if engine else "ocr"
        except Exception:
            ocr_blocks = []

    tables = _dedupe_blocks(tables + ocr_blocks)
    body = sanitize_text(body or "")

    if ocr_blocks:
        header = (
            f"\n\n======== PDF TABLES (machine-extracted cell text) ========\n"
            f"（共检测到 {len(tables)} 张可用表格；含 OCR）\n\n"
        )
    elif tables:
        header = (
            f"\n\n======== PDF TABLES (machine-extracted cell text) ========\n"
            f"（共检测到 {len(tables)} 张可用表格）\n\n"
        )
    else:
        header = (
            "\n\n======== PDF TABLES (machine-extracted cell text) ========\n"
            "（共检测到 0 张可用表格；表可能是图片且 OCR 未装好/失败）\n"
        )

    if tables:
        appendix = sanitize_text(header + "\n\n".join(tables))
        room = MAX_CHARS - len(appendix) - 2
        if room < 5000:
            raw = body[:4000] + "\n\n" + appendix
        else:
            raw = body[:room] + "\n\n" + appendix
    else:
        raw = (body[: MAX_CHARS - len(header) - 10] + header)[:MAX_CHARS]

    raw = sanitize_text(raw)
    return {
        "raw_text": raw,
        "table_count": len(tables),
        "engine": engine,
        "chars": len(raw),
        "ocr_used": bool(ocr_blocks),
    }
