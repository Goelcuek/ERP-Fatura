import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Setting, User  # noqa: E402

COMPANY = {
    "name": "Test Servis Ltd. Şti.", "tax_id": "1234567890", "tax_office": "Kadıköy", "address": "Test Sok. No: 1",
    "district": "Kadıköy", "city": "İstanbul", "country": "Türkiye", "phone": "0216 000 00 00",
    "email": "info@test.example", "iban": "TR000000000000000000000000",
}


@pytest.fixture
def app(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path / "data"), "WTF_CSRF_DISABLED": True})
    with app.app_context():
        for k, v in COMPANY.items():
            Setting.set(f"company.{k}", v)
        admin = User(username="admin", full_name="Admin User", role="admin")
        admin.set_password("password123")
        staff = User(username="staff", full_name="Staff User", role="staff")
        staff.set_password("password123")
        db.session.add_all([admin, staff])
        db.session.commit()
    yield app
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


@pytest.fixture
def client(app):
    c = app.test_client()
    r = c.post("/login", data={"username": "admin", "password": "password123"})
    assert r.status_code == 302
    return c


@pytest.fixture
def ctx(app):
    with app.test_request_context():
        from flask import g

        g.user = User.query.filter_by(username="admin").first()
        yield
