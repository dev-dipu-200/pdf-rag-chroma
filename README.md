# Custom PDF Chatbot

Backend API code now lives under `backend/`. The frontend remains under `frontend/`.

FastAPI app for a local multi-user PDF chatbot:

- local Ollama for answers with `llama3`
- local Ollama embeddings for ChromaDB with `nomic-embed-text`
- `pypdf` for text extraction
- `pdfplumber` for table extraction
- ChromaDB for vector storage
- Celery + Redis for background PDF ingestion
- OAuth2-style bearer token auth for API access
- optional OCR fallback with Tesseract for scanned PDFs
- PostgreSQL for login, roles, document metadata, and private chat history

## Architecture

- `backend/app/routers/auth.py`: register, login, logout, current user
- `backend/app/routers/ingest.py`: thin upload and reindex endpoints
- `backend/app/routers/query.py`: thin chat/session/query endpoints
- `backend/app/tasks.py`: Celery ingestion and reindex tasks
- `backend/app/models.py`: users, sessions, chat history, PDF metadata
- `backend/app/dependencies.py`: Ollama, ChromaDB, runtime settings
- `backend/app/services/ingestion.py`: upload validation, storage, queue orchestration
- `backend/app/services/query_service.py`: retrieval, session persistence, answer orchestration
- `backend/app/services/pdf.py`: `pypdf` + `pdfplumber` extraction and OCR-aware chunking
- `frontend/`: Nuxt 3 + Tailwind client for auth, chat, and document management

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
POSTGRES_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:5432/pdf_rag
REDIS_URL=redis://127.0.0.1:6379/0
COLLECTION_NAME=pdf_docs_dev
CHROMA_PERSIST_DIRECTORY=./chroma_data
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_BATCH_SIZE=16
JWT_SECRET_KEY=change-me-in-production
ACCESS_TOKEN_EXPIRE_MINUTES=10080
LLM_MODEL=llama3.2:3b
OLLAMA_URL=http://localhost:11434
ENABLE_OCR=false
OCR_LANGS=eng+hin
```

Example `.env.prod`:

```env
APP_ENV=prod
ENV=prod
PYTHON_BASE_IMAGE=python:3.12-slim
APP_PORT=8000
OLLAMA_PORT=11434
POSTGRES_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:5432/pdf_rag
REDIS_URL=redis://redis:6379/0
COLLECTION_NAME=vector_db
CHROMA_PERSIST_DIRECTORY=./chroma_data
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_BATCH_SIZE=16
JWT_SECRET_KEY=change-me-in-production
ACCESS_TOKEN_EXPIRE_MINUTES=10080
LLM_MODEL=llama3.1:8b
OLLAMA_URL=http://ollama-service:11434
ENABLE_OCR=false
OCR_LANGS=eng+hin
```

Example `.env.docker`:

```env
APP_ENV=prod
ENV=prod
PYTHON_BASE_IMAGE=python:3.12-slim
APP_PORT=8000
OLLAMA_PORT=11434
POSTGRES_URL=postgresql+psycopg://postgres:postgres@postgres:5432/pdf_rag
REDIS_URL=redis://redis:6379/0
COLLECTION_NAME=pdf_docs_prod
CHROMA_PERSIST_DIRECTORY=/app/chroma_data
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_BATCH_SIZE=16
JWT_SECRET_KEY=change-me-in-production
ACCESS_TOKEN_EXPIRE_MINUTES=10080
LLM_MODEL=llama3.1:8b
OLLAMA_URL=http://host.docker.internal:11434
ENABLE_OCR=true
OCR_LANGS=eng+hin
```

## Run locally

First make sure Ollama is running and the local models exist:

```bash
ollama pull llama3.2:3b
ollama pull nomic-embed-text
```

Then install dependencies and run:

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload
celery -A celery_worker worker --loglevel=info --concurrency=2
```

You also need PostgreSQL and Redis running.

## Run with Docker Compose

```bash
docker compose up --build -d
```

Services:

- `api`: FastAPI app on `http://localhost:8000`
- `worker`: Celery worker for PDF ingestion
- `postgres`: auth/history/document metadata database
- `redis`: queue broker/backend
- `chroma_data`: persisted ChromaDB collections

Docling and OCR model caching:

- The worker downloads Docling, Hugging Face, and RapidOCR models the first time the heavy parsing path runs.
- Those caches are persisted in Docker volumes so later `docker compose down` and `docker compose up` cycles do not force the same model downloads again.
- The first ingestion after enabling `PDF_PARSER=docling` and `ENABLE_OCR=true` will still be slower than later ingestions.

## Main flow

1. Create the initial admin with `POST /auth/register`, or register a normal user from the UI.
2. The API returns a bearer token.
3. The UI sends `Authorization: Bearer <token>` on protected requests.
4. Upload one PDF with `POST /ingest/pdf` or multiple PDFs with `POST /ingest/pdfs`.
5. Files are stored under `uploads/<user_id>/` and queued to Celery for indexing.
6. English, Hindi, and mixed-language PDFs are supported. Use `OCR_LANGS=eng+hin` for scanned files.
7. Ask questions from the UI or the query API after indexing finishes.
8. Retrieval runs against the shared ChromaDB collection built from uploaded PDFs.
9. Chat sessions and messages are stored per user in PostgreSQL.

## PDF handling

- Normal text PDFs: extracted with `pypdf`
- Tables in text PDFs: extracted with `pdfplumber` and converted into text before embedding
- Image-only or scanned PDFs: optional OCR fallback is available with `ENABLE_OCR=true`
- OCR runs page-by-page through Tesseract on rendered page images, so normal digital PDFs keep their extracted text while scanned pages are OCR'd directly from page images
- Mixed PDFs are supported: if some pages already have text and some pages are image-only, OCR is merged only into the empty pages
- Set `OCR_LANGS=eng` or `OCR_LANGS=eng+hin` depending on your documents

## API endpoints

- `POST /auth/register`
- `POST /auth/register-ui`
- `POST /auth/register-admin`
- `POST /auth/login`
- `POST /auth/logout`
- `GET /auth/me`
- `POST /ingest/pdf`
- `POST /ingest/pdfs`
- `GET /ingest/documents`
- `POST /ingest/reindex`
- `GET /query/sessions`
- `GET /query/sessions/{session_id}/messages`
- `POST /query`
- `POST /query/stream`

## Current behavior

- Each user has a separate chat history.
- The first backend registration through `POST /auth/register` creates the initial `admin`.
- UI registration through `POST /auth/register-ui` always creates a `user` after an admin already exists.
- Additional admin accounts can be created only by an authenticated admin through `POST /auth/register-admin`.
- Retrieval reads from the shared ChromaDB PDF collection.
- PDF ingestion is asynchronous through Celery.
- Multiple PDFs can be uploaded in a single request and are queued independently.
- Reindex drops and rebuilds the shared ChromaDB collection.
- Embeddings are sent to Ollama in batches; if indexing still fails with Ollama `EOF` or `500` errors, lower `EMBEDDING_BATCH_SIZE`.
