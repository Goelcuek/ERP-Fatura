"""End-to-end: customer → service order → invoice → send → payment → balance, via the HTTP routes."""

import os
from decimal import Decimal as D

from app.extensions import db
from app.models import Account, Contact, Invoice, Product, ServiceOrder, Transaction


def lines_form(*rows):
    data = {"line_description": [], "line_qty": [], "line_unit": [], "line_price": [], "line_discount": [],
            "line_vat": [], "line_product_id": []}
    for desc, qty, price, pid in rows:
        data["line_description"].append(desc)
        data["line_qty"].append(qty)
        data["line_unit"].append("C62")
        data["line_price"].append(price)
        data["line_discount"].append("")
        data["line_vat"].append("20")
        data["line_product_id"].append(str(pid or ""))
    return data


def test_full_repair_flow(app, client):
    # customer
    r = client.post("/contacts/new", data={
        "kind": "customer", "is_company": "1", "name": "Anadolu Metal Ltd. Şti.", "tax_id": "0680045512",
        "tax_office": "İkitelli", "address": "OSB 1. Cad", "district": "Başakşehir", "city": "İstanbul",
        "phone": "0212 000 00 00"})
    assert r.status_code == 302
    with app.app_context():
        c = Contact.query.filter_by(tax_id="0680045512").one()
        cid = c.id
        db.session.add(Product(code="RT-18", name="Rotor 18V", kind="part", price=D("1850"), stock_qty=D("5")))
        db.session.add(Account(name="Kasa", kind="cash"))
        db.session.commit()
        pid = Product.query.one().id
        aid = Account.query.one().id

    # e-Fatura check through the sandbox integrator
    r = client.post(f"/contacts/{cid}/check-efatura")
    assert r.status_code == 302
    with app.app_context():
        assert db.session.get(Contact, cid).efatura_user is True

    # service order
    r = client.post("/service/new", data={"contact_id": cid, "device_type": "Darbeli matkap", "brand": "Bosch",
                                          "model": "GSB 18V", "serial_no": "SN1", "complaint": "Çalışmıyor",
                                          "priority": "normal"})
    assert r.status_code == 302
    with app.app_context():
        o = ServiceOrder.query.one()
        oid = o.id
        assert o.number.startswith("SRV-")
    assert client.get(f"/service/{oid}/receipt").status_code == 200

    r = client.post(f"/service/{oid}/lines", data=lines_form(("Rotor 18V", "2", "1850", pid), ("İşçilik", "1,5", "750", None)))
    assert r.status_code == 302
    client.post(f"/service/{oid}/status", data={"status": "ready"})

    # invoice from order
    r = client.post(f"/service/{oid}/invoice")
    assert r.status_code == 302 and "/invoices/" in r.headers["Location"]
    with app.app_context():
        inv = Invoice.query.one()
        iid = inv.id
        assert inv.status == "draft"
        assert inv.profile == "TICARIFATURA"
        assert inv.payable == D("5790.00")  # (3700 + 1125) * 1.2

    r = client.post(f"/invoices/{iid}/issue", data={"send": "1"})
    assert r.status_code == 302
    with app.app_context():
        inv = db.session.get(Invoice, iid)
        assert inv.number.startswith("EFT2")
        assert len(inv.number) == 16
        assert inv.status == "sent"
        assert os.path.exists(os.path.join(app.config["DATA_DIR"], inv.xml_path))
        assert Product.query.one().stock_qty == D("3")  # 2 rotors used
        assert db.session.get(ServiceOrder, oid).status == "delivered"
        assert db.session.get(Contact, cid).balance() == D("5790.00")

    assert client.get(f"/invoices/{iid}/xml").status_code == 200
    assert client.get(f"/invoices/{iid}/print").status_code == 200

    # receiver accepts (sandbox)
    client.post(f"/invoices/{iid}/refresh")
    # partial then full payment
    client.post(f"/invoices/{iid}/pay", data={"account_id": aid, "amount": "1.000,00"})
    with app.app_context():
        inv = db.session.get(Invoice, iid)
        assert inv.status == "accepted"
        assert inv.open_amount == D("4790.00")
    client.post(f"/invoices/{iid}/pay", data={"account_id": aid, "amount": "4790"})
    with app.app_context():
        assert db.session.get(Invoice, iid).open_amount == D("0")
        assert db.session.get(Contact, cid).balance() == D("0")
        assert db.session.get(Account, aid).balance() == D("5790.00")

    # issued invoice can't be edited
    r = client.get(f"/invoices/{iid}/edit")
    assert r.status_code == 302

    # return invoice puts stock back
    r = client.post(f"/invoices/{iid}/return")
    with app.app_context():
        ret = Invoice.query.filter_by(type_code="IADE").one()
        rid = ret.id
        assert ret.return_ref_number == db.session.get(Invoice, iid).number
    client.post(f"/invoices/{rid}/issue", data={"send": "1"})
    with app.app_context():
        ret = db.session.get(Invoice, rid)
        assert ret.status == "accepted"
        assert Product.query.one().stock_qty == D("5")
        assert db.session.get(Contact, cid).balance() == D("-5790.00")  # we owe the refund


def test_earsiv_numbering_is_sequential_and_separate(app, client):
    with app.app_context():
        c = Contact(name="Ali Veli", tax_id="10000000146", is_company=False, district="A", city="B")
        db.session.add(c)
        db.session.commit()
        cid = c.id
    numbers = []
    for _ in range(3):
        data = {"contact_id": cid, "profile": "EARSIVFATURA", "type_code": "SATIS", "issue_date": "2026-09-01",
                "action": "issue_send"}
        data.update(lines_form(("Bakım", "1", "100", None)))
        r = client.post("/invoices/new", data=data)
        assert r.status_code == 302
    with app.app_context():
        numbers = sorted(i.number for i in Invoice.query.all())
    assert numbers == ["ARS2026000000001", "ARS2026000000002", "ARS2026000000003"]


def test_invoice_validation_blocks_issue(app, client):
    with app.app_context():
        c = Contact(name="Kayıtsız Firma", tax_id="9870011223", district="A", city="B", efatura_user=False)
        db.session.add(c)
        db.session.commit()
        cid = c.id
    data = {"contact_id": cid, "profile": "TICARIFATURA", "type_code": "SATIS", "action": "issue"}
    data.update(lines_form(("Bakım", "1", "100", None)))
    client.post("/invoices/new", data=data)
    with app.app_context():
        inv = Invoice.query.one()
        assert inv.status == "draft" and inv.number is None


def test_expense_with_payment_and_supplier_balance(app, client):
    with app.app_context():
        s = Contact(name="Parça A.Ş.", kind="supplier", tax_id="1840056677")
        a = Account(name="Banka", kind="bank", opening_balance=D("1000"))
        db.session.add_all([s, a])
        db.session.commit()
        sid, aid = s.id, a.id
    client.post("/books/expenses/new", data={"contact_id": sid, "category": "Parça / Malzeme", "amount": "1200",
                                             "amount_mode": "gross", "vat_rate": "20", "pay_account_id": ""})
    with app.app_context():
        from app.models import Expense

        e = Expense.query.one()
        assert (e.net, e.vat, e.total) == (D("1000.00"), D("200.00"), D("1200.00"))
        assert db.session.get(Contact, sid).balance() == D("-1200.00")
    client.post("/books/tx/new", data={"kind": "payment", "contact_id": sid, "account_id": aid, "amount": "1200"})
    with app.app_context():
        assert db.session.get(Contact, sid).balance() == D("0")
        assert db.session.get(Account, aid).balance() == D("-200.00")


def test_transfer_moves_money(app, client):
    with app.app_context():
        a = Account(name="Kasa", kind="cash", opening_balance=D("500"))
        b = Account(name="Banka", kind="bank")
        db.session.add_all([a, b])
        db.session.commit()
        aid, bid = a.id, b.id
    client.post("/books/tx/new", data={"kind": "transfer", "account_id": aid, "to_account_id": bid, "amount": "300"})
    with app.app_context():
        assert db.session.get(Account, aid).balance() == D("200.00")
        assert db.session.get(Account, bid).balance() == D("300.00")
        tid = Transaction.query.first().id
    client.post(f"/books/tx/{tid}/delete")
    with app.app_context():
        assert Transaction.query.count() == 0


def test_numbering_can_continue_from_previous_system(app, ctx):
    from app.models import Setting
    from app.services.invoicing import next_invoice_number

    assert next_invoice_number("TICARIFATURA", 2026) == "EFT2026000000001"
    Setting.set("invoice.efatura_start", 158)
    assert next_invoice_number("TICARIFATURA", 2026) == "EFT2026000000158"
    assert next_invoice_number("EARSIVFATURA", 2026) == "ARS2026000000001"


def test_integrator_choice_changes_sent_xml(app, client, tmp_path):
    from app.models import Setting

    with app.app_context():
        c = Contact(name="Ali Veli", tax_id="10000000146", is_company=False, district="A", city="B")
        db.session.add(c)
        db.session.commit()
        cid = c.id
    # choose manual export into a temp folder, with the XSLT embedding switched off
    r = client.post("/settings/integrator", data={"integrator": "file", "file__folder": str(tmp_path / "out"),
                                                  "file__xml_earsiv_sending_type": "1", "file__xml_uppercase_uuid": "1"})
    assert r.status_code == 302
    with app.app_context():
        cfg = Setting.get("integrator.config")["file"]
        assert cfg["xml_embed_xslt"] == "0" and "xml_earsiv_sending_type" not in cfg  # only deviations stored
        assert Setting.get("integrator.name") == "file"
    data = {"contact_id": cid, "profile": "EARSIVFATURA", "type_code": "SATIS", "action": "issue_send"}
    data.update(lines_form(("Bakım", "1", "100", None)))
    client.post("/invoices/new", data=data)
    with app.app_context():
        inv = Invoice.query.one()
        assert inv.status == "exported" and inv.integrator == "file"
        sent = open(os.path.join(app.config["DATA_DIR"], inv.sent_xml_path), "rb").read()
        canonical = open(os.path.join(app.config["DATA_DIR"], inv.xml_path), "rb").read()
        exported = (tmp_path / "out" / f"{inv.number}.xml").read_bytes()
        assert exported == sent and b">XSLT<" not in sent and b"SendingType" in sent
        assert b">XSLT<" not in canonical
    r = client.get(f"/invoices/{inv.id}/render")
    assert r.status_code == 200 and "e-ARŞİV FATURA" in r.get_data(as_text=True)
    assert client.get(f"/invoices/{inv.id}/xml").data == sent
    assert client.get("/settings/integrator").status_code == 200
    assert client.get("/settings/integrator?show=izibiz").status_code == 200


def test_unsupported_profile_is_reported(app, client):
    with app.app_context():
        from app.models import Setting

        Setting.set("integrator.name", "sovos")
        c = Contact(name="Ali Veli", tax_id="10000000146", is_company=False, district="A", city="B")
        db.session.add(c)
        db.session.commit()
        cid = c.id
    data = {"contact_id": cid, "profile": "EARSIVFATURA", "type_code": "SATIS", "action": "issue_send"}
    data.update(lines_form(("Bakım", "1", "100", None)))
    client.post("/invoices/new", data=data)
    with app.app_context():
        inv = Invoice.query.one()
        assert inv.status == "error" and "Elle XML" in inv.status_message
