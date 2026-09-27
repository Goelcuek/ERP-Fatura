from datetime import date
from decimal import Decimal as D

from lxml import etree

from app.extensions import db
from app.models import Contact, Invoice, InvoiceLine, Setting
from app.services.ubl import NS, build_invoice_xml, validate_for_issue

CBC = "{%s}" % NS["cbc"]
CAC = "{%s}" % NS["cac"]
X = {"i": NS[None], "cbc": NS["cbc"], "cac": NS["cac"]}

# UBL 2.1 Invoice sequence (subset we emit) — GİB validates the order
ORDER = ["UBLExtensions", "UBLVersionID", "CustomizationID", "ProfileID", "ID", "CopyIndicator", "UUID", "IssueDate",
         "IssueTime", "InvoiceTypeCode", "Note", "DocumentCurrencyCode", "LineCountNumeric", "OrderReference",
         "BillingReference", "AdditionalDocumentReference", "Signature", "AccountingSupplierParty",
         "AccountingCustomerParty", "PaymentMeans", "TaxTotal", "WithholdingTaxTotal", "LegalMonetaryTotal",
         "InvoiceLine"]


def make_invoice(contact, **kw):
    inv = Invoice(contact=contact, number="EFT2026000000001", issue_date=date(2026, 9, 1), due_date=date(2026, 10, 1),
                  profile=kw.pop("profile", "TICARIFATURA"), type_code=kw.pop("type_code", "SATIS"), **kw)
    inv.lines = [
        InvoiceLine(position=0, description="Rotor değişimi", qty=D("1"), unit="C62", unit_price=D("1850"), vat_rate=20,
                    discount_rate=D("10")),
        InvoiceLine(position=1, description="İşçilik", qty=D("1.5"), unit="HUR", unit_price=D("750"), vat_rate=20,
                    discount_rate=D("0")),
    ]
    inv.recompute()
    return inv


def parse(xml):
    return etree.fromstring(xml)


def local(el):
    return etree.QName(el).localname


def test_efatura_structure_and_totals(app, ctx):
    c = Contact(name="Yıldız İnşaat A.Ş.", tax_id="9870011223", tax_office="Kozyatağı", district="Ataşehir",
                city="İstanbul", efatura_user=True, efatura_alias="urn:mail:defaultpk@x")
    inv = make_invoice(c)
    root = parse(build_invoice_xml(inv, Setting.group("company")))
    names = [local(e) for e in root]
    positions = [ORDER.index(n) for n in names]
    assert positions == sorted(positions), names
    assert root.findtext("cbc:ProfileID", namespaces=X) == "TICARIFATURA"
    assert root.findtext("cbc:CustomizationID", namespaces=X) == "TR1.2"
    assert root.findtext("cbc:LineCountNumeric", namespaces=X) == "2"
    # 1850 - 10% = 1665 + 1125 = 2790 net, VAT 558
    lmt = root.find("cac:LegalMonetaryTotal", X)
    assert lmt.findtext("cbc:LineExtensionAmount", namespaces=X) == "2975.00"
    assert lmt.findtext("cbc:AllowanceTotalAmount", namespaces=X) == "185.00"
    assert lmt.findtext("cbc:TaxExclusiveAmount", namespaces=X) == "2790.00"
    assert lmt.findtext("cbc:TaxInclusiveAmount", namespaces=X) == "3348.00"
    assert lmt.findtext("cbc:PayableAmount", namespaces=X) == "3348.00"
    assert root.findtext("cac:TaxTotal/cbc:TaxAmount", namespaces=X) == "558.00"
    assert root.find("cac:TaxTotal/cbc:TaxAmount", X).get("currencyID") == "TRY"
    cust = root.find("cac:AccountingCustomerParty/cac:Party", X)
    assert cust.find("cac:PartyIdentification/cbc:ID", X).get("schemeID") == "VKN"
    assert cust.find("cac:Person", X) is None
    line = root.findall("cac:InvoiceLine", X)[1]
    assert line.find("cbc:InvoicedQuantity", X).get("unitCode") == "HUR"
    assert line.findtext("cbc:InvoicedQuantity", namespaces=X) == "1.5"
    assert root.findtext("cbc:Note", namespaces=X).startswith("Yalnız ÜçBinÜçYüzKırkSekiz TL")


def test_earsiv_individual_has_person_and_sending_type(app, ctx):
    c = Contact(name="Hasan Kaya", first_name="Hasan", last_name="Kaya", tax_id="10000000146", district="Bağcılar",
                city="İstanbul", is_company=False)
    inv = make_invoice(c, profile="EARSIVFATURA")
    root = parse(build_invoice_xml(inv, Setting.group("company")))
    cust = root.find("cac:AccountingCustomerParty/cac:Party", X)
    assert cust.find("cac:PartyIdentification/cbc:ID", X).get("schemeID") == "TCKN"
    assert cust.findtext("cac:Person/cbc:FirstName", namespaces=X) == "Hasan"
    assert cust.findtext("cac:Person/cbc:FamilyName", namespaces=X) == "Kaya"
    assert root.findtext("cac:AdditionalDocumentReference/cbc:DocumentType", namespaces=X) == "ELEKTRONIK"


def test_withholding_invoice(app, ctx):
    c = Contact(name="Belediye", tax_id="1760012345", tax_office="Beşiktaş", district="Beşiktaş", city="İstanbul",
                efatura_user=True)
    inv = make_invoice(c, type_code="TEVKIFAT", withholding_code="603", withholding_rate=70)
    root = parse(build_invoice_xml(inv, Setting.group("company")))
    assert root.findtext("cbc:InvoiceTypeCode", namespaces=X) == "TEVKIFAT"
    wt = root.find("cac:WithholdingTaxTotal", X)
    assert wt.findtext("cbc:TaxAmount", namespaces=X) == "390.60"  # 558 * 0.7
    assert wt.findtext("cac:TaxSubtotal/cac:TaxCategory/cac:TaxScheme/cbc:TaxTypeCode", namespaces=X) == "603"
    assert root.findtext("cac:LegalMonetaryTotal/cbc:PayableAmount", namespaces=X) == "2957.40"
    for line in root.findall("cac:InvoiceLine", X):
        assert line.find("cac:WithholdingTaxTotal", X) is not None
    assert inv.payable == D("2957.40")


def test_return_invoice_references_original(app, ctx):
    c = Contact(name="X Ltd", tax_id="9870011223", tax_office="A", district="B", city="C", efatura_user=True)
    inv = make_invoice(c, profile="TEMELFATURA", type_code="IADE", return_ref_number="EFT2026000000009",
                       return_ref_date=date(2026, 8, 1))
    root = parse(build_invoice_xml(inv, Setting.group("company")))
    ref = root.find("cac:BillingReference/cac:InvoiceDocumentReference", X)
    assert ref.findtext("cbc:ID", namespaces=X) == "EFT2026000000009"
    assert ref.findtext("cbc:DocumentTypeCode", namespaces=X) == "IADE"


def test_validation_rules(app, ctx):
    c = Contact(name="Not registered", tax_id="9870011223", district="A", city="B", efatura_user=False)
    inv = make_invoice(c)
    errs = validate_for_issue(inv, Setting.group("company"))
    assert any("e-Arşiv" in e for e in errs)
    inv.profile = "EARSIVFATURA"
    assert validate_for_issue(inv, Setting.group("company")) == []
    inv.lines[0].vat_rate = 0
    inv.recompute()
    assert any("exemption" in e for e in validate_for_issue(inv, Setting.group("company")))
    db.session.rollback()
