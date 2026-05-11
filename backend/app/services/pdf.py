# pdf.py
import re
import subprocess
import tempfile
from pathlib import Path
from langchain_core.documents import Document
import pymupdf
import pymupdf4llm

from app.dependencies import PDF_PARSER

# Increased sizes to maintain context for dense Hindi script
CHUNK_SIZE = 2400
CHUNK_OVERLAP = 400
MIN_RETRY_CHUNK_SIZE = 80
TREE_BRANCH_FACTOR = 4
TREE_NODE_SNIPPET_CHARS = 320
OCR_DPI = 400
OCR_PSM = "6"
LEGACY_GARBLED_HINTS = (
    "jktk",
    "ugha",
    "gksrk",
    "nku",
    "dk",
    "osQ",
    "vk",
    "gq,",
)


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


def _devanagari_ratio(text: str) -> float:
    visible = [ch for ch in text if not ch.isspace()]
    if not visible:
        return 0.0
    devanagari = sum(1 for ch in visible if "\u0900" <= ch <= "\u097f")
    return devanagari / len(visible)


def _latin_ratio(text: str) -> float:
    visible = [ch for ch in text if not ch.isspace()]
    if not visible:
        return 0.0
    latin = sum(1 for ch in visible if ("a" <= ch.lower() <= "z"))
    return latin / len(visible)


def _looks_like_legacy_hindi_garble(text: str) -> bool:
    sample = sanitize_text_for_embedding(text)
    if len(sample) < 120:
        return False

    lowered = sample.lower()
    hint_hits = sum(lowered.count(token) for token in LEGACY_GARBLED_HINTS)
    devanagari_ratio = _devanagari_ratio(sample)
    latin_ratio = _latin_ratio(sample)

    return devanagari_ratio < 0.1 and latin_ratio > 0.35 and hint_hits >= 3


def _garble_hint_hits(text: str) -> int:
    lowered = sanitize_text_for_embedding(text).lower()
    return sum(lowered.count(token) for token in LEGACY_GARBLED_HINTS)


def _ocr_cleanup(text: str) -> str:
    cleaned = text.replace("\x0c", " ")
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    lines = []
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            lines.append("")
            continue
        if len(line) <= 2 and not any(ch.isalpha() for ch in line):
            continue
        if re.fullmatch(r"[\W_]+", line):
            continue
        if _latin_ratio(line) > 0.75 and _devanagari_ratio(line) == 0.0 and len(line) < 12:
            continue
        lines.append(line)
    return sanitize_text_for_embedding("\n".join(lines))


def _strip_docling_artifacts(text: str) -> str:
    cleaned = re.sub(r"<!--\s*image\s*-->", " ", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"&amp;", "&", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return sanitize_text_for_embedding(cleaned)


def _page_quality_score(text: str) -> float:
    cleaned = sanitize_text_for_embedding(text)
    if not cleaned:
        return float("-inf")

    devanagari = _devanagari_ratio(cleaned)
    latin = _latin_ratio(cleaned)
    garble_hits = _garble_hint_hits(cleaned)
    digit_ratio = sum(ch.isdigit() for ch in cleaned) / max(1, len(cleaned))
    length_score = min(len(cleaned), 2500) / 250.0

    return (
        length_score
        + devanagari * 18.0
        - latin * 6.0
        - garble_hits * 4.0
        - digit_ratio * 3.0
    )


def _should_retry_with_ocr(pages: list[dict]) -> bool:
    if not pages:
        return True

    inspected = 0
    suspicious = 0
    empty = 0

    for page in pages:
        text = _page_text(page)
        if not text:
            empty += 1
            inspected += 1
            continue
        inspected += 1
        if _looks_like_legacy_hindi_garble(text):
            suspicious += 1

    if inspected == 0:
        return True

    return empty == inspected or (suspicious / inspected) >= 0.5


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


def _extract_pages(
    filepath: str,
    use_ocr: bool = False,
    ocr_languages: str = "hin+eng",
) -> list[dict]:
    """
    Extracts pages using standard text extraction by default.
    If use_ocr is True, it triggers Tesseract to read the visual layer.
    """
    base_args = {
        "page_chunks": True,
        "write_images": False,
        "ignore_images": True,
    }

    extract_args = dict(base_args)
    if use_ocr:
        extract_args.update(
            force_ocr=True,
            ocr_language=ocr_languages,
            dpi=300,
        )
    else:
        extract_args.update(table_strategy="lines")

    try:
        return pymupdf4llm.to_markdown(filepath, **extract_args)
    except TypeError as exc:
        error_text = str(exc)
        if "missing 1 required positional argument: 'doc'" not in error_text:
            raise

    with pymupdf.open(filepath) as doc:
        return pymupdf4llm.to_markdown(doc, **extract_args)


def _extract_pages_with_tesseract(
    filepath: str,
    ocr_languages: str = "hin+eng",
    dpi: int = OCR_DPI,
) -> list[dict]:
    pages: list[dict] = []
    matrix = pymupdf.Matrix(dpi / 72.0, dpi / 72.0)

    with pymupdf.open(filepath) as doc:
        for index, page in enumerate(doc, start=1):
            pix = page.get_pixmap(matrix=matrix, colorspace=pymupdf.csGRAY, alpha=False)
            with tempfile.NamedTemporaryFile(suffix=".png") as handle:
                pix.save(handle.name)
                result = subprocess.run(
                    [
                        "tesseract",
                        handle.name,
                        "stdout",
                        "-l",
                        ocr_languages,
                        "--oem",
                        "1",
                        "--psm",
                        OCR_PSM,
                        "-c",
                        "preserve_interword_spaces=1",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            pages.append(
                {
                    "page": index,
                    "page_number": index,
                    "text": _ocr_cleanup(result.stdout),
                    "metadata": {"page": index, "page_number": index, "extraction_method": "tesseract"},
                }
            )
    return pages


def _ocr_page_with_tesseract(
    page,
    page_number: int,
    ocr_languages: str = "hin+eng",
    dpi: int = OCR_DPI,
) -> dict:
    matrix = pymupdf.Matrix(dpi / 72.0, dpi / 72.0)
    pix = page.get_pixmap(matrix=matrix, colorspace=pymupdf.csGRAY, alpha=False)
    with tempfile.NamedTemporaryFile(suffix=".png") as handle:
        pix.save(handle.name)
        result = subprocess.run(
            [
                "tesseract",
                handle.name,
                "stdout",
                "-l",
                ocr_languages,
                "--oem",
                "1",
                "--psm",
                OCR_PSM,
                "-c",
                "preserve_interword_spaces=1",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    return {
        "page": page_number,
        "page_number": page_number,
        "text": _ocr_cleanup(result.stdout),
        "metadata": {
            "page": page_number,
            "page_number": page_number,
            "extraction_method": "tesseract",
        },
    }


def _extract_pages_with_docling(filepath: str, ocr_languages: str = "hin+eng") -> list[dict]:
    try:
        from docling.document_converter import DocumentConverter
    except ImportError as exc:
        raise RuntimeError(
            "Docling is not installed. Add 'docling' to backend dependencies and rebuild the image."
        ) from exc

    converter = DocumentConverter()
    pages: list[dict] = []

    with pymupdf.open(filepath) as src_doc:
        for index in range(len(src_doc)):
            source_page = src_doc[index]
            with tempfile.NamedTemporaryFile(suffix=".pdf") as handle:
                single_page_doc = pymupdf.open()
                single_page_doc.insert_pdf(src_doc, from_page=index, to_page=index)
                single_page_doc.save(handle.name)
                single_page_doc.close()

                result = converter.convert(handle.name)
                document = result.document
                markdown = _strip_docling_artifacts(document.export_to_markdown())

            if (
                not markdown
                or _looks_like_legacy_hindi_garble(markdown)
                or (_devanagari_ratio(markdown) < 0.15 and _latin_ratio(markdown) > 0.2)
            ):
                pages.append(
                    _ocr_page_with_tesseract(
                        source_page,
                        index + 1,
                        ocr_languages=ocr_languages,
                        dpi=OCR_DPI,
                    )
                )
                continue

            pages.append(
                {
                    "page": index + 1,
                    "page_number": index + 1,
                    "text": markdown,
                    "metadata": {
                        "page": index + 1,
                        "page_number": index + 1,
                        "extraction_method": "docling",
                    },
                }
            )
    return pages


def _page_text(page_chunk: dict) -> str:
    for key in ("text", "md", "markdown", "page_content"):
        value = page_chunk.get(key)
        if isinstance(value, str) and value.strip():
            return sanitize_text_for_embedding(value)
    return ""


def _merge_page_extractions(
    native_pages: list[dict],
    ocr_pages: list[dict],
    prefer_ocr: bool = False,
) -> list[dict]:
    merged: list[dict] = []
    max_pages = max(len(native_pages), len(ocr_pages))

    for index in range(max_pages):
        native_page = native_pages[index] if index < len(native_pages) else {}
        ocr_page = ocr_pages[index] if index < len(ocr_pages) else {}

        native_text = _page_text(native_page)
        ocr_text = _page_text(ocr_page)

        native_score = _page_quality_score(native_text)
        ocr_score = _page_quality_score(ocr_text)
        native_garbled = _looks_like_legacy_hindi_garble(native_text)
        ocr_has_devanagari = _devanagari_ratio(ocr_text) > 0.2
        native_has_devanagari = _devanagari_ratio(native_text) > 0.2

        if native_garbled and ocr_text and ocr_has_devanagari:
            chosen = dict(ocr_page)
        elif prefer_ocr and ocr_text and ocr_score >= native_score - 0.5:
            chosen = dict(ocr_page)
        elif ocr_has_devanagari and not native_has_devanagari and ocr_score >= native_score - 2.0:
            chosen = dict(ocr_page)
        elif ocr_score > native_score + 1.0:
            chosen = dict(ocr_page)
        else:
            chosen = dict(native_page or ocr_page)

        metadata = dict(chosen.get("metadata") or {})
        metadata["native_score"] = native_score
        metadata["ocr_score"] = ocr_score
        metadata["native_garbled"] = native_garbled
        chosen["metadata"] = metadata

        if not chosen.get("page"):
            chosen["page"] = index + 1
        if not chosen.get("page_number"):
            chosen["page_number"] = index + 1
        merged.append(chosen)

    return merged


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
        page_metadata = page_chunk.get("metadata")
        if not isinstance(page_metadata, dict):
            page_metadata = {}

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
                        **page_metadata,
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
    Extract text accuracy-first. When OCR is enabled, prefer custom Tesseract output
    and only fall back to native extraction for pages where OCR returns nothing useful.
    """
    if PDF_PARSER == "docling":
        try:
            pages_data = _extract_pages_with_docling(filepath, ocr_languages=ocr_languages)
            leaf_documents = _build_documents_from_pages(filepath, pages_data, metadata)
            if not include_tree_documents:
                return leaf_documents
            tree_docs = _build_tree_documents(leaf_documents)
            return leaf_documents + tree_docs
        except Exception as exc:
            print(f"Docling failed, falling back to OCR/native parser: {exc}")

    try:
        native_pages = _extract_pages(filepath, use_ocr=False, ocr_languages=ocr_languages)
        pages_data = native_pages

        should_use_ocr = enable_ocr or _should_retry_with_ocr(native_pages)
        if should_use_ocr:
            ocr_pages = _extract_pages_with_tesseract(
                filepath,
                ocr_languages=ocr_languages,
                dpi=OCR_DPI,
            )
            if enable_ocr:
                pages_data = []
                max_pages = max(len(native_pages), len(ocr_pages))
                for index in range(max_pages):
                    native_page = native_pages[index] if index < len(native_pages) else {}
                    ocr_page = ocr_pages[index] if index < len(ocr_pages) else {}
                    ocr_text = _page_text(ocr_page)
                    chosen = dict(ocr_page) if len(ocr_text) >= 40 else dict(native_page or ocr_page)
                    if not chosen.get("page"):
                        chosen["page"] = index + 1
                    if not chosen.get("page_number"):
                        chosen["page_number"] = index + 1
                    pages_data.append(chosen)
            else:
                pages_data = _merge_page_extractions(
                    native_pages,
                    ocr_pages,
                    prefer_ocr=False,
                )
    except Exception as e:
        print(f"OCR failed, falling back to standard: {e}")
        pages_data = _extract_pages(filepath, use_ocr=False, ocr_languages=ocr_languages)

    leaf_documents = _build_documents_from_pages(filepath, pages_data, metadata)

    if not include_tree_documents:
        return leaf_documents

    tree_docs = _build_tree_documents(leaf_documents)
    return leaf_documents + tree_docs
