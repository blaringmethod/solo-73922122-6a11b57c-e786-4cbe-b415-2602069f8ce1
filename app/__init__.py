"""Flask application factory for the collation check page."""

from __future__ import annotations

import os

from flask import Flask

from . import db
from .parsing import MAX_BATCHES, MAX_SHEETS


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        DATABASE=os.environ.get("DATABASE_PATH", "/data/collation.sqlite3"),
        MAX_CONTENT_LENGTH=512 * 1024,
        SECRET_KEY=os.environ.get("SECRET_KEY", "collation-check-local"),
        AUTOINIT=True,
    )
    if config:
        app.config.update(config)

    if app.config["AUTOINIT"]:
        init_app_database(app)
    app.teardown_appcontext(db.close_db)

    @app.template_filter("r")
    def _revision(value: int) -> str:
        return f"r{value}"

    from .views import bp

    app.register_blueprint(bp)
    app.extensions["limits"] = {"batches": MAX_BATCHES, "sheets": MAX_SHEETS}

    return app


def init_app_database(app: Flask) -> None:
    conn = db.connect(app.config["DATABASE"])
    try:
        db.init_db(conn)
    finally:
        conn.close()


__all__ = ["create_app", "init_app_database"]
