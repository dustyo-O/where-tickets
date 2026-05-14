import logging

from fastapi import FastAPI

from app.config import settings
from app.routers import health

logging.basicConfig(level=settings.LOG_LEVEL)

app = FastAPI(title="Where Tickets API")
app.include_router(health.router)

logging.getLogger(__name__).info("Starting %s in %s", app.title, settings.APP_ENV)
