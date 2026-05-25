import logging
import os

from app.config import settings

logger = logging.getLogger(__name__)

_DEV_ENVS = {"local", "dev"}


async def run_migrations_if_dev() -> None:
    """Apply Piccolo migrations on startup in local/dev environments.

    In staging/prod, migrations are an explicit deployment step and this is a no-op.
    """
    if settings.APP_ENV not in _DEV_ENVS:
        logger.info("Skipping auto-migrations (APP_ENV=%s)", settings.APP_ENV)
        return

    logger.info("Applying Piccolo migrations...")

    # Piccolo discovers piccolo_conf via the PICCOLO_CONF env var.
    os.environ.setdefault("PICCOLO_CONF", "piccolo_conf")

    try:
        from piccolo.apps.migrations.commands.forwards import run_forwards

        response = await run_forwards(app_name="all")
        if not response.success:
            raise RuntimeError(f"Piccolo migrations failed: {response.message}")
    except Exception:
        logger.exception("Failed to apply Piccolo migrations")
        raise

    logger.info("Piccolo migrations applied")
