from piccolo.conf.apps import AppRegistry

from app.db import DB

DB = DB

APP_REGISTRY = AppRegistry(apps=["piccolo_migrations.piccolo_app"])
