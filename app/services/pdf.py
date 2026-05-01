import re
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

import pdfplumber
from langchain_core.documents import Document
from pypdf import PdfReader

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200


def _normalize_text(text: str) -> str:
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = _normalize_text(text)
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


def _build_documents(filepath: str, metadata: dict | None = None) -> list[Document]:
    base_metadata = metadata or {}
    filename = Path(filepath).name
    page_texts = _extract_page_texts(filepath)
    page_tables = _extract_page_tables(filepath)
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


def _ocr_pdf(filepath: str, ocr_languages: str) -> str:
    temp_dir = tempfile.mkdtemp(prefix="ocrmypdf-")
    output_path = Path(temp_dir) / f"{Path(filepath).stem}.ocr.pdf"
    command = [
        "ocrmypdf",
        "--skip-text",
        "--language",
        ocr_languages,
        filepath,
        str(output_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "OCR is enabled but 'ocrmypdf' is not installed in the runtime environment."
        ) from exc
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or exc.stdout or "").strip()
        raise RuntimeError(
            f"OCR preprocessing failed: {details or 'unknown ocrmypdf error'}"
        ) from exc
    return str(output_path)


def extract_and_chunk_pdf(
    filepath: str,
    metadata: dict | None = None,
    enable_ocr: bool = False,
    ocr_languages: str = "eng",
) -> list[Document]:
    documents = _build_documents(filepath, metadata)
    if documents:
        return documents

    if enable_ocr:
        ocr_filepath = _ocr_pdf(filepath, ocr_languages)
        documents = _build_documents(ocr_filepath, metadata)
        if documents:
            return documents

    raise ValueError(
        "No readable text or tables were extracted from the PDF. "
        "If this is a scanned or image-only PDF, enable OCR or preprocess the file first."
    )
