from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.dependencies import POSTGRES_URL, _ensure_postgres_database_exists
from app.models import Base

engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _ensure_user_role_column() -> None:
    with engine.begin() as conn:
        conn.exec_driver_sql(
            """
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS role VARCHAR(20) NOT NULL DEFAULT 'user'
            """
        )
        conn.exec_driver_sql(
            """
            UPDATE users
            SET role = 'user'
            WHERE role IS NULL OR role = ''
            """
        )
        conn.exec_driver_sql(
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


def init_db() -> None:
    _ensure_postgres_database_exists()
    Base.metadata.create_all(bind=engine)
    _ensure_user_role_column()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
