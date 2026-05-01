# Upload & index PDF endpoints
import os
import shutil
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Depends, HTTPException

from app.dependencies import (
    EmbeddingInitializationError,
    get_text_splitter,
    reset_vectorstore,
    get_vectorstore,
)
from app.schemas import IngestResponse, ReindexResponse
from app.services.pdf import extract_and_chunk_pdf

router = APIRouter(prefix="/ingest", tags=["ingest"])

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


def _pdf_paths() -> list[Path]:
    return sorted(
        path for path in Path(UPLOAD_DIR).glob("*.pdf")
        if path.is_file()
    )

@router.post("/pdf", response_model=IngestResponse)
async def upload_and_index_pdf(
    file: UploadFile = File(...),
    text_splitter = Depends(get_text_splitter)
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files allowed")
    
    if not file.content_type == "application/pdf":
        raise HTTPException(400, "Invalid file type")

    file_path = os.path.join(UPLOAD_DIR, file.filename)
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        docs = extract_and_chunk_pdf(file_path, text_splitter)
        vectorstore = get_vectorstore()
        vectorstore.add_documents(docs)
        return IngestResponse(
            filename=file.filename,
            chunks_added=len(docs),
            status="indexed"
        )
    except EmbeddingInitializationError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                f"{exc} Current model: '{os.getenv('EMBEDDING_MODEL', 'nomic-embed-text')}'. "
                "Pull the embedding model in Ollama and verify OLLAMA_URL."
            ),
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/reindex", response_model=ReindexResponse)
def reindex_uploaded_pdfs(
    text_splitter = Depends(get_text_splitter)
):
    pdf_paths = _pdf_paths()
    if not pdf_paths:
        raise HTTPException(
            status_code=400,
            detail="No PDFs are available in the uploads directory for reindexing.",
        )

    try:
        vectorstore = reset_vectorstore()
        total_chunks = 0

        for pdf_path in pdf_paths:
            docs = extract_and_chunk_pdf(str(pdf_path), text_splitter)
            vectorstore.add_documents(docs)
            total_chunks += len(docs)

        return ReindexResponse(
            files_indexed=len(pdf_paths),
            chunks_added=total_chunks,
            status="reindexed",
        )
    except EmbeddingInitializationError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                f"{exc} Current model: '{os.getenv('EMBEDDING_MODEL', 'nomic-embed-text')}'. "
                "Pull the embedding model in Ollama and verify OLLAMA_URL."
            ),
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
