import os
import zipfile

import pytest

from app.extensions import db
from app.models import Contact, Setting
from app.services import backup


def test_backup_and_restore_roundtrip(app, client):
    with app.app_context():
        db.session.add(Contact(name="Before backup"))
        db.session.commit()
        os.makedirs(os.path.join(app.config["DATA_DIR"], "files", "invoices"), exist_ok=True)
        with open(os.path.join(app.config["DATA_DIR"], "files", "invoices", "a.xml"), "w") as fh:
            fh.write("<x/>")
        path = backup.create_backup(app, label="manual")
        manifest = backup.inspect(path)
        assert manifest["counts"]["contact"] == 1
        with zipfile.ZipFile(path) as zf:
            assert "files/invoices/a.xml" in zf.namelist()

        db.session.add(Contact(name="After backup"))
        db.session.commit()
        os.remove(os.path.join(app.config["DATA_DIR"], "files", "invoices", "a.xml"))

    r = client.post("/settings/backup/restore", data={"name": os.path.basename(path), "confirm": "RESTORE"})
    assert r.status_code == 302
    with app.app_context():
        assert [c.name for c in Contact.query.all()] == ["Before backup"]
        assert os.path.exists(os.path.join(app.config["DATA_DIR"], "files", "invoices", "a.xml"))
        names = [b["name"] for b in backup.list_backups(app)]
        assert any("pre-restore" in n for n in names)


def test_restore_requires_confirmation(app, client):
    with app.app_context():
        path = backup.create_backup(app)
    client.post("/settings/backup/restore", data={"name": os.path.basename(path), "confirm": "no"})
    with app.app_context():
        assert not any("pre-restore" in b["name"] for b in backup.list_backups(app))


def test_inspect_rejects_bad_files(app, tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(backup.BackupError):
        backup.inspect(str(bad))
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as zf:
        zf.writestr("hello.txt", "x")
    with pytest.raises(backup.BackupError):
        backup.inspect(str(empty))
    with app.app_context():
        path = backup.create_backup(app)
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(tampered, "w") as dst:
        for item in src.namelist():
            data = src.read(item)
            if item == "erp.db":
                data = data[:-10] + b"0123456789"
            dst.writestr(item, data)
    with pytest.raises(backup.BackupError, match="checksum"):
        backup.inspect(str(tampered))


def test_auto_backup_and_prune(app):
    with app.app_context():
        Setting.set("backup.keep", 2)
        db.session.commit()
    for _ in range(3):
        with app.app_context():
            Setting.set("backup.last_at", "")
            db.session.commit()
        assert backup.run_auto_backup_if_due(app)
        import time
        time.sleep(1.1)  # file names have second resolution
    with app.app_context():
        autos = [b for b in backup.list_backups(app) if b["name"].endswith("-auto.zip")]
        assert len(autos) == 2
        # not due again right after a backup
        assert backup.run_auto_backup_if_due(app) is None


def test_backup_download(client):
    r = client.get("/settings/backup/download-now")
    assert r.status_code == 200
    assert r.headers["Content-Disposition"].startswith("attachment")
