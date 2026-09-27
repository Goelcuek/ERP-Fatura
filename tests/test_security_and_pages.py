from app.extensions import db
from app.models import User


def test_login_required(app):
    c = app.test_client()
    r = c.get("/invoices/")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_bad_login(app):
    c = app.test_client()
    r = c.post("/login", data={"username": "admin", "password": "wrong"})
    assert r.status_code == 200
    r = c.get("/")
    assert r.status_code == 302


def test_open_redirect_blocked(app):
    c = app.test_client()
    r = c.post("/login?next=https://evil.example/", data={"username": "admin", "password": "password123"})
    assert r.headers["Location"] == "/"


def test_staff_cannot_open_settings(app):
    c = app.test_client()
    c.post("/login", data={"username": "staff", "password": "password123"})
    assert c.get("/").status_code == 200
    assert c.get("/settings/").status_code == 403
    assert c.get("/settings/backup").status_code == 403


def test_csrf_enforced(tmp_path):
    from app import create_app

    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path / "d2")})
    with app.app_context():
        u = User(username="a", full_name="A", role="admin")
        u.set_password("password123")
        db.session.add(u)
        db.session.commit()
    c = app.test_client()
    # login without the token is rejected
    r = c.post("/login", data={"username": "a", "password": "password123"})
    assert r.status_code == 302
    assert c.get("/").status_code == 302  # still logged out
    c.get("/login")
    with c.session_transaction() as s:
        tok = s["_csrf"]
    r = c.post("/login", data={"username": "a", "password": "password123", "_csrf": tok})
    assert c.get("/").status_code == 200


def test_first_run_setup(tmp_path):
    from app import create_app

    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path / "d3"), "WTF_CSRF_DISABLED": True})
    c = app.test_client()
    assert "/setup" in c.get("/").headers["Location"]
    r = c.post("/setup", data={"full_name": "Owner", "username": "owner", "password": "longpassword",
                               "password2": "longpassword", "lang": "tr", "name": "Firma"})
    assert r.status_code == 302
    assert c.get("/").status_code == 200
    assert c.get("/setup").status_code == 302


def test_every_page_renders_with_demo_data(tmp_path):
    from app import create_app
    from app.demo import seed

    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path / "demo"), "WTF_CSRF_DISABLED": True})
    with app.app_context():
        seed()
    c = app.test_client()
    c.post("/login", data={"username": "demo", "password": "demo1234"})
    urls = ["/", "/search?q=bosch", "/contacts/", "/contacts/1", "/contacts/1?tab=invoices", "/contacts/1?tab=ledger",
            "/contacts/new", "/contacts/1/edit", "/service/", "/service/?status=all&page=2", "/service/new",
            "/service/1", "/service/1/edit", "/service/1/receipt", "/invoices/", "/invoices/?status=open",
            "/invoices/?page=3", "/invoices/new", "/invoices/1", "/invoices/1/print", "/invoices/1/xml",
            "/products/", "/products/?low=1", "/products/new", "/products/1/edit", "/books/", "/books/accounts/1",
            "/books/accounts/new", "/books/tx/new?kind=collection", "/books/tx/new?kind=transfer", "/books/expenses",
            "/books/expenses/new", "/books/expenses/1/edit", "/reports/", "/reports/receivables",
            "/reports/export/invoices.csv", "/reports/export/invoice-lines.csv", "/reports/export/expenses.csv",
            "/reports/export/transactions.csv", "/reports/export/contacts.csv", "/settings/", "/settings/invoicing",
            "/settings/integrator", "/settings/users", "/settings/users/1", "/settings/users/new",
            "/settings/backup", "/profile", "/contacts/api/search?q=a", "/invoices/api/product/1"]
    with app.app_context():
        from app.models import Invoice, ServiceOrder

        draft = Invoice.query.filter_by(status="draft").first()
        urls += [f"/invoices/{draft.id}", f"/invoices/{draft.id}/edit", f"/invoices/{draft.id}/xml"]
        open_order = ServiceOrder.query.filter_by(status="in_repair").first()
        urls += [f"/service/{open_order.id}"]
    for lang in ("tr", "en"):
        with app.app_context():
            User.query.filter_by(username="demo").one().lang = lang
            db.session.commit()
        for u in urls:
            r = c.get(u)
            assert r.status_code == 200, (lang, u, r.status_code)


def test_old_database_gets_new_columns(tmp_path):
    import sqlite3

    from app import create_app

    d = tmp_path / "old"
    app = create_app({"TESTING": True, "DATA_DIR": str(d)})
    with app.app_context():
        db.session.remove()
        db.engine.dispose()
    con = sqlite3.connect(d / "erp.db")
    con.execute("ALTER TABLE invoice DROP COLUMN sent_xml_path")
    con.commit()
    assert "sent_xml_path" not in [r[1] for r in con.execute("PRAGMA table_info(invoice)")]
    con.close()
    app = create_app({"TESTING": True, "DATA_DIR": str(d)})
    con = sqlite3.connect(d / "erp.db")
    assert "sent_xml_path" in [r[1] for r in con.execute("PRAGMA table_info(invoice)")]
    con.close()
