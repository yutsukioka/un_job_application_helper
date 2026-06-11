#!/usr/bin/env python3
"""
Convert a PDF to Markdown using Microsoft's MarkItDown.

Recommended install:
    pip install "markitdown[pdf]" pymupdf

Example:
    python convert_pdf_to_markdown_markitdown.py RFPS-3626000015-AK.pdf -o RFPS-3626000015-AK.md

Why page mode?
    Large PDFs can appear to hang when converted all at once. The default page-by-page
    mode prints progress, adds page headings, and applies a timeout to each page.
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import queue
import re
import sys
import tempfile
from pathlib import Path
from typing import Iterable, Tuple


def clean_markdown(text: str, *, remove_docusign_id: bool = True) -> str:
    """Small cleanup pass for PDF-derived Markdown."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    cleaned_lines: list[str] = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t]+$", "", line)

        # The attached RFP repeats this line on many pages; remove it by default.
        if remove_docusign_id and line.strip().startswith("Docusign Envelope ID:"):
            continue

        cleaned_lines.append(line)

    text = "\n".join(cleaned_lines)

    # Promote common RFP headings if MarkItDown emits them as plain text.
    text = re.sub(r"(?m)^(SECTION\s+\d+\s*:\s+.+)$", r"# \1", text)
    text = re.sub(r"(?m)^(FORM\s+[A-Z](?:-\d+)?\s*:\s+.+)$", r"## \1", text)

    # Collapse excessive blank lines.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _markitdown_worker(pdf_path: str, output_queue: mp.Queue) -> None:
    """Runs in a child process so the parent can kill it on timeout."""
    try:
        from markitdown import MarkItDown

        md = MarkItDown()
        result = md.convert(pdf_path)
        output_queue.put(("ok", result.text_content or ""))
    except Exception as exc:  # pragma: no cover - meant for CLI robustness
        output_queue.put(("error", f"{type(exc).__name__}: {exc}"))


def convert_with_timeout(pdf_path: Path, timeout_seconds: int) -> str:
    """Convert a PDF using MarkItDown, terminating if conversion exceeds timeout."""
    output_queue: mp.Queue = mp.Queue()
    process = mp.Process(
        target=_markitdown_worker,
        args=(str(pdf_path), output_queue),
        daemon=True,
    )
    process.start()
    process.join(timeout_seconds)

    if process.is_alive():
        process.terminate()
        process.join(5)
        raise TimeoutError(f"MarkItDown timed out after {timeout_seconds} seconds")

    try:
        status, payload = output_queue.get_nowait()
    except queue.Empty as exc:
        raise RuntimeError("MarkItDown exited without returning output") from exc

    if status == "error":
        raise RuntimeError(payload)

    return payload


def split_pdf_into_single_pages(pdf_path: Path, work_dir: Path) -> Tuple[int, Iterable[Tuple[int, Path]]]:
    """Split PDF into one-page PDFs. Uses PyMuPDF only for page slicing."""
    import fitz  # PyMuPDF

    source_doc = fitz.open(pdf_path)
    total_pages = source_doc.page_count

    def iterator() -> Iterable[Tuple[int, Path]]:
        try:
            for page_index in range(total_pages):
                single_page_doc = fitz.open()
                single_page_doc.insert_pdf(source_doc, from_page=page_index, to_page=page_index)
                page_path = work_dir / f"page_{page_index + 1:04d}.pdf"
                single_page_doc.save(page_path)
                single_page_doc.close()
                yield page_index + 1, page_path
        finally:
            source_doc.close()

    return total_pages, iterator()


def convert_full_pdf(input_pdf: Path, output_md: Path, timeout_seconds: int, *, remove_docusign_id: bool) -> None:
    raw = convert_with_timeout(input_pdf, timeout_seconds)
    output_md.write_text(clean_markdown(raw, remove_docusign_id=remove_docusign_id) + "\n", encoding="utf-8")


def convert_pdf_by_pages(input_pdf: Path, output_md: Path, timeout_seconds: int, *, remove_docusign_id: bool) -> None:
    parts: list[str] = []

    with tempfile.TemporaryDirectory(prefix="markitdown_pages_") as tmp:
        tmp_dir = Path(tmp)
        total_pages, page_iter = split_pdf_into_single_pages(input_pdf, tmp_dir)

        for page_number, page_pdf in page_iter:
            print(f"Converting page {page_number}/{total_pages}...", file=sys.stderr, flush=True)

            try:
                raw = convert_with_timeout(page_pdf, timeout_seconds)
                page_md = clean_markdown(raw, remove_docusign_id=remove_docusign_id)
            except Exception as exc:
                page_md = f"> [!WARNING]\n> Page {page_number} was not converted: {type(exc).__name__}: {exc}"

            if page_md:
                parts.append(f"## Page {page_number}\n\n{page_md}")
            else:
                parts.append(f"## Page {page_number}\n\n> [!NOTE]\n> No text was extracted from this page.")

    final_md = "\n\n---\n\n".join(parts).strip() + "\n"
    output_md.write_text(final_md, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert PDF to Markdown using Microsoft MarkItDown.")
    parser.add_argument("pdf", type=Path, help="Input PDF path")
    parser.add_argument("-o", "--output", type=Path, help="Output Markdown path. Defaults to INPUT_STEM.md")
    parser.add_argument(
        "--mode",
        choices=("pages", "full"),
        default="pages",
        help="Use 'pages' for progress and per-page timeout; use 'full' to convert the whole PDF at once.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=90,
        help="Timeout in seconds. In page mode, this applies to each page; in full mode, it applies to the whole PDF.",
    )
    parser.add_argument(
        "--keep-docusign-id",
        action="store_true",
        help="Keep repeated 'Docusign Envelope ID' lines instead of removing them.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_pdf: Path = args.pdf
    output_md: Path = args.output or input_pdf.with_suffix(".md")

    if not input_pdf.exists():
        print(f"Input PDF not found: {input_pdf}", file=sys.stderr)
        return 2

    if input_pdf.suffix.lower() != ".pdf":
        print(f"Input file does not look like a PDF: {input_pdf}", file=sys.stderr)
        return 2

    remove_docusign_id = not args.keep_docusign_id

    if args.mode == "full":
        convert_full_pdf(input_pdf, output_md, args.timeout, remove_docusign_id=remove_docusign_id)
    else:
        convert_pdf_by_pages(input_pdf, output_md, args.timeout, remove_docusign_id=remove_docusign_id)

    print(f"Markdown written to: {output_md}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
