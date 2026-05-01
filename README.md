# Custom PDF Chatbot

FastAPI app for a local multi-user PDF chatbot:

- local Ollama for answers with `llama3`
- local Ollama embeddings for pgvector with `nomic-embed-text`
- `pypdf` for text extraction
- `pdfplumber` for table extraction
- PostgreSQL + pgvector for vector storage
- Celery + Redis for background PDF ingestion
- OAuth2-style bearer token auth for API access
- optional OCR fallback with OCRmyPDF + Tesseract for scanned PDFs
- per-user login, private document space, and private chat history

## Architecture

- `app/routers/auth.py`: register, login, logout, current user
- `app/routers/ingest.py`: multi-PDF upload, background queue, document status, reindex
- `app/routers/query.py`: chat sessions, message history, RAG answers
- `app/tasks.py`: Celery ingestion and reindex tasks
- `app/models.py`: users, sessions, chat history, PDF metadata
- `app/dependencies.py`: Ollama, pgvector, runtime settings
- `app/services/pdf.py`: `pypdf` + `pdfplumber` extraction and chunking
- `templates/chat.html`: login + upload + chat UI

## Important model note

`llama3` should be used as the LLM for answering, not as the embedding model.

Use:

```env
LLM_MODEL=llama3.2:3b
EMBEDDING_MODEL=nomic-embed-text
```

If you want larger local answering models, change `LLM_MODEL`. Keep `EMBEDDING_MODEL` as a local embedding-capable Ollama model.

## Environment

Docker now reads only `.env.docker`.
`.env.dev` and `.env.prod` are for non-Docker local runs if you still want profile-based startup outside containers.

Example `.env.dev`:

```env
APP_ENV=dev
ENV=dev
PYTHON_BASE_IMAGE=python:3.12-slim
APP_PORT=8000
OLLAMA_PORT=11434
POSTGRES_URL=postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/pdf_rag
REDIS_URL=redis://127.0.0.1:6379/0
COLLECTION_NAME=pdf_docs_dev
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_BATCH_SIZE=16
JWT_SECRET_KEY=change-me-in-production
ACCESS_TOKEN_EXPIRE_MINUTES=10080
LLM_MODEL=llama3.2:3b
OLLAMA_URL=http://localhost:11434
ENABLE_OCR=false
OCR_LANGS=eng
```

Example `.env.prod`:

```env
APP_ENV=prod
ENV=prod
PYTHON_BASE_IMAGE=python:3.12-slim
APP_PORT=8000
OLLAMA_PORT=11434
POSTGRES_URL=postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/pdf_rag
REDIS_URL=redis://redis:6379/0
COLLECTION_NAME=pdf_docs_prod
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_BATCH_SIZE=16
JWT_SECRET_KEY=change-me-in-production
ACCESS_TOKEN_EXPIRE_MINUTES=10080
LLM_MODEL=llama3.1:8b
OLLAMA_URL=http://ollama-service:11434
ENABLE_OCR=false
OCR_LANGS=eng
```

Example `.env.docker`:

```env
APP_ENV=prod
ENV=prod
PYTHON_BASE_IMAGE=python:3.12-slim
APP_PORT=8000
OLLAMA_PORT=11434
POSTGRES_URL=postgresql+psycopg2://postgres:postgres@postgres:5432/pdf_rag
REDIS_URL=redis://redis:6379/0
COLLECTION_NAME=pdf_docs_prod
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_BATCH_SIZE=16
JWT_SECRET_KEY=change-me-in-production
ACCESS_TOKEN_EXPIRE_MINUTES=10080
LLM_MODEL=llama3.1:8b
OLLAMA_URL=http://host.docker.internal:11434
ENABLE_OCR=false
OCR_LANGS=eng
```

## Run locally

First make sure Ollama is running and the local models exist:

```bash
ollama pull llama3.2:3b
ollama pull nomic-embed-text
```

Then install dependencies and run:

```bash
pip install -r requirements.txt
uvicorn main:app --reload
celery -A celery_worker worker --loglevel=info --concurrency=2
```

You also need PostgreSQL with pgvector and Redis running.

## Run with Docker Compose

```bash
docker compose up --build -d
```

Services:

- `api`: FastAPI app on `http://localhost:8000`
- `worker`: Celery worker for PDF ingestion
- `postgres`: pgvector database
- `redis`: queue broker/backend

## Main flow

1. Register or log in.
2. The API returns a bearer token.
3. The UI sends `Authorization: Bearer <token>` on protected requests.
4. Upload a PDF.
5. PDFs are queued to Celery and indexed in the background.
6. Ask questions from the UI.
7. Retrieval runs only against that user’s own vector collection.
8. Chat sessions and messages are stored per user.

## PDF handling

- Normal text PDFs: extracted with `pypdf`
- Tables in text PDFs: extracted with `pdfplumber` and converted into text before embedding
- Image-only or scanned PDFs: optional OCR fallback is available with `ENABLE_OCR=true`
- OCR runs through `ocrmypdf --skip-text`, so normal digital PDFs keep their text while scanned pages get a text layer
- Set `OCR_LANGS=eng` or `OCR_LANGS=eng+hin` depending on your documents

## API endpoints

- `POST /auth/register`
- `POST /auth/login`
- `POST /auth/logout`
- `GET /auth/me`
- `GET /chat`
- `POST /ingest/pdf`
- `GET /ingest/documents`
- `POST /ingest/reindex`
- `GET /query/sessions`
- `GET /query/sessions/{session_id}/messages`
- `POST /query`
- `POST /query/stream`

## Current behavior

- Each user has a separate chat history.
- Each user queries only their own indexed PDFs.
- PDF ingestion is asynchronous through Celery.
- Reindex resets and rebuilds that user’s vector collection.
- Embeddings are sent to Ollama in batches; if indexing still fails with Ollama `EOF` or `500` errors, lower `EMBEDDING_BATCH_SIZE`.
