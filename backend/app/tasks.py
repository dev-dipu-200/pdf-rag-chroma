import logging
from datetime import datetime
from uuid import uuid4

from langchain_core.documents import Document
from ollama import ResponseError

from app.celery_app import celery_app
from app.database import SessionLocal
from app.dependencies import (
    EmbeddingInitializationError,
    EMBEDDING_BATCH_SIZE,
    ENABLE_OCR,
    OCR_LANGS,
    get_vectorstore,
    reset_vectorstore,
    shared_collection_name,
)
from app.models import PdfDocument
from app.services.pdf import (
    MIN_RETRY_CHUNK_SIZE,
    extract_and_chunk_pdf,
    sanitize_text_for_embedding,
    split_chunk_for_retry,
)

logger = logging.getLogger(__name__)
PARSED_PREVIEW_CHARS = 1200


def _build_index_metadata(document: PdfDocument) -> dict[str, str]:
    return {
        "source": document.original_filename,
        "document_id": str(document.id),
        "user_id": str(document.user_id),
    }


def _set_document_state(document: PdfDocument, status: str, error_message: str | None = None) -> None:
    document.status = status
    document.error_message = error_message
    document.updated_at = datetime.utcnow()


def _clone_document(document: Document, content: str, depth: int, part_index: int) -> Document:
    metadata = dict(document.metadata)
    metadata["chunk_id"] = str(uuid4())
    metadata["chunk_retry_depth"] = depth
    metadata["chunk_retry_part"] = part_index
    return Document(page_content=content, metadata=metadata)


def _log_parsed_documents(document: PdfDocument, docs: list[Document]) -> None:
    logger.info(
        "Parsed %s chunk(s) from document_id=%s file=%s",
        len(docs),
        document.id,
        document.original_filename,
    )
    for index, parsed_doc in enumerate(docs, start=1):
        preview = sanitize_text_for_embedding(parsed_doc.page_content)[:PARSED_PREVIEW_CHARS]
        logger.info(
            "Parsed preview %s/%s document_id=%s page=%s metadata=%s\n%s",
            index,
            len(docs),
            document.id,
            parsed_doc.metadata.get("page", "?"),
            parsed_doc.metadata,
            preview,
        )


def _add_document_with_retry(
    vectorstore,
    document: Document,
    document_index: int,
    retry_chunk_size: int = 600,
    retry_depth: int = 0,
) -> tuple[int, list[str]]:
    sanitized = sanitize_text_for_embedding(document.page_content)
    if not sanitized:
        return 0, [f"chunk {document_index}: empty after sanitization"]

    current_document = document
    if sanitized != document.page_content:
        current_document = _clone_document(document, sanitized, retry_depth, 0)

    try:
        vectorstore.add_documents([current_document])
        return 1, []
    except ResponseError as exc:
        if len(sanitized) <= MIN_RETRY_CHUNK_SIZE:
            return 0, [f"chunk {document_index}: {exc}"]

        next_chunk_size = min(retry_chunk_size, max(MIN_RETRY_CHUNK_SIZE, len(sanitized) // 2))
        parts = split_chunk_for_retry(sanitized, chunk_size=next_chunk_size)
        if len(parts) <= 1:
            return 0, [f"chunk {document_index}: {exc}"]

        indexed = 0
        failures: list[str] = []
        for part_index, part in enumerate(parts, start=1):
            if len(part) >= len(sanitized):
                failures.append(f"chunk {document_index}: {exc}")
                continue
            child_document = _clone_document(document, part, retry_depth + 1, part_index)
            child_indexed, child_failures = _add_document_with_retry(
                vectorstore,
                child_document,
                document_index,
                retry_chunk_size=max(MIN_RETRY_CHUNK_SIZE, next_chunk_size // 2),
                retry_depth=retry_depth + 1,
            )
            indexed += child_indexed
            failures.extend(child_failures)
        return indexed, failures


def _add_documents_in_batches(vectorstore, docs: list) -> tuple[int, list[str]]:
    indexed = 0
    failures: list[str] = []
    for start in range(0, len(docs), EMBEDDING_BATCH_SIZE):
        batch = docs[start : start + EMBEDDING_BATCH_SIZE]
        try:
            vectorstore.add_documents(batch)
            indexed += len(batch)
        except ResponseError as exc:
            if len(batch) == 1:
                recovered, recovered_failures = _add_document_with_retry(
                    vectorstore,
                    batch[0],
                    start,
                )
                indexed += recovered
                failures.extend(recovered_failures)
                continue

            for offset, document in enumerate(batch):
                recovered, recovered_failures = _add_document_with_retry(
                    vectorstore,
                    document,
                    start + offset,
                )
                indexed += recovered
                failures.extend(recovered_failures)
    return indexed, failures


@celery_app.task(name="app.tasks.index_pdf_document")
def index_pdf_document(document_id: int) -> dict:
    db = SessionLocal()
    try:
        document = db.query(PdfDocument).filter(PdfDocument.id == document_id).first()
        if document is None:
            return {"status": "missing", "document_id": document_id}

        _set_document_state(document, "indexing")
        db.commit()

        docs = extract_and_chunk_pdf(
            document.stored_path,
            metadata=_build_index_metadata(document),
            enable_ocr=ENABLE_OCR,
            ocr_languages=OCR_LANGS,
            include_tree_documents=False,
        )
        _log_parsed_documents(document, docs)
        vectorstore = get_vectorstore(shared_collection_name())
        indexed_pages, failures = _add_documents_in_batches(vectorstore, docs)
        document.chunks_added = indexed_pages
        if indexed_pages == 0:
            _set_document_state(document, "failed", failures[0] if failures else "No pages were indexed.")
        elif failures:
            _set_document_state(
                document,
                "indexed",
                f"Indexed {indexed_pages}/{len(docs)} pages. Skipped {len(docs) - indexed_pages} page(s) due to Ollama embedding failures.",
            )
        else:
            _set_document_state(document, "indexed")
        db.commit()
        return {
            "status": document.status,
            "document_id": document.id,
            "chunks_added": indexed_pages,
            "skipped_chunks": len(docs) - indexed_pages,
            "error": document.error_message,
        }
    except (EmbeddingInitializationError, ResponseError, ValueError, FileNotFoundError) as exc:
        document = db.query(PdfDocument).filter(PdfDocument.id == document_id).first()
        if document is not None:
            document.chunks_added = 0
            _set_document_state(document, "failed", str(exc))
            db.commit()
        return {"status": "failed", "document_id": document_id, "error": str(exc)}
    except Exception as exc:
        document = db.query(PdfDocument).filter(PdfDocument.id == document_id).first()
        if document is not None:
            document.chunks_added = 0
            _set_document_state(document, "failed", str(exc))
            db.commit()
        raise
    finally:
        db.close()


@celery_app.task(name="app.tasks.reindex_user_documents")
def reindex_user_documents(user_id: int) -> dict:
    db = SessionLocal()
    try:
        documents = (
            db.query(PdfDocument)
            .filter(PdfDocument.user_id == user_id)
            .order_by(PdfDocument.created_at.asc())
            .all()
        )
        if not documents:
            return {"status": "empty", "user_id": user_id, "documents": 0}

        for document in documents:
            document.chunks_added = 0
            _set_document_state(document, "indexing")
        db.commit()

        vectorstore = reset_vectorstore(shared_collection_name())
        total_chunks = 0
        total_indexed = 0

        for document in documents:
            try:
                docs = extract_and_chunk_pdf(
                    document.stored_path,
                    metadata=_build_index_metadata(document),
                    enable_ocr=ENABLE_OCR,
                    ocr_languages=OCR_LANGS,
                    include_tree_documents=False,
                )
                _log_parsed_documents(document, docs)
                indexed_pages, failures = _add_documents_in_batches(vectorstore, docs)
                document.chunks_added = indexed_pages
                total_chunks += indexed_pages
                if indexed_pages == 0:
                    _set_document_state(document, "failed", failures[0] if failures else "No pages were indexed.")
                else:
                    total_indexed += 1
                    if failures:
                        _set_document_state(
                            document,
                            "indexed",
                            f"Indexed {indexed_pages}/{len(docs)} pages. Skipped {len(docs) - indexed_pages} page(s) due to Ollama embedding failures.",
                        )
                    else:
                        _set_document_state(document, "indexed")
            except (EmbeddingInitializationError, ResponseError, ValueError, FileNotFoundError) as exc:
                document.chunks_added = 0
                _set_document_state(document, "failed", str(exc))
            except Exception:
                raise
            finally:
                db.commit()

        return {
            "status": "reindexed",
            "user_id": user_id,
            "documents": total_indexed,
            "chunks": total_chunks,
        }
    finally:
        db.close()
