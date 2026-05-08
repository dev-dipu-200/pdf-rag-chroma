# ingestion.py

import hashlib
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_vectorstore, shared_collection_name
from app.models import PdfDocument, User
from app.schemas import PdfDocumentResponse
from app.tasks import index_pdf_document, reindex_user_documents

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_PDF_CONTENT_TYPES = {
    "application/pdf",
    "application/x-pdf",
    "application/acrobat",
    "applications/vnd.pdf",
    "text/pdf",
    "text/x-pdf",
    "binary/octet-stream",
    "application/octet-stream",
}


def serialize_document(document: PdfDocument) -> PdfDocumentResponse:
    return PdfDocumentResponse(
        id=document.id,
        original_filename=document.original_filename,
        status=document.status,
        chunks_added=document.chunks_added,
        error_message=document.error_message,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


def user_upload_dir(user_id: int) -> Path:
    path = UPLOAD_DIR / str(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def validate_pdf_upload(file: UploadFile) -> None:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed.")

    if file.content_type and file.content_type.lower() not in ALLOWED_PDF_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid file type for '{file.filename}'.")


def _file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


async def _find_duplicate_document(
    db: AsyncSession,
    user_id: int,
    file_hash: str,
) -> PdfDocument | None:
    result = await db.execute(
        select(PdfDocument).where(
            PdfDocument.user_id == user_id,
            PdfDocument.file_hash == file_hash,
        )
    )
    duplicate = result.scalar_one_or_none()
    if duplicate is not None:
        return duplicate

    legacy_result = await db.execute(
        select(PdfDocument).where(
            PdfDocument.user_id == user_id,
            PdfDocument.file_hash.is_(None),
        )
    )
    legacy_documents = legacy_result.scalars().all()
    updated_legacy_hash = False
    for document in legacy_documents:
        stored_path = Path(document.stored_path)
        if not stored_path.exists():
            continue
        legacy_hash = _file_hash(stored_path.read_bytes())
        document.file_hash = legacy_hash
        updated_legacy_hash = True
        if legacy_hash == file_hash:
            await db.commit()
            return document

    if updated_legacy_hash:
        await db.commit()
    return None


async def save_uploaded_pdf(file: UploadFile, current_user: User, db: AsyncSession) -> PdfDocument:
    validate_pdf_upload(file)
    content = await file.read()
    content_hash = _file_hash(content)

    existing_document = await _find_duplicate_document(db, current_user.id, content_hash)
    if existing_document is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"A matching PDF is already uploaded as '{existing_document.original_filename}'. "
                "Delete the existing file before uploading it again."
            ),
        )

    upload_dir = user_upload_dir(current_user.id)
    suffix = Path(file.filename).suffix.lower() or ".pdf"
    stored_filename = f"{uuid4().hex}{suffix}"
    stored_path = upload_dir / stored_filename

    with stored_path.open("wb") as buffer:
        buffer.write(content)

    document = PdfDocument(
        user_id=current_user.id,
        original_filename=file.filename,
        stored_filename=stored_filename,
        stored_path=str(stored_path),
        file_hash=content_hash,
        status="pending",
        updated_at=datetime.utcnow(),
    )

    db.add(document)
    await db.commit()
    await db.refresh(document)
    return document


async def queue_document_indexing(document: PdfDocument, db: AsyncSession) -> PdfDocument:
    try:
        task = index_pdf_document.delay(document.id)
        document.celery_task_id = task.id
    except Exception as exc:
        document.status = "failed"
        document.error_message = str(exc)
        document.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(document)
    return document


async def upload_and_queue_pdfs(
    files: list[UploadFile],
    current_user: User,
    db: AsyncSession,
) -> list[PdfDocument]:
    if not files:
        raise HTTPException(status_code=400, detail="At least one PDF file is required.")

    documents: list[PdfDocument] = []
    for file in files:
        document = await save_uploaded_pdf(file, current_user, db)
        document = await queue_document_indexing(document, db)
        documents.append(document)
    return documents


async def queue_reindex_for_documents(
    documents: list[PdfDocument],
    db: AsyncSession,
) -> str:
    if not documents:
        raise HTTPException(
            status_code=400,
            detail="No PDFs are available in your account for reindexing.",
        )

    for document in documents:
        document.status = "pending"
        document.error_message = None
        document.chunks_added = 0
        document.updated_at = datetime.utcnow()

    task = reindex_user_documents.delay(documents[0].user_id)
    for document in documents:
        document.celery_task_id = task.id
    await db.commit()
    return task.id


def delete_document_vectors(document_id: int) -> None:
    vectorstore = get_vectorstore(shared_collection_name())
    collection = getattr(vectorstore, "_collection", None)
    if collection is None:
        return
    collection.delete(where={"document_id": str(document_id)})


async def delete_document(
    document: PdfDocument,
    db: AsyncSession,
) -> None:
    try:
        delete_document_vectors(document.id)
    except Exception:
        # Deleting metadata and the local file should still proceed if vector cleanup fails.
        pass

    stored_path = Path(document.stored_path)
    if stored_path.exists():
        stored_path.unlink()

    await db.delete(document)
    await db.commit()
