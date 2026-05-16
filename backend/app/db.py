from urllib.parse import urlparse

from piccolo.engine.postgres import PostgresEngine

from app.config import settings


def _build_engine() -> PostgresEngine:
    parsed = urlparse(settings.DATABASE_URL)
    return PostgresEngine(
        config={
            "host": parsed.hostname or "localhost",
            "port": parsed.port or 5432,
            "user": parsed.username or "",
            "password": parsed.password or "",
            "database": (parsed.path or "/").lstrip("/"),
        }
    )


DB = _build_engine()
