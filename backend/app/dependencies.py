import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import chromadb
from chromadb.config import Settings as ChromaSettings
from dotenv import load_dotenv
import httpx
from langchain_chroma import Chroma
from langchain_ollama import OllamaEmbeddings
from langchain_ollama import OllamaLLM
import psycopg
from psycopg import sql

PROFILE_ENV_FILES = {
    "dev": ".env.dev",
    "prod": ".env.prod",
}
PROFILE_ALIASES = {
    "local": "dev",
    "development": "dev",
    "dev": "dev",
    "production": "prod",
    "prod": "prod",
}


def _normalize_profile(value: str | None) -> str:
    if not value:
        return "dev"
    return PROFILE_ALIASES.get(value.strip().lower(), "dev")


def _load_profile_env() -> str:
    explicit_env_file = os.getenv("DOTENV_FILE", "").strip()
    if explicit_env_file:
        load_dotenv(explicit_env_file)
        profile = _normalize_profile(os.getenv("APP_ENV") or os.getenv("ENV"))
        os.environ.setdefault("APP_ENV", profile)
        os.environ.setdefault("ENV", profile)
        return profile

    profile = _normalize_profile(os.getenv("APP_ENV") or os.getenv("ENV"))
    env_file = PROFILE_ENV_FILES[profile]
    load_dotenv(env_file)
    os.environ.setdefault("APP_ENV", profile)
    os.environ.setdefault("ENV", profile)
    return profile


ACTIVE_PROFILE = _load_profile_env()

DEFAULT_ENV = "dev"
DEFAULT_EMBEDDING_MODEL = "qwen3-embedding:8b"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
ENV_LLM_DEFAULTS = {
    "dev": "llama3.2:3b",
    "prod": "llama3.1:8b",
}


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return default


@dataclass(frozen=True)
class RuntimeSettings:
    env: str
    postgres_url: str
    redis_url: str
    collection_name: str
    llm_provider: str
    embedding_provider: str
    chroma_persist_directory: str
    chroma_api_key: str
    chroma_tenant: str
    chroma_database: str
    embedding_model: str
    embedding_batch_size: int
    llm_model: str
    ollama_url: str
    public_api_key: str
    public_api_base_url: str
    public_llm_base_url: str
    public_embedding_base_url: str
    cors_allow_origins: str
    jwt_secret_key: str
    access_token_expire_minutes: int
    enable_ocr: bool
    ocr_languages: str
    pdf_parser: str


def _load_runtime_settings() -> RuntimeSettings:
    env = _normalize_profile(_first_env("APP_ENV", "ENV", default=DEFAULT_ENV))
    llm_default = ENV_LLM_DEFAULTS.get(env, ENV_LLM_DEFAULTS[DEFAULT_ENV])
    ollama_url_default = (
        "http://ollama-service:11434" if env == "prod" else DEFAULT_OLLAMA_URL
    )
    postgres_url_default = "postgresql://postgres:postgres@127.0.0.1:5432/pdf_rag"

    return RuntimeSettings(
        env=env,
        postgres_url=_first_env(
            "POSTGRES_URL", "DATABASE_URL", default=postgres_url_default
        ),
        redis_url=_first_env("REDIS_URL", default="redis://localhost:6379/0"),
        collection_name=_first_env("COLLECTION_NAME", default="pdf_docs"),
        llm_provider=_first_env("LLM_PROVIDER", default="ollama").lower(),
        embedding_provider=_first_env(
            "EMBEDDING_PROVIDER", default=_first_env("LLM_PROVIDER", default="ollama")
        ).lower(),
        chroma_persist_directory=_first_env(
            "CHROMA_PERSIST_DIRECTORY",
            default="./chroma_data",
        ),
        chroma_api_key=_first_env("CHROMA_API_KEY", default=""),
        chroma_tenant=_first_env("CHROMA_TENANT", default=""),
        chroma_database=_first_env("CHROMA_DATABASE", default=""),
        embedding_model=_first_env(
            "EMBEDDING_MODEL",
            default=DEFAULT_EMBEDDING_MODEL,
        ),
        embedding_batch_size=max(
            1,
            int(_first_env("EMBEDDING_BATCH_SIZE", default="16")),
        ),
        llm_model=_first_env("LLM_MODEL", "OLLAMA_MODEL", default=llm_default),
        ollama_url=_first_env(
            "OLLAMA_URL", "OLLAMA_BASE_URL", default=ollama_url_default
        ),
        public_api_key=_first_env("PUBLIC_API_KEY", "OPENAI_API_KEY", default=""),
        public_api_base_url=_first_env("PUBLIC_API_BASE_URL", default=""),
        public_llm_base_url=_first_env(
            "PUBLIC_LLM_BASE_URL",
            "OPENAI_BASE_URL",
            default=_first_env("PUBLIC_API_BASE_URL", default=""),
        ),
        public_embedding_base_url=_first_env(
            "PUBLIC_EMBEDDING_BASE_URL",
            default=_first_env("PUBLIC_API_BASE_URL", default=""),
        ),
        cors_allow_origins=_first_env(
            "CORS_ALLOW_ORIGINS",
            default="http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001",
        ),
        jwt_secret_key=_first_env("JWT_SECRET_KEY", default="change-me-in-production"),
        access_token_expire_minutes=int(
            _first_env("ACCESS_TOKEN_EXPIRE_MINUTES", default="10080")
        ),
        enable_ocr=_first_env("ENABLE_OCR", default="false").lower()
        in {"1", "true", "yes", "on"},
        ocr_languages=_first_env("OCR_LANGS", default="eng"),
        pdf_parser=_first_env("PDF_PARSER", default="hybrid").lower(),
    )


SETTINGS = _load_runtime_settings()

POSTGRES_URL = SETTINGS.postgres_url


# Helper for SQLAlchemy URLs
def get_async_postgres_url() -> str:
    url = POSTGRES_URL
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if "asyncpg" not in url and "psycopg" not in url:
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


def get_sync_postgres_url() -> str:
    url = POSTGRES_URL
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if "psycopg" not in url and "asyncpg" not in url:
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    return url


REDIS_URL = SETTINGS.redis_url
COLLECTION_NAME = SETTINGS.collection_name
LLM_PROVIDER = SETTINGS.llm_provider
EMBEDDING_PROVIDER = SETTINGS.embedding_provider
CHROMA_PERSIST_DIRECTORY = SETTINGS.chroma_persist_directory
CHROMA_API_KEY = SETTINGS.chroma_api_key
CHROMA_TENANT = SETTINGS.chroma_tenant
CHROMA_DATABASE = SETTINGS.chroma_database
EMBEDDING_MODEL = SETTINGS.embedding_model
EMBEDDING_BATCH_SIZE = SETTINGS.embedding_batch_size
OLLAMA_MODEL = SETTINGS.llm_model
OLLAMA_BASE_URL = SETTINGS.ollama_url
PUBLIC_API_KEY = SETTINGS.public_api_key
PUBLIC_API_BASE_URL = SETTINGS.public_api_base_url
PUBLIC_LLM_BASE_URL = SETTINGS.public_llm_base_url
PUBLIC_EMBEDDING_BASE_URL = SETTINGS.public_embedding_base_url
JWT_SECRET_KEY = SETTINGS.jwt_secret_key
ACCESS_TOKEN_EXPIRE_MINUTES = SETTINGS.access_token_expire_minutes
ENABLE_OCR = SETTINGS.enable_ocr
OCR_LANGS = SETTINGS.ocr_languages
PDF_PARSER = SETTINGS.pdf_parser

# Singleton-like clients
_embeddings = None
_vectorstores = {}
_chroma_client = None


class EmbeddingInitializationError(RuntimeError):
    pass


def _normalize_public_base_url(base_url: str, endpoint: str) -> str:
    cleaned = base_url.rstrip("/")
    if not cleaned:
        raise EmbeddingInitializationError(
            f"Missing public API base URL for {endpoint}. Set PUBLIC_API_BASE_URL or provider-specific base URLs."
        )
    if cleaned.endswith(endpoint):
        return cleaned
    return f"{cleaned}/{endpoint}"


class PublicAPIEmbeddings:
    def __init__(self, model: str, base_url: str, api_key: str = "") -> None:
        self.model = model
        self.base_url = _normalize_public_base_url(base_url, "embeddings")
        self.api_key = api_key
        self.timeout = httpx.Timeout(120.0, connect=30.0)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                self.base_url,
                headers=self._headers(),
                json={"model": self.model, "input": texts},
            )
            response.raise_for_status()
            payload = response.json()
        data = payload.get("data") or []
        return [item["embedding"] for item in data]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


def _split_postgres_url(database_url: str) -> tuple[str, str]:
    parsed = urlsplit(database_url)
    database_name = parsed.path.lstrip("/")
    scheme = parsed.scheme.replace("+psycopg2", "").replace("+psycopg", "")
    admin_url = urlunsplit(parsed._replace(scheme=scheme, path="/postgres"))
    return admin_url, database_name


async def _ensure_postgres_database_exists() -> None:
    admin_url, database_name = _split_postgres_url(POSTGRES_URL)
    if not database_name:
        raise RuntimeError("POSTGRES_URL must include a database name.")

    # Convert asyncpg/other async schemes back to psycopg for the admin connection if needed
    # but psycopg 3 is async capable. We just need a simple check.
    # Note: admin_url from _split_postgres_url already has scheme normalized (no +asyncpg)
    async with await psycopg.AsyncConnection.connect(
        admin_url, autocommit=True
    ) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (database_name,),
            )
            if await cur.fetchone():
                return
            await cur.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
            )


def _normalize_collection_name(name: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-_")
    return normalized or "default"


RESOLVED_COLLECTION_NAME = _normalize_collection_name(f"{COLLECTION_NAME}")


def user_collection_name(user_id: int) -> str:
    return _normalize_collection_name(f"{COLLECTION_NAME}__user_{user_id}")


def shared_collection_name() -> str:
    return _normalize_collection_name(f"{COLLECTION_NAME}__shared")


def _get_chroma_client():
    global _chroma_client
    if _chroma_client is None:
        if CHROMA_API_KEY and CHROMA_TENANT and CHROMA_DATABASE:
            cloud_client_factory = getattr(chromadb, "CloudClient", None)
            if cloud_client_factory is None:
                raise RuntimeError(
                    "Chroma Cloud configuration is set, but this chromadb package does not provide CloudClient."
                )
            _chroma_client = cloud_client_factory(
                api_key=CHROMA_API_KEY,
                tenant=CHROMA_TENANT,
                database=CHROMA_DATABASE,
            )
        else:
            persist_directory = Path(CHROMA_PERSIST_DIRECTORY)
            persist_directory.mkdir(parents=True, exist_ok=True)
            _chroma_client = chromadb.PersistentClient(
                path=str(persist_directory),
                settings=ChromaSettings(),
            )
    return _chroma_client


def get_embeddings():
    global _embeddings
    if _embeddings is None:
        try:
            if EMBEDDING_PROVIDER == "public":
                _embeddings = PublicAPIEmbeddings(
                    model=EMBEDDING_MODEL,
                    base_url=PUBLIC_EMBEDDING_BASE_URL or PUBLIC_API_BASE_URL,
                    api_key=PUBLIC_API_KEY,
                )
            else:
                _embeddings = OllamaEmbeddings(
                    model=EMBEDDING_MODEL,
                    base_url=OLLAMA_BASE_URL,
                )
        except Exception as exc:
            raise EmbeddingInitializationError(
                "Failed to initialize embeddings. "
                "Check the configured embedding provider, base URL, model name, and API key."
            ) from exc
    return _embeddings


def get_vectorstore(collection_name: str | None = None) -> Chroma:
    resolved_collection = collection_name or RESOLVED_COLLECTION_NAME
    vectorstore = _vectorstores.get(resolved_collection)
    if vectorstore is None:
        vectorstore = Chroma(
            client=_get_chroma_client(),
            collection_name=resolved_collection,
            embedding_function=get_embeddings(),
        )
        _vectorstores[resolved_collection] = vectorstore
    return vectorstore


def invalidate_vectorstore(collection_name: str | None = None) -> None:
    resolved_collection = collection_name or RESOLVED_COLLECTION_NAME
    _vectorstores.pop(resolved_collection, None)


def reset_vectorstore(collection_name: str | None = None) -> Chroma:
    resolved_collection = collection_name or RESOLVED_COLLECTION_NAME
    try:
        _get_chroma_client().delete_collection(name=resolved_collection)
    except Exception:
        pass
    invalidate_vectorstore(resolved_collection)
    return get_vectorstore(resolved_collection)


def get_llm():
    if LLM_PROVIDER == "public":
        return {
            "provider": "public",
            "model": OLLAMA_MODEL,
            "base_url": PUBLIC_LLM_BASE_URL or PUBLIC_API_BASE_URL,
            "api_key": PUBLIC_API_KEY,
            "temperature": 0.15,
        }
    return OllamaLLM(
        model=OLLAMA_MODEL,
        base_url=OLLAMA_BASE_URL,
        temperature=0.15,
    )


def get_runtime_settings() -> RuntimeSettings:
    return SETTINGS
