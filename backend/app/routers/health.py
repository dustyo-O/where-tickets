import logging

from fastapi import APIRouter, Response, status

from app.db import DB

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
async def health(response: Response) -> dict[str, str]:
    try:
        await DB.run_ddl("SELECT 1")
        db_status = "ok"
    except Exception:
        logger.exception("Health check: database probe failed")
        db_status = "down"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {"status": "ok" if db_status == "ok" else "degraded", "database": db_status}
