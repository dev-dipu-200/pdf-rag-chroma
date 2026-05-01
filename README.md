# PDF RAG Postgres

A FastAPI-based PDF RAG app using PostgreSQL with pgvector for retrieval and Ollama for generation.
The default setup supports Hindi and English PDF text retrieval and answers questions in the same language as the user query.
The app also supports adaptive `dev`/`prod` model selection through environment-based configuration.

## Project structure

- `main.py`: FastAPI app entrypoint
- `app/routers/`: ingest and query endpoints
- `app/services/`: PDF extraction and RAG chain logic
- `app/dependencies.py`: shared pgvector, embedding, splitter, and Ollama client setup
- `docker/api-entrypoint.sh`: API startup script used by Docker
- `docker-compose.yml`: Docker services for `dev` and `prod`
- `.env.dev`: local development profile
- `.env.prod`: production GPU profile

## Adaptive model strategy

- `APP_ENV=dev` defaults to `LLM_MODEL=llama3.2:3b` for CPU-friendly local runs
- `APP_ENV=prod` defaults to `LLM_MODEL=llama3.1:8b` for GPU-backed deployments
- `EMBEDDING_MODEL` defaults to `nomic-embed-text`
- `POSTGRES_URL` points the app to the pgvector database
- Explicit `LLM_MODEL` or `OLLAMA_MODEL` overrides the profile default
- Explicit `OLLAMA_URL` or `OLLAMA_BASE_URL` overrides the profile default

Supported env names:

```env
APP_ENV=dev|prod
ENV=dev|prod
POSTGRES_URL=postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/pdf_rag
EMBEDDING_MODEL=nomic-embed-text
LLM_MODEL=llama3.2:3b
OLLAMA_URL=http://localhost:11434
```

Backward-compatible aliases:

```env
OLLAMA_MODEL=llama3.2:3b
OLLAMA_BASE_URL=http://localhost:11434
```

## Run with Docker

```bash
sudo docker compose down
sudo docker compose up --build -d dev
```

That starts:

- `dev`: FastAPI API on `http://localhost:8000`
- the selected service runs on host networking and connects to Ollama using the chosen env file

For production profile:

```bash
sudo docker compose down
sudo docker compose up --build -d prod
```

## Docker notes

- The API uses `network_mode: host` in the provided local Docker setup
- This reuses the host's Ollama service
- The API starts through `docker/api-entrypoint.sh`
- Container logs are rotated with `max-size` between `10m` and `20m`, and `max-file: 5`
- PostgreSQL data and uploads use named Docker volumes
- `dev` loads `.env.dev`
- `prod` loads `.env.prod`
- Docker also starts a `postgres` service using the `pgvector/pgvector:pg16` image

## Environment variables

Example local `.env.dev`:

```env
APP_ENV=dev
ENV=dev
POSTGRES_URL=postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/pdf_rag
COLLECTION_NAME=pdf_docs_dev
EMBEDDING_MODEL=nomic-embed-text
LLM_MODEL=llama3.2:3b
OLLAMA_URL=http://localhost:11434
```

Example production `.env.prod`:

```env
PYTHON_BASE_IMAGE=python:3.12-slim
APP_PORT=8000
OLLAMA_PORT=11434
APP_ENV=prod
ENV=prod
POSTGRES_URL=postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/pdf_rag
COLLECTION_NAME=pdf_docs_prod
EMBEDDING_MODEL=nomic-embed-text
LLM_MODEL=llama3.1:8b
OLLAMA_URL=http://ollama-service:11434
```

If `LLM_MODEL` is omitted, the app resolves it from `APP_ENV` or `ENV`.

## Hindi + English support

- Retrieval is configurable through `EMBEDDING_MODEL`. The current default is `nomic-embed-text`.
- Answers are generated in the same language as the question.
- PDFs must contain selectable text. If a PDF is scanned as images only, OCR is required before ingesting it.
- After changing the embedding model, re-ingest your PDFs so the vector store is rebuilt with the new embeddings.

## API endpoints

- `GET /health`
- `GET /chat`
- `POST /ingest/pdf`
- `POST /ingest/reindex`
- `POST /query`
- `POST /query/stream`

`GET /health` now reports the active environment, embedding model, LLM model, and Ollama URL.
`GET /chat` serves a Bootstrap chatbot UI.
`POST /query/stream` returns newline-delimited JSON chunks for live streaming in the browser.

## Reindex workflow

- Uploaded PDFs are now kept in `/app/uploads` so they can be reindexed later.
- When you change the embedding model or want to rebuild the Hindi/English index, call `POST /ingest/reindex`.
- `POST /ingest/reindex` clears the current collection for the active embedding model and rebuilds it from all stored PDFs.
