import os
import re
from hashlib import sha1
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv
from langchain_ollama import OllamaEmbeddings
from langchain_ollama import OllamaLLM
from langchain_postgres import PGEngine, PGVectorStore
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
DEFAULT_EMBEDDING_MODEL = "nomic-embed-text"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_EMBEDDING_VECTOR_SIZE = 768
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
    embedding_model: str
    embedding_batch_size: int
    embedding_vector_size: int
    llm_model: str
    ollama_url: str
    jwt_secret_key: str
    access_token_expire_minutes: int
    enable_ocr: bool
    ocr_languages: str


def _load_runtime_settings() -> RuntimeSettings:
    env = _normalize_profile(_first_env("APP_ENV", "ENV", default=DEFAULT_ENV))
    llm_default = ENV_LLM_DEFAULTS.get(env, ENV_LLM_DEFAULTS[DEFAULT_ENV])
    ollama_url_default = (
        "http://ollama-service:11434" if env == "prod" else DEFAULT_OLLAMA_URL
    )
    postgres_url_default = (
        "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/pdf_rag"
    )

    return RuntimeSettings(
        env=env,
        postgres_url=_first_env("POSTGRES_URL", "DATABASE_URL", default=postgres_url_default),
        redis_url=_first_env("REDIS_URL", default="redis://localhost:6379/0"),
        collection_name=_first_env("COLLECTION_NAME", default="pdf_docs"),
        embedding_model=_first_env(
            "EMBEDDING_MODEL",
            default=DEFAULT_EMBEDDING_MODEL,
        ),
        embedding_batch_size=max(
            1,
            int(_first_env("EMBEDDING_BATCH_SIZE", default="16")),
        ),
        embedding_vector_size=max(
            1,
            int(_first_env("EMBEDDING_VECTOR_SIZE", default=str(DEFAULT_EMBEDDING_VECTOR_SIZE))),
        ),
        llm_model=_first_env("LLM_MODEL", "OLLAMA_MODEL", default=llm_default),
        ollama_url=_first_env("OLLAMA_URL", "OLLAMA_BASE_URL", default=ollama_url_default),
        jwt_secret_key=_first_env("JWT_SECRET_KEY", default="change-me-in-production"),
        access_token_expire_minutes=int(_first_env("ACCESS_TOKEN_EXPIRE_MINUTES", default="10080")),
        enable_ocr=_first_env("ENABLE_OCR", default="false").lower() in {"1", "true", "yes", "on"},
        ocr_languages=_first_env("OCR_LANGS", default="eng"),
    )


SETTINGS = _load_runtime_settings()

POSTGRES_URL = SETTINGS.postgres_url
REDIS_URL = SETTINGS.redis_url
COLLECTION_NAME = SETTINGS.collection_name
EMBEDDING_MODEL = SETTINGS.embedding_model
EMBEDDING_BATCH_SIZE = SETTINGS.embedding_batch_size
EMBEDDING_VECTOR_SIZE = SETTINGS.embedding_vector_size
OLLAMA_MODEL = SETTINGS.llm_model
OLLAMA_BASE_URL = SETTINGS.ollama_url
JWT_SECRET_KEY = SETTINGS.jwt_secret_key
ACCESS_TOKEN_EXPIRE_MINUTES = SETTINGS.access_token_expire_minutes
ENABLE_OCR = SETTINGS.enable_ocr
OCR_LANGS = SETTINGS.ocr_languages

# Singleton-like clients
_embeddings = None
_vectorstores = {}
_pgvector_engine = None
_initialized_vector_tables = set()


class EmbeddingInitializationError(RuntimeError):
    pass


def _split_postgres_url(database_url: str) -> tuple[str, str]:
    parsed = urlsplit(database_url)
    database_name = parsed.path.lstrip("/")
    scheme = parsed.scheme.replace("+psycopg2", "").replace("+psycopg", "")
    admin_url = urlunsplit(parsed._replace(scheme=scheme, path="/postgres"))
    return admin_url, database_name


def _ensure_postgres_database_exists() -> None:
    admin_url, database_name = _split_postgres_url(POSTGRES_URL)
    if not database_name:
        raise RuntimeError("POSTGRES_URL must include a database name.")

    with psycopg.connect(admin_url, autocommit=True) as conn:
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


def _normalize_collection_name(name: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-_")
    return normalized or "default"


RESOLVED_COLLECTION_NAME = _normalize_collection_name(
    f"{COLLECTION_NAME}__{EMBEDDING_MODEL}"
)


def user_collection_name(user_id: int) -> str:
    return _normalize_collection_name(
        f"{COLLECTION_NAME}__user_{user_id}__{EMBEDDING_MODEL}"
    )


def shared_collection_name() -> str:
    return _normalize_collection_name(
        f"{COLLECTION_NAME}__shared__{EMBEDDING_MODEL}"
    )


def _vector_table_name(name: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_]+", "_", name).strip("_").lower() or "default"
    if not re.match(r"^[a-z_]", normalized):
        normalized = f"v_{normalized}"
    if len(normalized) <= 54:
        return normalized
    suffix = sha1(normalized.encode("utf-8")).hexdigest()[:8]
    return f"{normalized[:45]}_{suffix}"


def _pgvector_connection_url() -> str:
    parsed = urlsplit(POSTGRES_URL)
    scheme = parsed.scheme
    if "+psycopg2" in scheme:
        scheme = scheme.replace("+psycopg2", "+psycopg")
    elif "+" not in scheme:
        scheme = f"{scheme}+psycopg"
    return urlunsplit(parsed._replace(scheme=scheme))


def _database_connection_url() -> str:
    parsed = urlsplit(POSTGRES_URL)
    scheme = parsed.scheme.replace("+psycopg2", "").replace("+psycopg", "")
    return urlunsplit(parsed._replace(scheme=scheme))


def _get_pgvector_engine() -> PGEngine:
    global _pgvector_engine
    if _pgvector_engine is None:
        _ensure_postgres_database_exists()
        _pgvector_engine = PGEngine.from_connection_string(url=_pgvector_connection_url())
    return _pgvector_engine


def _vector_table_exists(table_name: str) -> bool:
    with psycopg.connect(_database_connection_url()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)", (f"public.{table_name}",))
            return cur.fetchone()[0] is not None


def _ensure_vector_table(table_name: str) -> None:
    if table_name in _initialized_vector_tables:
        return
    if not _vector_table_exists(table_name):
        _get_pgvector_engine().init_vectorstore_table(
            table_name=table_name,
            vector_size=EMBEDDING_VECTOR_SIZE,
        )
    _initialized_vector_tables.add(table_name)


def _drop_vector_table(table_name: str) -> None:
    with psycopg.connect(_database_connection_url(), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(table_name))
            )
    _initialized_vector_tables.discard(table_name)


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


def get_vectorstore(collection_name: str | None = None) -> PGVectorStore:
    resolved_collection = collection_name or RESOLVED_COLLECTION_NAME
    table_name = _vector_table_name(resolved_collection)
    vectorstore = _vectorstores.get(table_name)
    if vectorstore is None:
        _ensure_vector_table(table_name)
        vectorstore = PGVectorStore.create_sync(
            engine=_get_pgvector_engine(),
            table_name=table_name,
            embedding_service=get_embeddings(),
        )
        _vectorstores[table_name] = vectorstore
    return vectorstore


def reset_vectorstore(collection_name: str | None = None) -> PGVectorStore:
    resolved_collection = collection_name or RESOLVED_COLLECTION_NAME
    table_name = _vector_table_name(resolved_collection)
    _drop_vector_table(table_name)
    _vectorstores.pop(table_name, None)
    return get_vectorstore(resolved_collection)


def get_llm():
    return OllamaLLM(
        model=OLLAMA_MODEL,
        base_url=OLLAMA_BASE_URL,
        temperature=0.15,
    )


def get_runtime_settings() -> RuntimeSettings:
    return SETTINGS
