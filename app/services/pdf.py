import re
from pathlib import Path
from uuid import uuid4

import pdfplumber
from langchain_core.documents import Document
import pypdfium2 as pdfium
from pypdf import PdfReader
import pytesseract

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200
MIN_RETRY_CHUNK_SIZE = 80
OCR_RENDER_SCALE = 2.0


def _normalize_text(text: str) -> str:
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def sanitize_text_for_embedding(text: str) -> str:
    cleaned = text.replace("\x00", " ")
    cleaned = "".join(
        ch for ch in cleaned if ch == "\n" or ch == "\t" or ord(ch) >= 32
    )
    cleaned = re.sub(r"[|•·]{4,}", " ", cleaned)
    cleaned = re.sub(r"[_=~-]{4,}", " ", cleaned)
    return _normalize_text(cleaned)


def _split_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = sanitize_text_for_embedding(text)
    if not text:
        return []

    chunks: list[str] = []
    start = 0
    text_length = len(text)

    while start < text_length:
        end = min(start + chunk_size, text_length)
        if end < text_length:
            split_at = text.rfind("\n\n", start, end)
            if split_at == -1:
                split_at = text.rfind("\n", start, end)
            if split_at == -1:
                split_at = text.rfind(". ", start, end)
            if split_at == -1:
                split_at = end
            else:
                split_at += 1
        else:
            split_at = end

        chunk = text[start:split_at].strip()
        if chunk:
            chunks.append(chunk)

        if split_at >= text_length:
            break

        start = max(split_at - overlap, start + 1)

    return chunks


def split_chunk_for_retry(text: str, chunk_size: int) -> list[str]:
    sanitized = sanitize_text_for_embedding(text)
    if not sanitized:
        return []

    overlap = min(CHUNK_OVERLAP, max(0, chunk_size // 6))
    chunks = _split_text(sanitized, chunk_size=chunk_size, overlap=overlap)
    if len(chunks) > 1:
        return chunks

    midpoint = len(sanitized) // 2
    if midpoint < MIN_RETRY_CHUNK_SIZE:
        return [sanitized]

    left = sanitized[:midpoint].strip()
    right = sanitized[midpoint:].strip()
    fallback = [part for part in (left, right) if part]
    return fallback or [sanitized]


def _extract_page_texts(filepath: str) -> list[str]:
    reader = PdfReader(filepath)
    page_texts: list[str] = []

    for page in reader.pages:
        extracted = page.extract_text() or ""
        page_texts.append(_normalize_text(extracted))

    return page_texts


def _table_to_text(table: list[list[str | None]]) -> str:
    lines: list[str] = []
    for row in table:
        cells = [_normalize_text(cell or "") for cell in row]
        cleaned = [cell for cell in cells if cell]
        if cleaned:
            lines.append(" | ".join(cleaned))
    return "\n".join(lines).strip()


def _extract_page_tables(filepath: str) -> dict[int, list[str]]:
    page_tables: dict[int, list[str]] = {}

    with pdfplumber.open(filepath) as pdf:
        for page_index, page in enumerate(pdf.pages, start=1):
            tables = page.extract_tables() or []
            extracted_tables = []
            for table in tables:
                table_text = _table_to_text(table)
                if table_text:
                    extracted_tables.append(table_text)
            if extracted_tables:
                page_tables[page_index] = extracted_tables

    return page_tables


def _extract_pdf_content(filepath: str) -> tuple[list[str], dict[int, list[str]]]:
    return _extract_page_texts(filepath), _extract_page_tables(filepath)


def _resolve_tesseract_languages(ocr_languages: str) -> str:
    resolved: list[str] = []
    for item in ocr_languages.split("+"):
        lang = item.strip().lower()
        if lang in {"eng", "en", "english"}:
            resolved.append("eng")
        elif lang in {"hin", "hi", "hindi"}:
            resolved.append("hin")
    return "+".join(dict.fromkeys(resolved)) or "eng"


def _ocr_page_image(image, ocr_languages: str) -> str:
    raw_text = pytesseract.image_to_string(
        image,
        lang=_resolve_tesseract_languages(ocr_languages),
        config="--oem 1 --psm 6",
    )
    return sanitize_text_for_embedding(raw_text)


def _render_pdf_page(filepath: str, page_number: int):
    pdf = pdfium.PdfDocument(filepath)
    try:
        page = pdf[page_number - 1]
        bitmap = page.render(scale=OCR_RENDER_SCALE)
        return bitmap.to_pil().convert("RGB")
    finally:
        pdf.close()


def _ocr_pdf_pages(
    filepath: str,
    ocr_languages: str,
    page_numbers: list[int] | None = None,
    total_pages: int | None = None,
) -> list[str]:
    page_count = total_pages or len(PdfReader(filepath).pages)
    requested_pages = page_numbers or list(range(1, page_count + 1))
    recognized = [""] * page_count

    for page_number in requested_pages:
        image = _render_pdf_page(filepath, page_number)
        recognized[page_number - 1] = _ocr_page_image(image, ocr_languages)

    return recognized


def _merge_ocr_page_texts(
    page_texts: list[str],
    page_tables: dict[int, list[str]],
    ocr_page_texts: list[str],
) -> list[str]:
    merged = list(page_texts)
    total_pages = max(len(page_texts), len(ocr_page_texts))
    if len(merged) < total_pages:
        merged.extend([""] * (total_pages - len(merged)))

    for page_number in range(1, total_pages + 1):
        has_text = bool(page_number <= len(page_texts) and page_texts[page_number - 1].strip())
        has_tables = bool(page_tables.get(page_number))
        if has_text or has_tables:
            continue
        if page_number <= len(ocr_page_texts):
            merged[page_number - 1] = sanitize_text_for_embedding(ocr_page_texts[page_number - 1])

    return merged


def _build_documents_from_content(
    filepath: str,
    page_texts: list[str],
    page_tables: dict[int, list[str]],
    metadata: dict | None = None,
) -> list[Document]:
    base_metadata = metadata or {}
    filename = Path(filepath).name
    documents: list[Document] = []
    chunk_index = 0

    for page_number, page_text in enumerate(page_texts, start=1):
        sections: list[tuple[str, str]] = []
        if page_text:
            sections.append(("text", page_text))

        for table_index, table_text in enumerate(page_tables.get(page_number, []), start=1):
            sections.append(
                (
                    "table",
                    f"Table {table_index} on page {page_number}:\n{table_text}",
                )
            )

        for content_type, section_text in sections:
            for chunk in _split_text(section_text):
                documents.append(
                    Document(
                        page_content=chunk,
                        metadata={
                            "source": filename,
                            "chunk_id": str(uuid4()),
                            "chunk_index": chunk_index,
                            "page": str(page_number),
                            "content_type": content_type,
                            **base_metadata,
                        },
                    )
                )
                chunk_index += 1

    return documents


def _build_documents(filepath: str, metadata: dict | None = None) -> list[Document]:
    page_texts, page_tables = _extract_pdf_content(filepath)
    return _build_documents_from_content(filepath, page_texts, page_tables, metadata)


def extract_and_chunk_pdf(
    filepath: str,
    metadata: dict | None = None,
    enable_ocr: bool = False,
    ocr_languages: str = "eng",
) -> list[Document]:
    page_texts, page_tables = _extract_pdf_content(filepath)
    documents = _build_documents_from_content(filepath, page_texts, page_tables, metadata)
    if documents:
        if not enable_ocr:
            return documents

        empty_pages = [
            page_number
            for page_number in range(1, len(page_texts) + 1)
            if not page_texts[page_number - 1].strip() and not page_tables.get(page_number)
        ]
        if not empty_pages:
            return documents

        ocr_page_texts = _ocr_pdf_pages(
            filepath,
            ocr_languages,
            page_numbers=empty_pages,
            total_pages=len(page_texts),
        )
        merged_page_texts = _merge_ocr_page_texts(page_texts, page_tables, ocr_page_texts)
        merged_documents = _build_documents_from_content(
            filepath,
            merged_page_texts,
            page_tables,
            metadata,
        )
        if merged_documents:
            return merged_documents
        return documents

    if enable_ocr:
        ocr_page_texts = _ocr_pdf_pages(filepath, ocr_languages)
        documents = _build_documents_from_content(filepath, ocr_page_texts, {}, metadata)
        if documents:
            return documents

    raise ValueError(
        "No readable text or tables were extracted from the PDF. "
        "If this is a scanned or image-only PDF, enable OCR or preprocess the file first."
    )
