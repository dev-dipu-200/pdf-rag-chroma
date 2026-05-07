from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.dependencies import get_async_postgres_url, get_sync_postgres_url, _ensure_postgres_database_exists
from app.models import Base

# Async setup for FastAPI
async_engine = create_async_engine(get_async_postgres_url(), pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# Sync setup for Celery
sync_engine = create_engine(get_sync_postgres_url(), pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=sync_engine)


async def _ensure_user_role_column() -> None:
    async with async_engine.begin() as conn:
        await conn.execute(
            text(
                """
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS role VARCHAR(20) NOT NULL DEFAULT 'user'
                """
            )
        )
        await conn.execute(
            text(
                """
                UPDATE users
                SET role = 'user'
                WHERE role IS NULL OR role = ''
                """
            )
        )
        await conn.execute(
            text(
                """
                WITH first_user AS (
                    SELECT id
                    FROM users
                    ORDER BY id ASC
                    LIMIT 1
                )
                UPDATE users
                SET role = 'admin'
                WHERE id IN (SELECT id FROM first_user)
                """
            )
        )


async def _ensure_anonymous_query_usage_table() -> None:
    async with async_engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS anonymous_query_usage (
                    id SERIAL PRIMARY KEY,
                    ip_address VARCHAR(128) NOT NULL UNIQUE,
                    query_count INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
                """
            )
        )


async def _ensure_pdf_document_file_hash_column() -> None:
    async with async_engine.begin() as conn:
        await conn.execute(
            text(
                """
                ALTER TABLE pdf_documents
                ADD COLUMN IF NOT EXISTS file_hash VARCHAR(64)
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_pdf_documents_user_file_hash
                ON pdf_documents (user_id, file_hash)
                """
            )
        )


async def init_db() -> None:
    await _ensure_postgres_database_exists()
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _ensure_user_role_column()
    await _ensure_anonymous_query_usage_table()
    await _ensure_pdf_document_file_hash_column()


async def get_db():
    async with AsyncSessionLocal() as db:
        try:
            yield db
        finally:
            await db.close()
