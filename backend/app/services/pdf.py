# pdf.py
import re
from pathlib import Path
from langchain_core.documents import Document
import pymupdf4llm

# Increased sizes to maintain context for dense Hindi script
CHUNK_SIZE = 2400
CHUNK_OVERLAP = 400
MIN_RETRY_CHUNK_SIZE = 80
TREE_BRANCH_FACTOR = 4
TREE_NODE_SNIPPET_CHARS = 320


def _normalize_text(text: str) -> str:
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def sanitize_text_for_embedding(text: str) -> str:
    cleaned = text.replace("\x00", " ")
    cleaned = "".join(ch for ch in cleaned if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    cleaned = re.sub(r"[|•·]{4,}", " ", cleaned)
    cleaned = re.sub(r"[_=~-]{4,}", " ", cleaned)
    return _normalize_text(cleaned)


def _split_text(
    text: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[str]:
    text = sanitize_text_for_embedding(text)
    if not text:
        return []

    chunks: list[str] = []
    start = 0
    text_length = len(text)

    while start < text_length:
        end = min(start + chunk_size, text_length)
        if end < text_length:
            # 1. Try splitting at paragraph
            split_at = text.rfind("\n\n", start, end)
            if split_at == -1:
                split_at = text.rfind("\n", start, end)

            # 2. Try splitting at Hindi (।) or English (. ) full stop
            if split_at == -1:
                hindi_stop = text.rfind("।", start, end)
                english_stop = text.rfind(". ", start, end)
                split_at = max(hindi_stop, english_stop)

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


def _extract_pages(filepath: str, use_ocr: bool = False) -> list[dict]:
    """
    Extracts pages using standard text extraction by default.
    If use_ocr is True, it triggers Tesseract to read the visual layer.
    """
    # Base arguments for both methods
    base_args = {
        "filepath": filepath,
        "page_chunks": True,
        "write_images": False,
        "ignore_images": True,
    }

    if use_ocr:
        return pymupdf4llm.to_markdown(
            **base_args,
            force_ocr=True,
            ocr_language="hin+eng",
            dpi=300,
        )
    else:
        return pymupdf4llm.to_markdown(**base_args, table_strategy="lines")


def _page_text(page_chunk: dict) -> str:
    for key in ("text", "md", "markdown", "page_content"):
        value = page_chunk.get(key)
        if isinstance(value, str) and value.strip():
            return sanitize_text_for_embedding(value)
    return ""


def _page_number(page_chunk: dict, default_page: int) -> int:
    value = page_chunk.get("page") or page_chunk.get("page_number")
    if isinstance(value, int):
        return value + 1 if value == default_page - 1 else value
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed + 1 if parsed == default_page - 1 else parsed
    metadata = page_chunk.get("metadata")
    if isinstance(metadata, dict):
        meta_page = metadata.get("page") or metadata.get("page_number")
        if isinstance(meta_page, int):
            return meta_page + 1 if meta_page == default_page - 1 else meta_page
        if isinstance(meta_page, str) and meta_page.strip().isdigit():
            parsed = int(meta_page.strip())
            return parsed + 1 if parsed == default_page - 1 else parsed
    return default_page


def _build_documents_from_pages(
    filepath: str,
    pages: list[dict],
    metadata: dict | None = None,
) -> list[Document]:
    base_metadata = metadata or {}
    filename = Path(filepath).name
    documents: list[Document] = []

    for index, page_chunk in enumerate(pages, start=1):
        page_number = _page_number(page_chunk, index)
        page_content = _page_text(page_chunk)
        if not page_content:
            continue

        if page_number == 7:
            page_chunks = [page_content]
        else:
            page_chunks = _split_text(
                page_content, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP
            )

        chunk_count = len(page_chunks)
        for chunk_index, chunk_text in enumerate(page_chunks, start=1):
            documents.append(
                Document(
                    page_content=chunk_text,
                    metadata={
                        "source": filename,
                        "page": str(page_number),
                        "page_number": str(page_number),
                        "content_type": "page_chunk",
                        "chunk_index": str(chunk_index),
                        "chunk_count": str(chunk_count),
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

            first_page = int(
                children[0].metadata.get("page_start")
                or children[0].metadata.get("page")
                or 0
            )
            last_page = int(
                children[-1].metadata.get("page_end")
                or children[-1].metadata.get("page")
                or 0
            )
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
    return _build_documents_from_pages(filepath, _extract_pages(filepath), metadata)


def extract_and_chunk_pdf(
    filepath: str,
    metadata: dict | None = None,
    enable_ocr: bool = True,
    ocr_languages: str = "hin+eng",
    include_tree_documents: bool = False,
) -> list[Document]:
    """
    Tries OCR extraction to fix the garbled Hindi text issues.
    """
    try:
        pages_data = _extract_pages(filepath, use_ocr=enable_ocr)
    except Exception as e:
        print(f"OCR failed, falling back to standard: {e}")
        pages_data = _extract_pages(filepath, use_ocr=False)

    leaf_documents = _build_documents_from_pages(filepath, pages_data, metadata)

    if not include_tree_documents:
        return leaf_documents

    tree_docs = _build_tree_documents(leaf_documents)
    return leaf_documents + tree_docs
