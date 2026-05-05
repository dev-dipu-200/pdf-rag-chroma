import re
from pathlib import Path

import pdfplumber
from langchain_core.documents import Document
import pypdfium2 as pdfium
from pypdf import PdfReader
import pytesseract

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200
MIN_RETRY_CHUNK_SIZE = 80
OCR_RENDER_SCALE = 2.0
TREE_BRANCH_FACTOR = 4
TREE_NODE_SNIPPET_CHARS = 320
VISUAL_KEYWORDS = ("figure", "fig.", "fig ", "chart", "graph", "plot", "diagram")
VISUAL_NEARBY_WORD_LIMIT = 40


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
    normalized_rows: list[list[str]] = []
    for row in table:
        cells = [_normalize_text(cell or "") for cell in row]
        if any(cells):
            normalized_rows.append(cells)

    if not normalized_rows:
        return ""

    header = normalized_rows[0]
    data_rows = normalized_rows[1:]
    lines: list[str] = []

    header_cells = [cell for cell in header if cell]
    if header_cells:
        lines.append(f"Columns: {' | '.join(header_cells)}")

    for row_index, row in enumerate(data_rows, start=1):
        pairs: list[str] = []
        for col_index, cell in enumerate(row):
            if not cell:
                continue
            column_name = header[col_index] if col_index < len(header) and header[col_index] else f"column_{col_index + 1}"
            pairs.append(f"{column_name}: {cell}")
        if pairs:
            lines.append(f"Row {row_index}: " + " | ".join(pairs))

    if len(lines) == 1 and header_cells:
        return header_cells[0] if len(header_cells) == 1 else " | ".join(header_cells)
    return "\n".join(lines).strip()


def _extract_visual_lines(page_text: str) -> list[str]:
    lines = [_normalize_text(line) for line in page_text.splitlines()]
    return [
        line for line in lines
        if line and any(keyword in line.lower() for keyword in VISUAL_KEYWORDS)
    ]


def _collect_words_near_visuals(page) -> list[str]:
    nearby: list[str] = []
    words = page.extract_words() or []
    if not words:
        return nearby

    visual_regions = list(page.images or [])
    visual_regions.extend(rect for rect in (page.rects or []) if rect.get("width", 0) > 120 and rect.get("height", 0) > 120)
    if not visual_regions:
        return nearby

    for region_index, region in enumerate(visual_regions, start=1):
        x0 = float(region.get("x0", 0))
        x1 = float(region.get("x1", 0))
        top = float(region.get("top", 0))
        bottom = float(region.get("bottom", 0))

        related_words = [
            _normalize_text(word.get("text", ""))
            for word in words
            if (
                x0 - 40 <= float(word.get("x0", 0)) <= x1 + 40
                and top - 60 <= float(word.get("top", 0)) <= bottom + 60
            )
        ]
        related_words = [word for word in related_words if word]
        if related_words:
            nearby.append(
                f"Visual {region_index} nearby text: {' '.join(related_words[:VISUAL_NEARBY_WORD_LIMIT])}"
            )

    return nearby


def _extract_page_visuals(filepath: str) -> dict[int, list[str]]:
    page_visuals: dict[int, list[str]] = {}

    with pdfplumber.open(filepath) as pdf:
        for page_index, page in enumerate(pdf.pages, start=1):
            page_text = page.extract_text() or ""
            visual_lines = _extract_visual_lines(page_text)
            nearby_words = _collect_words_near_visuals(page)
            visuals = visual_lines + nearby_words
            deduped: list[str] = []
            seen: set[str] = set()
            for item in visuals:
                normalized = sanitize_text_for_embedding(item)
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    deduped.append(normalized)
            if deduped:
                page_visuals[page_index] = deduped

    return page_visuals


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


def _extract_pdf_content(filepath: str) -> tuple[list[str], dict[int, list[str]], dict[int, list[str]]]:
    return _extract_page_texts(filepath), _extract_page_tables(filepath), _extract_page_visuals(filepath)


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
    page_visuals: dict[int, list[str]],
    ocr_page_texts: list[str],
) -> list[str]:
    merged = list(page_texts)
    total_pages = max(len(page_texts), len(ocr_page_texts))
    if len(merged) < total_pages:
        merged.extend([""] * (total_pages - len(merged)))

    for page_number in range(1, total_pages + 1):
        has_text = bool(page_number <= len(page_texts) and page_texts[page_number - 1].strip())
        has_tables = bool(page_tables.get(page_number))
        has_visuals = bool(page_visuals.get(page_number))
        if has_text or has_tables or has_visuals:
            continue
        if page_number <= len(ocr_page_texts):
            merged[page_number - 1] = sanitize_text_for_embedding(ocr_page_texts[page_number - 1])

    return merged


def _build_documents_from_content(
    filepath: str,
    page_texts: list[str],
    page_tables: dict[int, list[str]],
    page_visuals: dict[int, list[str]],
    metadata: dict | None = None,
) -> list[Document]:
    base_metadata = metadata or {}
    filename = Path(filepath).name
    documents: list[Document] = []

    for page_number, page_text in enumerate(page_texts, start=1):
        sections: list[str] = []
        if page_text:
            sections.append(page_text)

        for table_index, table_text in enumerate(page_tables.get(page_number, []), start=1):
            sections.append(f"Table {table_index} on page {page_number}:\n{table_text}")

        for visual_index, visual_text in enumerate(page_visuals.get(page_number, []), start=1):
            sections.append(f"Figure or chart context {visual_index} on page {page_number}:\n{visual_text}")

        page_content = sanitize_text_for_embedding("\n\n".join(section for section in sections if section))
        if not page_content:
            continue

        documents.append(
            Document(
                page_content=page_content,
                metadata={
                    "source": filename,
                    "page": str(page_number),
                    "page_number": str(page_number),
                    "content_type": "page",
                    **base_metadata,
                },
            )
        )

    return documents


def _page_range_label(page_start: int, page_end: int) -> str:
    if page_start == page_end:
        return str(page_start)
    return f"{page_start}-{page_end}"


def _condense_for_tree_node(text: str, max_chars: int = TREE_NODE_SNIPPET_CHARS) -> str:
    cleaned = sanitize_text_for_embedding(text)
    if len(cleaned) <= max_chars:
        return cleaned

    split_at = cleaned.rfind(". ", 0, max_chars)
    if split_at == -1:
        split_at = cleaned.rfind("\n", 0, max_chars)
    if split_at == -1:
        split_at = max_chars

    condensed = cleaned[:split_at].strip()
    return condensed or cleaned[:max_chars].strip()


def _build_tree_documents(leaf_documents: list[Document]) -> list[Document]:
    if len(leaf_documents) <= 1:
        return []

    tree_documents: list[Document] = []
    current_level = leaf_documents
    level = 1

    while len(current_level) > 1:
        next_level: list[Document] = []
        for start in range(0, len(current_level), TREE_BRANCH_FACTOR):
            children = current_level[start : start + TREE_BRANCH_FACTOR]
            if len(children) <= 1:
                next_level.extend(children)
                continue

            first_page = int(children[0].metadata.get("page_start") or children[0].metadata.get("page") or 0)
            last_page = int(children[-1].metadata.get("page_end") or children[-1].metadata.get("page") or 0)
            source = children[0].metadata.get("source", "unknown")
            document_id = children[0].metadata.get("document_id")
            snippets = [
                f"Pages {_page_range_label(int(child.metadata.get('page_start') or child.metadata.get('page') or 0), int(child.metadata.get('page_end') or child.metadata.get('page') or 0))}: "
                f"{_condense_for_tree_node(child.page_content)}"
                for child in children
            ]
            content = sanitize_text_for_embedding(
                f"Tree node level {level} covering pages {_page_range_label(first_page, last_page)}.\n\n"
                + "\n\n".join(snippets)
            )
            if not content:
                continue

            node = Document(
                page_content=content,
                metadata={
                    "source": source,
                    "document_id": document_id,
                    "tree_level": str(level),
                    "content_type": "tree",
                    "page_start": str(first_page),
                    "page_end": str(last_page),
                    "page": _page_range_label(first_page, last_page),
                    "page_number": _page_range_label(first_page, last_page),
                    "child_count": str(len(children)),
                },
            )
            tree_documents.append(node)
            next_level.append(node)

        if len(next_level) == len(current_level):
            break
        current_level = next_level
        level += 1

    return tree_documents


def _build_documents(filepath: str, metadata: dict | None = None) -> list[Document]:
    page_texts, page_tables, page_visuals = _extract_pdf_content(filepath)
    return _build_documents_from_content(filepath, page_texts, page_tables, page_visuals, metadata)


def extract_and_chunk_pdf(
    filepath: str,
    metadata: dict | None = None,
    enable_ocr: bool = False,
    ocr_languages: str = "eng",
) -> list[Document]:
    page_texts, page_tables, page_visuals = _extract_pdf_content(filepath)
    documents = _build_documents_from_content(filepath, page_texts, page_tables, page_visuals, metadata)
    if documents:
        if not enable_ocr:
            return documents + _build_tree_documents(documents)

        empty_pages = [
            page_number
            for page_number in range(1, len(page_texts) + 1)
            if not page_texts[page_number - 1].strip() and not page_tables.get(page_number) and not page_visuals.get(page_number)
        ]
        if not empty_pages:
            return documents

        ocr_page_texts = _ocr_pdf_pages(
            filepath,
            ocr_languages,
            page_numbers=empty_pages,
            total_pages=len(page_texts),
        )
        merged_page_texts = _merge_ocr_page_texts(page_texts, page_tables, page_visuals, ocr_page_texts)
        merged_documents = _build_documents_from_content(
            filepath,
            merged_page_texts,
            page_tables,
            page_visuals,
            metadata,
        )
        if merged_documents:
            return merged_documents + _build_tree_documents(merged_documents)
        return documents + _build_tree_documents(documents)

    if enable_ocr:
        ocr_page_texts = _ocr_pdf_pages(filepath, ocr_languages)
        documents = _build_documents_from_content(filepath, ocr_page_texts, {}, {}, metadata)
        if documents:
            return documents + _build_tree_documents(documents)

    raise ValueError(
        "No readable text or tables were extracted from the PDF. "
        "If this is a scanned or image-only PDF, enable OCR or preprocess the file first."
    )
