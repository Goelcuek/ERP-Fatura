"""First-run setup wizard: account → company & logo → invoice numbers → Uyumsoft → backups → summary."""

import io
from datetime import date

import pytest
from PIL import Image

from app import create_app
from app.extensions import db
from app.models import Invoice, Setting

YEAR = date.today().year


@pytest.fixture
def fresh(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path / "data"), "WTF_CSRF_DISABLED": True,
                      "CUSTOMER": {"company_name": "Çağ-Tek Makina", "short_name": "Çağ-Tek Makina"}})
    yield app
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


def create_account(c):
    return c.post("/setup", data={"full_name": "Kurulum", "username": "kurulum", "password": "longpassword",
                                  "password2": "longpassword", "lang": "tr"})


def png():
    buf = io.BytesIO()
    Image.new("RGB", (120, 40), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


def test_full_wizard(fresh, tmp_path):
    c = fresh.test_client()
    r = create_account(c)
    assert r.headers["Location"].endswith("/setup/company")
    page = c.get("/setup/company").get_data(as_text=True)
    assert "Adım 2 / 6" in page and 'value="Çağ-Tek Makina"' in page

    r = c.post("/setup/company", data={
        "name": "Çağ-Tek Makina San. ve Tic. Ltd. Şti.", "tax_id": "123 456 7890", "tax_office": "Ostim",
        "address": "Ostim OSB 1234. Cad. No: 5", "district": "Yenimahalle", "city": "Ankara",
        "logo": (io.BytesIO(png()), "logo.png")}, content_type="multipart/form-data")
    assert r.headers["Location"].endswith("/setup/invoicing")

    r = c.post("/setup/invoicing", data={"efatura_last": f"cgt{YEAR}000000123", "earsiv_last": "",
                                         "earsiv_prefix": "CGA", "earsiv_start": "1",
                                         "efatura_prefix": "EFT", "efatura_start": "1",
                                         "default_profile": "TEMELFATURA"})
    assert r.headers["Location"].endswith("/setup/efatura")

    assert f'value="CGT{YEAR}000000123"' in c.get("/setup/invoicing").get_data(as_text=True)  # shown on return
    r = c.post("/setup/efatura", data={"integrator": "uyumsoft", "uyumsoft__environment": "production",
                                       "uyumsoft__username": "cagtek_ws", "uyumsoft__password": "gizli"})
    assert r.headers["Location"].endswith("/setup/backup")

    extra = tmp_path / "usb" / "Atolye-Yedek"
    r = c.post("/setup/backup", data={"enabled": "1", "extra_dir": str(extra), "backup_now": "1"})
    assert r.headers["Location"].endswith("/setup/done")
    assert any(f.name.startswith("erp-backup-") for f in extra.iterdir())  # the copy really arrived

    done = c.get("/setup/done").get_data(as_text=True)
    assert f"CGT{YEAR}000000124" in done and f"CGA{YEAR}000000001" in done
    assert "Uyumsoft · Canlı" in done
    with fresh.app_context():
        assert Setting.get("company.tax_id") == "1234567890"
        assert Setting.get("company.logo")
        assert Setting.get("invoice.default_profile") == "TEMELFATURA"
        assert Setting.get("integrator.name") == "uyumsoft"
        cfg = Setting.get("integrator.config")["uyumsoft"]
        assert cfg["username"] == "cagtek_ws" and cfg["password"] == "gizli"
        assert not any(k.startswith("xml_") for k in cfg)  # wizard leaves XML adjustments alone
        assert Setting.get("setup.pending") is False
    assert "Kurulum tamamlanmadı" not in c.get("/").get_data(as_text=True)


def test_banner_until_finished_and_resume(fresh):
    c = fresh.test_client()
    create_account(c)
    c.post("/setup/company", data={"name": "Çağ-Tek Makina", "tax_id": ""})
    c.get("/setup/invoicing")
    html = c.get("/").get_data(as_text=True)
    assert "Kurulum tamamlanmadı" in html
    assert c.get("/setup/continue").headers["Location"].endswith("/setup/invoicing")


def test_validation_keeps_input(fresh):
    c = fresh.test_client()
    create_account(c)
    r = c.post("/setup/company", data={"name": "Çağ-Tek", "tax_id": "12345", "city": "Ankara"})
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and 'value="Ankara"' in html and "10 hane" in html
    r = c.post("/setup/invoicing", data={"efatura_last": "12345", "earsiv_prefix": "ARS", "earsiv_start": "1"})
    assert "fatura numarası değil" in r.get_data(as_text=True)


def test_last_years_number_starts_this_year_at_one(fresh):
    c = fresh.test_client()
    create_account(c)
    c.post("/setup/invoicing", data={"efatura_last": f"CGT{YEAR - 1}000004711", "earsiv_last": f"CGA{YEAR}000000050"})
    with fresh.app_context():
        assert Setting.get("invoice.efatura_prefix") == "CGT" and Setting.get("invoice.efatura_start") == 1
        assert Setting.get("invoice.earsiv_start") == 51


def test_start_number_only_applies_to_its_year(fresh):
    from app.services.invoicing import next_invoice_number

    with fresh.app_context():
        Setting.set("invoice.efatura_start", 124)
        Setting.set("invoice.start_year", YEAR)
        assert next_invoice_number("TICARIFATURA", YEAR).endswith("000000124")
        assert next_invoice_number("TICARIFATURA", YEAR + 1).endswith("000000001")  # new year starts at 1
        assert Invoice.query.count() == 0


def test_practice_mode_and_uyumsoft_preselected(fresh):
    c = fresh.test_client()
    create_account(c)
    assert 'value="uyumsoft" checked' in c.get("/setup/efatura").get_data(as_text=True)
    c.post("/setup/efatura", data={"integrator": "mock"})
    with fresh.app_context():
        assert Setting.get("integrator.name") == "mock"
    assert 'value="mock" checked' in c.get("/setup/efatura").get_data(as_text=True)


def test_wizard_is_admin_only(fresh):
    c = fresh.test_client()
    create_account(c)
    with fresh.app_context():
        from app.models import User

        u = User(username="tekn", full_name="Teknisyen", role="staff")
        u.set_password("longpassword")
        db.session.add(u)
        db.session.commit()
    staff = fresh.test_client()
    staff.post("/login", data={"username": "tekn", "password": "longpassword"})
    assert staff.get("/setup/company").status_code == 403
    assert "Kurulum tamamlanmadı" not in staff.get("/").get_data(as_text=True)


def test_restore_instead_of_setup(tmp_path):
    """New computer: the first screen can bring everything back from a backup."""
    from app.services import backup as backup_svc

    old = create_app({"TESTING": True, "DATA_DIR": str(tmp_path / "old"), "WTF_CSRF_DISABLED": True})
    oc = old.test_client()
    create_account(oc)
    oc.post("/setup/company", data={"name": "Çağ-Tek Makina San. ve Tic. Ltd. Şti.", "tax_id": "1234567890"})
    with old.app_context():
        zip_path = backup_svc.create_backup(label="manual")
        db.session.remove()
        db.engine.dispose()

    new = create_app({"TESTING": True, "DATA_DIR": str(tmp_path / "new"), "WTF_CSRF_DISABLED": True})
    nc = new.test_client()
    with open(zip_path, "rb") as fh:
        r = nc.post("/setup/restore", data={"file": (fh, "erp-backup.zip")}, content_type="multipart/form-data")
    assert r.headers["Location"].endswith("/login")
    assert nc.post("/login", data={"username": "kurulum", "password": "longpassword"}).status_code == 302
    with new.app_context():
        assert Setting.get("company.tax_id") == "1234567890"
    # once there are users, nobody can restore without logging in
    with open(zip_path, "rb") as fh:
        r = new.test_client().post("/setup/restore", data={"file": (fh, "x.zip")}, content_type="multipart/form-data")
    assert r.headers["Location"].endswith("/login")
    with new.app_context():
        db.session.remove()
        db.engine.dispose()
