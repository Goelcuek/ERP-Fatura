"""Backups: the whole system state is one SQLite file plus the data/files folder.

A backup is a single .zip containing a consistent SQLite snapshot (taken with the
online backup API, safe while the app is running), all generated files and a
manifest with checksums. Restoring validates the archive and takes a safety
backup first.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timedelta

from flask import current_app

from .. import __version__

MANIFEST = "manifest.json"
DB_NAME = "erp.db"
PREFIX = "erp-backup-"


class BackupError(Exception):
    pass


def data_dir(app=None):
    return (app or current_app).config["DATA_DIR"]


def db_path(app=None):
    return os.path.join(data_dir(app), DB_NAME)


def default_backup_dir(app=None):
    return os.path.join(data_dir(app), "backups")


def backup_dir(app=None):
    from ..models import Setting

    d = Setting.get("backup.dir") or default_backup_dir(app)
    os.makedirs(d, exist_ok=True)
    return d


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _snapshot_db(src, dest):
    s = sqlite3.connect(src)
    d = sqlite3.connect(dest)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def create_backup(app=None, label="manual", dest_dir=None):
    """Write a backup zip and return its path."""
    app = app or current_app._get_current_object()
    dest_dir = dest_dir or backup_dir(app)
    os.makedirs(dest_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"{PREFIX}{stamp}-{label}.zip"
    final = os.path.join(dest_dir, name)
    with tempfile.TemporaryDirectory() as tmp:
        snap = os.path.join(tmp, DB_NAME)
        _snapshot_db(db_path(app), snap)
        con = sqlite3.connect(snap)
        counts = {}
        for table in ("contact", "invoice", "service_order", "expense", "transaction"):
            try:
                counts[table] = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            except sqlite3.Error:
                counts[table] = None
        con.close()
        manifest = {
            "app": "erp-fatura",
            "version": __version__,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "label": label,
            "db_sha256": _sha256(snap),
            "counts": counts,
        }
        tmp_zip = os.path.join(tmp, name)
        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(snap, DB_NAME)
            files_root = os.path.join(data_dir(app), "files")
            if os.path.isdir(files_root):
                for base, _dirs, files in os.walk(files_root):
                    for f in files:
                        full = os.path.join(base, f)
                        zf.write(full, os.path.relpath(full, data_dir(app)))
            secret = os.path.join(data_dir(app), "secret.key")
            if os.path.exists(secret):
                zf.write(secret, "secret.key")
            zf.writestr(MANIFEST, json.dumps(manifest, indent=2, ensure_ascii=False))
        shutil.move(tmp_zip, final)
    return final


def list_backups(app=None):
    d = backup_dir(app)
    out = []
    for f in sorted(os.listdir(d), reverse=True):
        if f.startswith(PREFIX) and f.endswith(".zip"):
            p = os.path.join(d, f)
            st = os.stat(p)
            out.append({"name": f, "path": p, "size": st.st_size, "mtime": datetime.fromtimestamp(st.st_mtime)})
    return out


def prune(keep, app=None):
    """Delete the oldest automatic backups beyond `keep`. Manual & pre-restore backups are kept."""
    autos = [b for b in list_backups(app) if b["name"].endswith("-auto.zip")]
    for b in autos[keep:]:
        os.remove(b["path"])


def inspect(zip_path):
    """Validate a backup archive; returns its manifest or raises BackupError."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
            if MANIFEST not in names or DB_NAME not in names:
                raise BackupError("Not a valid backup file (manifest or database missing).")
            for n in names:
                if n.startswith("/") or ".." in n.split("/"):
                    raise BackupError("Backup contains unsafe paths.")
            manifest = json.loads(zf.read(MANIFEST))
            with tempfile.TemporaryDirectory() as tmp:
                zf.extract(DB_NAME, tmp)
                p = os.path.join(tmp, DB_NAME)
                if _sha256(p) != manifest.get("db_sha256"):
                    raise BackupError("Database checksum mismatch — the backup file is damaged.")
                con = sqlite3.connect(p)
                ok = con.execute("PRAGMA integrity_check").fetchone()[0]
                con.close()
                if ok != "ok":
                    raise BackupError("Database integrity check failed.")
    except zipfile.BadZipFile:
        raise BackupError("Not a valid zip file.")
    return manifest


def restore(zip_path, app=None):
    """Replace the live database and files with the backup's contents."""
    from ..extensions import db

    app = app or current_app._get_current_object()
    manifest = inspect(zip_path)
    safety = create_backup(app, label="pre-restore")
    db.session.remove()
    db.engine.dispose()
    ddir = data_dir(app)
    with zipfile.ZipFile(zip_path) as zf, tempfile.TemporaryDirectory() as tmp:
        zf.extractall(tmp)
        # database: copy via the backup API so the WAL/SHM files stay consistent
        for suffix in ("-wal", "-shm"):
            try:
                os.remove(db_path(app) + suffix)
            except FileNotFoundError:
                pass
        _snapshot_db(os.path.join(tmp, DB_NAME), db_path(app))
        files_src = os.path.join(tmp, "files")
        files_dst = os.path.join(ddir, "files")
        if os.path.isdir(files_dst):
            shutil.rmtree(files_dst)
        if os.path.isdir(files_src):
            shutil.copytree(files_src, files_dst)
        secret = os.path.join(tmp, "secret.key")
        if os.path.exists(secret):
            shutil.copy2(secret, os.path.join(ddir, "secret.key"))
    db.engine.dispose()
    return manifest, safety


def copy_to_extra(path, app=None):
    from ..models import Setting

    extra = Setting.get("backup.extra_dir")
    if extra:
        os.makedirs(extra, exist_ok=True)
        shutil.copy2(path, os.path.join(extra, os.path.basename(path)))


def run_auto_backup_if_due(app):
    """Called periodically by the scheduler thread."""
    from ..extensions import db
    from ..models import Setting

    with app.app_context():
        if not Setting.get("backup.enabled"):
            return None
        interval = int(Setting.get("backup.interval_hours") or 24)
        last = Setting.get("backup.last_at")
        if last:
            try:
                if datetime.now() - datetime.fromisoformat(last) < timedelta(hours=interval):
                    return None
            except ValueError:
                pass
        try:
            path = create_backup(app, label="auto")
            copy_to_extra(path, app)
            prune(int(Setting.get("backup.keep") or 30), app)
            Setting.set("backup.last_at", datetime.now().isoformat(timespec="seconds"))
            Setting.set("backup.last_error", "")
            db.session.commit()
            return path
        except Exception as e:  # never kill the scheduler thread
            db.session.rollback()
            Setting.set("backup.last_error", f"{datetime.now():%Y-%m-%d %H:%M} {e}")
            db.session.commit()
            app.logger.exception("Automatic backup failed")
            return None


def start_scheduler(app, every_seconds=900):
    """Background thread that checks every 15 minutes whether an automatic backup is due."""

    def loop():
        time.sleep(5)
        while True:
            run_auto_backup_if_due(app)
            time.sleep(every_seconds)

    t = threading.Thread(target=loop, name="auto-backup", daemon=True)
    t.start()
    return t
