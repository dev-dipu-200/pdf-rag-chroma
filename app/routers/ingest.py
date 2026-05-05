import shutil
from datetime import datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.auth import get_current_user, require_admin
from app.database import get_db
from app.models import PdfDocument, User
from app.schemas import IngestResponse, PdfDocumentResponse, ReindexResponse
from app.tasks import index_pdf_document, reindex_user_documents

router = APIRouter(prefix="/ingest", tags=["ingest"])

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


def _serialize_document(document: PdfDocument) -> PdfDocumentResponse:
    return PdfDocumentResponse(
        id=document.id,
        original_filename=document.original_filename,
        status=document.status,
        chunks_added=document.chunks_added,
        error_message=document.error_message,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


def _user_upload_dir(user_id: int) -> Path:
    path = UPLOAD_DIR / str(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


@router.post(
    "/pdf",
    response_model=IngestResponse,
    summary="Upload a PDF file",
)
async def upload_pdf(
    file: Annotated[
        UploadFile,
        File(
            ...,
            description="Select a PDF file.",
            media_type="application/pdf",
        ),
    ],
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    upload_dir = _user_upload_dir(current_user.id)

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed.")

    if file.content_type and file.content_type.lower() not in ALLOWED_PDF_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Invalid file type.")

    suffix = Path(file.filename).suffix.lower() or ".pdf"
    stored_filename = f"{uuid4().hex}{suffix}"
    stored_path = upload_dir / stored_filename
    
    # Save file asynchronously
    content = await file.read()
    with stored_path.open("wb") as buffer:
        buffer.write(content)

    document = PdfDocument(
        user_id=current_user.id,
        original_filename=file.filename,
        stored_filename=stored_filename,
        stored_path=str(stored_path),
        status="pending",
        updated_at=datetime.utcnow(),
    )

    db.add(document)
    await db.commit()
    await db.refresh(document)
    
    try:
        task = index_pdf_document.delay(document.id)
        document.celery_task_id = task.id
    except Exception as exc:
        document.status = "failed"
        document.error_message = str(exc)
        document.updated_at = datetime.utcnow()

    await db.commit()

    return IngestResponse(
        document=_serialize_document(document),
        status="queued",
    )


@router.get("/documents", response_model=list[PdfDocumentResponse])
async def list_documents(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PdfDocument)
        .filter(PdfDocument.user_id == current_user.id)
        .order_by(PdfDocument.created_at.desc())
    )
    documents = result.scalars().all()
    return [_serialize_document(document) for document in documents]


@router.post("/reindex", response_model=ReindexResponse)
async def reindex_uploaded_pdfs(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PdfDocument)
        .filter(PdfDocument.user_id == current_user.id)
        .order_by(PdfDocument.created_at.asc())
    )
    documents = result.scalars().all()
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

    task = reindex_user_documents.delay(current_user.id)
    for document in documents:
        document.celery_task_id = task.id
    await db.commit()

    return ReindexResponse(
        queued_documents=len(documents),
        status="queued",
    )
