import os

from piccolo.conf.apps import AppConfig

from app.tables import SystemHeartbeat

CURRENT_DIRECTORY = os.path.dirname(os.path.abspath(__file__))


APP_CONFIG = AppConfig(
    app_name="piccolo_migrations",
    migrations_folder_path=CURRENT_DIRECTORY,
    table_classes=[SystemHeartbeat],
    migration_dependencies=[],
    commands=[],
)
