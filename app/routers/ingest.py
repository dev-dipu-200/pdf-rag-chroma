import math
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.database import get_db
from app.models import PdfDocument, User
from app.schemas import IngestResponse, MultiIngestResponse, PaginatedPdfDocumentsResponse, ReindexResponse, StatusResponse
from app.services.ingestion import delete_document, queue_reindex_for_documents, serialize_document, upload_and_queue_pdfs

router = APIRouter(prefix="/ingest", tags=["ingest"])


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
    document = (await upload_and_queue_pdfs([file], current_user, db))[0]
    return IngestResponse(
        document=serialize_document(document),
        status="queued",
    )


@router.post(
    "/pdfs",
    response_model=MultiIngestResponse,
    summary="Upload multiple PDF files",
)
async def upload_pdfs(
    files: Annotated[
        list[UploadFile],
        File(
            ...,
            description="Select one or more PDF files.",
            media_type="application/pdf",
        ),
    ],
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    documents = await upload_and_queue_pdfs(files, current_user, db)
    return MultiIngestResponse(
        documents=[serialize_document(document) for document in documents],
        queued_documents=len(documents),
        status="queued",
    )


@router.get("/documents", response_model=PaginatedPdfDocumentsResponse)
async def list_documents(
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    count_stmt = select(func.count()).select_from(PdfDocument).filter(PdfDocument.user_id == current_user.id)
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    skip = (page - 1) * size
    stmt = (
        select(PdfDocument)
        .filter(PdfDocument.user_id == current_user.id)
        .order_by(PdfDocument.created_at.desc())
        .offset(skip)
        .limit(size)
    )
    result = await db.execute(stmt)
    documents = result.scalars().all()
    pages = math.ceil(total / size) if total > 0 else 1

    return PaginatedPdfDocumentsResponse(
        items=[serialize_document(doc) for doc in documents],
        total=total,
        page=page,
        pages=pages,
        size=size,
    )


@router.delete("/documents/{document_id}", response_model=StatusResponse)
async def delete_uploaded_document(
    document_id: int,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PdfDocument).filter(
            PdfDocument.id == document_id,
            PdfDocument.user_id == current_user.id,
        )
    )
    document = result.scalar_one_or_none()
    if document is None:
        return StatusResponse(status="missing")

    await delete_document(document, db)
    return StatusResponse(status="deleted")


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
    await queue_reindex_for_documents(documents, db)

    return ReindexResponse(
        queued_documents=len(documents),
        status="queued",
    )
