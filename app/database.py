from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.dependencies import POSTGRES_URL, _ensure_postgres_database_exists
from app.models import Base

engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db() -> None:
    _ensure_postgres_database_exists()
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
