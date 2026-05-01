from datetime import datetime

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
    user_collection_name,
)
from app.models import PdfDocument
from app.services.pdf import extract_and_chunk_pdf


def _set_document_state(document: PdfDocument, status: str, error_message: str | None = None) -> None:
    document.status = status
    document.error_message = error_message
    document.updated_at = datetime.utcnow()


def _add_documents_in_batches(vectorstore, docs: list) -> tuple[int, list[str]]:
    indexed = 0
    failures: list[str] = []
    for start in range(0, len(docs), EMBEDDING_BATCH_SIZE):
        batch = docs[start : start + EMBEDDING_BATCH_SIZE]
        try:
            vectorstore.add_documents(batch)
            indexed += len(batch)
        except ResponseError as exc:
            batch_start = start
            batch_end = start + len(batch) - 1
            failures.append(f"chunks {batch_start}-{batch_end}: {exc}")
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
            metadata={
                "source": document.original_filename,
                "document_id": str(document.id),
                "user_id": str(document.user_id),
            },
            enable_ocr=ENABLE_OCR,
            ocr_languages=OCR_LANGS,
        )
        vectorstore = get_vectorstore(user_collection_name(document.user_id))
        indexed_chunks, failures = _add_documents_in_batches(vectorstore, docs)
        document.chunks_added = indexed_chunks
        if indexed_chunks == 0:
            _set_document_state(document, "failed", failures[0] if failures else "No chunks were indexed.")
        elif failures:
            _set_document_state(
                document,
                "indexed",
                f"Indexed {indexed_chunks}/{len(docs)} chunks. Skipped {len(docs) - indexed_chunks} chunk(s) due to Ollama embedding failures.",
            )
        else:
            _set_document_state(document, "indexed")
        db.commit()
        return {
            "status": document.status,
            "document_id": document.id,
            "chunks_added": indexed_chunks,
            "skipped_chunks": len(docs) - indexed_chunks,
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

        vectorstore = reset_vectorstore(user_collection_name(user_id))
        total_chunks = 0
        total_indexed = 0

        for document in documents:
            try:
                docs = extract_and_chunk_pdf(
                    document.stored_path,
                    metadata={
                        "source": document.original_filename,
                        "document_id": str(document.id),
                        "user_id": str(document.user_id),
                    },
                    enable_ocr=ENABLE_OCR,
                    ocr_languages=OCR_LANGS,
                )
                indexed_chunks, failures = _add_documents_in_batches(vectorstore, docs)
                document.chunks_added = indexed_chunks
                total_chunks += indexed_chunks
                if indexed_chunks == 0:
                    _set_document_state(document, "failed", failures[0] if failures else "No chunks were indexed.")
                else:
                    total_indexed += 1
                    if failures:
                        _set_document_state(
                            document,
                            "indexed",
                            f"Indexed {indexed_chunks}/{len(docs)} chunks. Skipped {len(docs) - indexed_chunks} chunk(s) due to Ollama embedding failures.",
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
