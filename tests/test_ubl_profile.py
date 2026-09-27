from datetime import date
from decimal import Decimal as D

from lxml import etree

from app.extensions import db
from app.models import Contact, Invoice, InvoiceLine, Setting
from app.services.integrators import REGISTRY
from app.services.integrators.base import XmlOptions
from app.services.ubl import build_invoice_xml
from app.services.ubl_profile import apply_profile, render_html

X = {"cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
     "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"}


def earsiv_invoice():
    c = Contact(name="Hasan Kaya", first_name="Hasan", last_name="Kaya", tax_id="10000000146", district="A", city="B",
                is_company=False)
    inv = Invoice(contact=c, number="ARS2026000000001", issue_date=date(2026, 9, 1), profile="EARSIVFATURA",
                  type_code="SATIS")
    inv.lines = [InvoiceLine(position=0, description="Bakım", qty=D("2"), unit="C62", unit_price=D("1250.5"),
                             vat_rate=20, discount_rate=D("0"))]
    inv.recompute()
    return inv


def adr_types(root):
    return [(a.findtext("cbc:DocumentType", namespaces=X), a.findtext("cbc:DocumentTypeCode", namespaces=X))
            for a in root.findall("cac:AdditionalDocumentReference", X)]


def test_profiles_change_only_the_dialect(app, ctx):
    inv = earsiv_invoice()
    canonical = build_invoice_xml(inv, Setting.group("company"))
    full = etree.fromstring(apply_profile(canonical, inv, XmlOptions(embed_xslt=True, earsiv_sending_type=True)))
    assert adr_types(full) == [("ELEKTRONIK", "SendingType"), ("XSLT", None)]
    # XSLT reference sits before the signature, keeping UBL element order
    names = [etree.QName(e).localname for e in full]
    assert names.index("AdditionalDocumentReference") < names.index("Signature")

    bare = etree.fromstring(apply_profile(canonical, inv, XmlOptions(embed_xslt=False, earsiv_sending_type=False,
                                                                     uppercase_uuid=False, pretty_print=False)))
    assert adr_types(bare) == []
    assert bare.findtext("cbc:UUID", namespaces=X) == inv.uuid.lower()
    # the business content is identical
    for path in ("cbc:ID", "cac:LegalMonetaryTotal/cbc:PayableAmount", "cac:TaxTotal/cbc:TaxAmount"):
        assert full.findtext(path, namespaces=X) == bare.findtext(path, namespaces=X)
    db.session.rollback()


def test_integrator_presets_differ(app, ctx):
    inv = earsiv_invoice()
    canonical = build_invoice_xml(inv, Setting.group("company"))
    uyumsoft = REGISTRY["uyumsoft"](config={}).prepare_xml(canonical, inv)
    nilvera = REGISTRY["nilvera"](config={}).prepare_xml(canonical, inv)
    assert b"SendingType" not in uyumsoft and b">XSLT<" in uyumsoft  # delivery type goes via API metadata
    assert b"SendingType" in nilvera and b">XSLT<" not in nilvera
    # admin override wins over the preset
    overridden = REGISTRY["uyumsoft"](config={"xml_embed_xslt": "0"}).prepare_xml(canonical, inv)
    assert b">XSLT<" not in overridden
    db.session.rollback()


def test_embedded_template_renders(app, ctx):
    inv = earsiv_invoice()
    xml = apply_profile(build_invoice_xml(inv, Setting.group("company")), inv, XmlOptions())
    html = render_html(xml).decode()
    assert "e-ARŞİV FATURA" in html
    assert "ARS2026000000001" in html
    assert "3.001,20 TL" in html  # 2 x 1250,50 + 20% VAT
    assert "Hasan Kaya" in html and "Test Servis Ltd. Şti." in html
    db.session.rollback()
