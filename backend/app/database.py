"""Database engine, session factory, and the one request-scoped session dependency."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings

engine = create_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout_s,
    pool_pre_ping=True,   # a Postgres restart must not leave dead connections in the pool
)
SessionLocal = sessionmaker(engine)


def get_db():
    """The request's DB session. Defined once: FastAPI shares a dependency within a request only when it is the
    same function, and two copies (auth + routers) made every signed-in request hold two connections."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
