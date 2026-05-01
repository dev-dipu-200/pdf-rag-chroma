# FastAPI app entrypoint

from fastapi import FastAPI

from app.database import init_db
from app.dependencies import get_runtime_settings
from app.routers import auth, chat, ingest, query

settings = get_runtime_settings()

app = FastAPI(
    title="Custom PDF Chatbot API",
    description="Multi-user PDF chatbot with local Ollama, pgvector, and Celery ingestion.",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

init_db()

app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(ingest.router)
app.include_router(query.router)


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "environment": settings.env,
        "vector_db": "Postgres pgvector",
        "embedding_model": settings.embedding_model,
        "llm_provider": "Ollama",
        "llm_model": settings.llm_model,
        "ollama_url": settings.ollama_url,
        "redis_url": settings.redis_url,
        "enable_ocr": settings.enable_ocr,
        "ocr_languages": settings.ocr_languages,
    }
