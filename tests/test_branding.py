import io
import struct
import zlib

from app.extensions import db
from app.models import Setting
from app.services import branding


def tiny_png():
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"\x00\xff\x00\x00"  # one red pixel
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_names_and_monogram():
    assert branding.short_name("Çağ-Tek Makina San. ve Tic. Ltd. Şti.") == "Çağ-Tek Makina"
    assert branding.short_name("Çağ-Tek Makina") == "Çağ-Tek Makina"
    assert branding.short_name("Yıldız İnşaat A.Ş.") == "Yıldız İnşaat"
    assert branding.initials("Çağ-Tek Makina") == "ÇT"
    assert branding.initials("Yıldız İnşaat") == "Yİ"


def test_customer_preset_prefills_first_run(tmp_path):
    from app import create_app

    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path / "d"), "WTF_CSRF_DISABLED": True,
                      "CUSTOMER": {"company_name": "Çağ-Tek Makina", "short_name": "Çağ-Tek Makina"}})
    c = app.test_client()
    html = c.get("/setup").get_data(as_text=True)
    assert "<title>İlk kurulum · Çağ-Tek Makina</title>" in html
    assert ">ÇT<" in html
    c.post("/setup", data={"full_name": "Sahip", "username": "sahip", "password": "longpassword",
                           "password2": "longpassword", "lang": "tr"})
    assert 'value="Çağ-Tek Makina"' in c.get("/setup/company").get_data(as_text=True)


def test_repo_preset_is_cag_tek():
    import os

    from app import BASE_DIR

    assert branding.load_customer_preset(BASE_DIR)["company_name"] == "Çağ-Tek Makina"
    assert os.path.exists(os.path.join(BASE_DIR, "customer.json"))


def test_logo_upload_serves_and_embeds(app, client):
    r = client.post("/settings/", data={"name": "Çağ-Tek Makina San. ve Tic. Ltd. Şti.",
                                        "logo": (io.BytesIO(tiny_png()), "logo.png")},
                    content_type="multipart/form-data")
    assert r.status_code == 302
    page = client.get("/").get_data(as_text=True)
    assert "Çağ-Tek Makina</div>" in page and "/branding/logo" in page
    logo = client.get("/branding/logo")
    assert logo.status_code == 200 and logo.data == tiny_png() and logo.mimetype == "image/png"
    with app.test_request_context():
        from app.services.ubl_profile import default_xslt

        assert b"data:image/png;base64," in default_xslt()
    # removing it
    client.post("/settings/", data={"name": "Çağ-Tek Makina", "remove_logo": "1"}, content_type="multipart/form-data")
    assert client.get("/branding/logo").status_code == 404


def test_logo_rejects_other_files(app, client):
    client.post("/settings/", data={"name": "X", "logo": (io.BytesIO(b"<svg onload=alert(1)>"), "x.svg")},
                content_type="multipart/form-data")
    with app.app_context():
        assert Setting.get("company.logo") == ""
    client.post("/settings/", data={"name": "X", "logo": (io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 400_000), "big.png")},
                content_type="multipart/form-data")
    with app.app_context():
        assert Setting.get("company.logo") == ""
        db.session.remove()
