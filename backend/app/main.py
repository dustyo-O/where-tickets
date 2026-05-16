import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.migrations import run_migrations_if_dev
from app.routers import health

logging.basicConfig(level=settings.LOG_LEVEL)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s in %s", app.title, settings.APP_ENV)
    await run_migrations_if_dev()
    yield


app = FastAPI(title="Where Tickets API", lifespan=lifespan)
app.include_router(health.router)
