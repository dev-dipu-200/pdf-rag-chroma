import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import psycopg2
from dotenv import load_dotenv
from langchain_ollama import OllamaEmbeddings
from langchain_ollama import OllamaLLM
from langchain_community.vectorstores import PGVector
from langchain_text_splitters import RecursiveCharacterTextSplitter
from psycopg2 import sql

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
    profile = _normalize_profile(os.getenv("APP_ENV") or os.getenv("ENV"))
    env_file = PROFILE_ENV_FILES[profile]
    load_dotenv(env_file)
    os.environ.setdefault("APP_ENV", profile)
    os.environ.setdefault("ENV", profile)
    return profile


ACTIVE_PROFILE = _load_profile_env()

DEFAULT_ENV = "dev"
DEFAULT_EMBEDDING_MODEL = "nomic-embed-text"
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
    collection_name: str
    embedding_model: str
    llm_model: str
    ollama_url: str


def _load_runtime_settings() -> RuntimeSettings:
    env = _normalize_profile(_first_env("APP_ENV", "ENV", default=DEFAULT_ENV))
    llm_default = ENV_LLM_DEFAULTS.get(env, ENV_LLM_DEFAULTS[DEFAULT_ENV])
    ollama_url_default = (
        "http://ollama-service:11434" if env == "prod" else DEFAULT_OLLAMA_URL
    )
    postgres_url_default = (
        "postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/pdf_rag"
    )

    return RuntimeSettings(
        env=env,
        postgres_url=_first_env("POSTGRES_URL", "DATABASE_URL", default=postgres_url_default),
        collection_name=_first_env("COLLECTION_NAME", default="pdf_docs"),
        embedding_model=_first_env(
            "EMBEDDING_MODEL",
            default=DEFAULT_EMBEDDING_MODEL,
        ),
        llm_model=_first_env("LLM_MODEL", "OLLAMA_MODEL", default=llm_default),
        ollama_url=_first_env("OLLAMA_URL", "OLLAMA_BASE_URL", default=ollama_url_default),
    )


SETTINGS = _load_runtime_settings()

POSTGRES_URL = SETTINGS.postgres_url
COLLECTION_NAME = SETTINGS.collection_name
EMBEDDING_MODEL = SETTINGS.embedding_model
OLLAMA_MODEL = SETTINGS.llm_model
OLLAMA_BASE_URL = SETTINGS.ollama_url

# Singleton-like clients
_embeddings = None
_vectorstore = None
_text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=900,
    chunk_overlap=150,
    separators=["\n\n", "\n", "।", ".", "?", "!", " ", ""],
)


class EmbeddingInitializationError(RuntimeError):
    pass


def _split_postgres_url(database_url: str) -> tuple[str, str]:
    parsed = urlsplit(database_url)
    database_name = parsed.path.lstrip("/")
    scheme = parsed.scheme.replace("+psycopg2", "")
    admin_url = urlunsplit(parsed._replace(scheme=scheme, path="/postgres"))
    return admin_url, database_name


def _ensure_postgres_database_exists() -> None:
    admin_url, database_name = _split_postgres_url(POSTGRES_URL)
    if not database_name:
        raise RuntimeError("POSTGRES_URL must include a database name.")

    conn = psycopg2.connect(admin_url)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (database_name,),
            )
            if cur.fetchone():
                return
            cur.execute(
                sql.SQL("CREATE DATABASE {}").format(
                    sql.Identifier(database_name)
                )
            )
    finally:
        conn.close()


def _normalize_collection_name(name: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-_")
    return normalized or "default"


RESOLVED_COLLECTION_NAME = _normalize_collection_name(
    f"{COLLECTION_NAME}__{EMBEDDING_MODEL}"
)


def get_embeddings():
    global _embeddings
    if _embeddings is None:
        try:
            _embeddings = OllamaEmbeddings(
                model=EMBEDDING_MODEL,
                base_url=OLLAMA_BASE_URL,
            )
        except Exception as exc:
            raise EmbeddingInitializationError(
                "Failed to initialize Ollama embeddings. "
                "Make sure the embedding model is pulled in Ollama and the configured "
                "OLLAMA_URL is reachable from the API container."
            ) from exc
    return _embeddings


def get_vectorstore() -> PGVector:
    global _vectorstore
    if _vectorstore is None:
        _ensure_postgres_database_exists()
        _vectorstore = PGVector(
            connection_string=POSTGRES_URL,
            collection_name=RESOLVED_COLLECTION_NAME,
            embedding_function=get_embeddings(),
            use_jsonb=True,
        )
    return _vectorstore


def reset_vectorstore() -> PGVector:
    global _vectorstore

    _ensure_postgres_database_exists()
    store = _vectorstore or PGVector(
        connection_string=POSTGRES_URL,
        collection_name=RESOLVED_COLLECTION_NAME,
        embedding_function=get_embeddings(),
        use_jsonb=True,
    )

    try:
        store.delete_collection()
    except Exception:
        pass

    _vectorstore = None
    return get_vectorstore()


def get_llm():
    return OllamaLLM(
        model=OLLAMA_MODEL,
        base_url=OLLAMA_BASE_URL,
        temperature=0.15,
    )


def get_text_splitter():
    return _text_splitter


def get_runtime_settings() -> RuntimeSettings:
    return SETTINGS
