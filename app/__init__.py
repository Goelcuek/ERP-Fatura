import os
import secrets

from flask import Flask
from sqlalchemy import event
from sqlalchemy.engine import Engine

__version__ = "1.0.0"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    import sqlite3

    if isinstance(dbapi_conn, sqlite3.Connection):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()


def _secret_key(data_dir):
    path = os.path.join(data_dir, "secret.key")
    if not os.path.exists(path):
        with open(path, "w") as fh:
            fh.write(secrets.token_hex(32))
    with open(path) as fh:
        return fh.read().strip()


def _add_missing_columns(db):
    """Tiny forward-only migration: add columns that newer versions define to existing tables."""
    from sqlalchemy import inspect, text

    insp = inspect(db.engine)
    with db.engine.begin() as conn:
        for table in db.metadata.sorted_tables:
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col.type.compile(db.engine.dialect)}'
                default = col.default.arg if col.default is not None and not callable(col.default.arg) else None
                if isinstance(default, str):
                    ddl += " DEFAULT '" + default.replace("'", "''") + "'"
                elif isinstance(default, (bool, int)):
                    ddl += f" DEFAULT {int(default)}"
                conn.execute(text(ddl))


def create_app(test_config=None):
    app = Flask(__name__)
    data_dir = os.path.abspath(os.environ.get("ERP_DATA_DIR", os.path.join(BASE_DIR, "data")))
    if test_config and "DATA_DIR" in test_config:
        data_dir = test_config["DATA_DIR"]
    os.makedirs(os.path.join(data_dir, "files"), exist_ok=True)

    app.config.update(
        DATA_DIR=data_dir,
        SECRET_KEY=_secret_key(data_dir),
        SQLALCHEMY_DATABASE_URI="sqlite:///" + os.path.join(data_dir, "erp.db"),
        SQLALCHEMY_ENGINE_OPTIONS={"connect_args": {"check_same_thread": False, "timeout": 15}},
        MAX_CONTENT_LENGTH=512 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if test_config:
        app.config.update(test_config)

    from .extensions import db

    db.init_app(app)

    from . import models  # noqa: F401  (register tables)

    with app.app_context():
        db.create_all()
        _add_missing_columns(db)

    from .i18n import init_i18n
    from .web import init_web

    init_i18n(app)
    init_web(app)

    from .routes import register_blueprints

    register_blueprints(app)

    from .cli import register_cli

    register_cli(app)

    return app
