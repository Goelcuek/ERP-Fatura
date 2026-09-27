"""UBL-TR 1.2 (UBL 2.1) invoice XML generation for e-Fatura and e-Arşiv.

The produced document is unsigned: signing (mali mühür) is done by the special
integrator (özel entegratör) when the invoice is submitted through its API.
Element order follows the UBL 2.1 XSD sequence, which GİB validates strictly.
"""

from datetime import datetime
from decimal import Decimal

from lxml import etree

from .calc import compute_line
from .words import amount_in_words

NS = {
    None: "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "ext": "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}
CAC = "{%s}" % NS["cac"]
CBC = "{%s}" % NS["cbc"]
EXT = "{%s}" % NS["ext"]

# Placeholder TCKN GİB allows on e-Arşiv invoices for consumers who don't give an ID.
ANONYMOUS_TCKN = "11111111111"


def fmt(amount):
    return f"{Decimal(amount).quantize(Decimal('0.01')):.2f}"


def fmt_qty(q):
    q = Decimal(q).normalize()
    return format(q, "f")


class _B:
    """Tiny builder helpers."""

    def __init__(self, currency):
        self.currency = currency

    def cbc(self, parent, tag, text=None, **attrs):
        el = etree.SubElement(parent, CBC + tag, {k: str(v) for k, v in attrs.items()})
        if text is not None:
            el.text = str(text)
        return el

    def cac(self, parent, tag):
        return etree.SubElement(parent, CAC + tag)

    def amt(self, parent, tag, value):
        return self.cbc(parent, tag, fmt(value), currencyID=self.currency)


def _party(b, parent, *, tax_id, name, first_name="", last_name="", tax_office="", address="", district="",
           city="", postal_code="", country="Türkiye", phone="", email="", website="", extra_ids=None):
    party = b.cac(parent, "Party")
    if website:
        b.cbc(party, "WebsiteURI", website)
    scheme = "TCKN" if len(tax_id) == 11 else "VKN"
    pid = b.cac(party, "PartyIdentification")
    b.cbc(pid, "ID", tax_id, schemeID=scheme)
    for sid, val in (extra_ids or {}).items():
        if val:
            pid = b.cac(party, "PartyIdentification")
            b.cbc(pid, "ID", val, schemeID=sid)
    if scheme == "VKN" or name:
        pn = b.cac(party, "PartyName")
        b.cbc(pn, "Name", name)
    addr = b.cac(party, "PostalAddress")
    b.cbc(addr, "StreetName", address or "-")
    b.cbc(addr, "CitySubdivisionName", district or "-")
    b.cbc(addr, "CityName", city or "-")
    if postal_code:
        b.cbc(addr, "PostalZone", postal_code)
    c = b.cac(addr, "Country")
    b.cbc(c, "Name", country or "Türkiye")
    if tax_office:
        pts = b.cac(party, "PartyTaxScheme")
        ts = b.cac(pts, "TaxScheme")
        b.cbc(ts, "Name", tax_office)
    if phone or email:
        contact = b.cac(party, "Contact")
        if phone:
            b.cbc(contact, "Telephone", phone)
        if email:
            b.cbc(contact, "ElectronicMail", email)
    if scheme == "TCKN":
        person = b.cac(party, "Person")
        fn, ln = first_name, last_name
        if not (fn and ln):
            bits = (name or "-").rsplit(" ", 1)
            fn, ln = (bits[0], bits[1]) if len(bits) == 2 else (bits[0], "-")
        b.cbc(person, "FirstName", fn)
        b.cbc(person, "FamilyName", ln)
    return party


def _tax_subtotal(b, parent, base, vat, rate, exemption_code="", exemption_reason=""):
    st = b.cac(parent, "TaxSubtotal")
    b.amt(st, "TaxableAmount", base)
    b.amt(st, "TaxAmount", vat)
    b.cbc(st, "Percent", str(rate))
    cat = b.cac(st, "TaxCategory")
    if int(rate) == 0 and exemption_code:
        b.cbc(cat, "TaxExemptionReasonCode", exemption_code)
        b.cbc(cat, "TaxExemptionReason", exemption_reason or exemption_code)
    scheme = b.cac(cat, "TaxScheme")
    b.cbc(scheme, "Name", "KDV")
    b.cbc(scheme, "TaxTypeCode", "0015")


def _withholding(b, parent, vat_base, withheld, rate, code, reason):
    wt = b.cac(parent, "WithholdingTaxTotal")
    b.amt(wt, "TaxAmount", withheld)
    st = b.cac(wt, "TaxSubtotal")
    b.amt(st, "TaxableAmount", vat_base)
    b.amt(st, "TaxAmount", withheld)
    b.cbc(st, "Percent", str(rate))
    cat = b.cac(st, "TaxCategory")
    scheme = b.cac(cat, "TaxScheme")
    b.cbc(scheme, "Name", reason or "KDV TEVKİFAT")
    b.cbc(scheme, "TaxTypeCode", code)


def build_invoice_xml(inv, company, *, xslt=None):
    """Return UBL-TR XML bytes for an Invoice model instance.

    `company` is the settings group dict returned by Setting.group("company").
    """
    from ..models import WITHHOLDING_CODES

    cur = inv.currency or "TRY"
    b = _B(cur)
    root = etree.Element("Invoice", nsmap=NS)
    root.set(
        "{%s}schemaLocation" % NS["xsi"],
        "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2 UBL-Invoice-2.1.xsd",
    )

    exts = etree.SubElement(root, EXT + "UBLExtensions")
    ext = etree.SubElement(exts, EXT + "UBLExtension")
    etree.SubElement(ext, EXT + "ExtensionContent")

    type_code = inv.type_code
    wh_rate = inv.withholding_rate if type_code == "TEVKIFAT" else 0

    b.cbc(root, "UBLVersionID", "2.1")
    b.cbc(root, "CustomizationID", "TR1.2")
    b.cbc(root, "ProfileID", inv.profile)
    b.cbc(root, "ID", inv.number)
    b.cbc(root, "CopyIndicator", "false")
    b.cbc(root, "UUID", inv.uuid.upper())
    b.cbc(root, "IssueDate", inv.issue_date.isoformat())
    b.cbc(root, "IssueTime", (inv.issue_time or datetime.now().time()).strftime("%H:%M:%S"))
    b.cbc(root, "InvoiceTypeCode", type_code)

    # Notes: amount in words first (customary), then free text
    b.cbc(root, "Note", amount_in_words(inv.payable, "TL" if cur == "TRY" else cur))
    for line in (inv.notes or "").splitlines():
        if line.strip():
            b.cbc(root, "Note", line.strip())
    if inv.service_orders:
        b.cbc(root, "Note", "Servis No: " + ", ".join(o.number for o in inv.service_orders))

    b.cbc(root, "DocumentCurrencyCode", cur)
    b.cbc(root, "LineCountNumeric", len(inv.lines))

    if inv.order_ref:
        oref = b.cac(root, "OrderReference")
        b.cbc(oref, "ID", inv.order_ref)
        b.cbc(oref, "IssueDate", inv.issue_date.isoformat())

    if type_code == "IADE" and inv.return_ref_number:
        br = b.cac(root, "BillingReference")
        idr = b.cac(br, "InvoiceDocumentReference")
        b.cbc(idr, "ID", inv.return_ref_number)
        b.cbc(idr, "IssueDate", (inv.return_ref_date or inv.issue_date).isoformat())
        b.cbc(idr, "DocumentTypeCode", "IADE")

    if inv.profile == "EARSIVFATURA":
        adr = b.cac(root, "AdditionalDocumentReference")
        b.cbc(adr, "ID", "ELEKTRONIK")
        b.cbc(adr, "IssueDate", inv.issue_date.isoformat())
        b.cbc(adr, "DocumentTypeCode", "SendingType")
        b.cbc(adr, "DocumentType", "ELEKTRONIK")

    if xslt:
        adr = b.cac(root, "AdditionalDocumentReference")
        b.cbc(adr, "ID", inv.uuid.upper())
        b.cbc(adr, "IssueDate", inv.issue_date.isoformat())
        b.cbc(adr, "DocumentType", "XSLT")
        att = b.cac(adr, "Attachment")
        import base64

        b.cbc(att, "EmbeddedDocumentBinaryObject", base64.b64encode(xslt).decode(),
              characterSetCode="UTF-8", encodingCode="Base64", filename=f"{inv.number}.xslt",
              mimeCode="application/xml")

    # Signature block (the integrator applies the actual XAdES signature)
    sig = b.cac(root, "Signature")
    b.cbc(sig, "ID", company["tax_id"], schemeID="VKN_TCKN")
    sp = b.cac(sig, "SignatoryParty")
    spid = b.cac(sp, "PartyIdentification")
    b.cbc(spid, "ID", company["tax_id"], schemeID="VKN" if len(company["tax_id"]) == 10 else "TCKN")
    spa = b.cac(sp, "PostalAddress")
    b.cbc(spa, "StreetName", company.get("address") or "-")
    b.cbc(spa, "CitySubdivisionName", company.get("district") or "-")
    b.cbc(spa, "CityName", company.get("city") or "-")
    spc = b.cac(spa, "Country")
    b.cbc(spc, "Name", company.get("country") or "Türkiye")
    dsa = b.cac(sig, "DigitalSignatureAttachment")
    er = b.cac(dsa, "ExternalReference")
    b.cbc(er, "URI", "#Signature_" + inv.number)

    sup = b.cac(root, "AccountingSupplierParty")
    _party(
        b, sup,
        tax_id=company["tax_id"], name=company["name"], tax_office=company.get("tax_office", ""),
        address=company.get("address", ""), district=company.get("district", ""), city=company.get("city", ""),
        postal_code=company.get("postal_code", ""), country=company.get("country", "Türkiye"),
        phone=company.get("phone", ""), email=company.get("email", ""), website=company.get("website", ""),
        extra_ids={"MERSISNO": company.get("mersis", ""), "TICARETSICILNO": company.get("trade_registry", "")},
    )

    c = inv.contact
    cus = b.cac(root, "AccountingCustomerParty")
    _party(
        b, cus,
        tax_id=c.tax_id or ANONYMOUS_TCKN, name=c.name, first_name=c.first_name or "", last_name=c.last_name or "",
        tax_office=c.tax_office or "", address=c.address or "", district=c.district or "", city=c.city or "",
        postal_code=c.postal_code or "", country=c.country or "Türkiye", phone=c.phone or "", email=c.email or "",
    )

    if inv.due_date:
        pm = b.cac(root, "PaymentMeans")
        b.cbc(pm, "PaymentMeansCode", "1")  # instrument not defined
        b.cbc(pm, "PaymentDueDate", inv.due_date.isoformat())
        if company.get("iban"):
            acct = b.cac(pm, "PayeeFinancialAccount")
            b.cbc(acct, "ID", company["iban"].replace(" ", ""))
            b.cbc(acct, "CurrencyCode", cur)

    # compute per-line values once
    calcs = [compute_line(l.qty, l.unit_price, l.discount_rate or 0, l.vat_rate, wh_rate) for l in inv.lines]

    # Document TaxTotal grouped by rate
    groups = {}
    for ln, cl in zip(inv.lines, calcs):
        g = groups.setdefault(int(ln.vat_rate), [Decimal("0"), Decimal("0"), Decimal("0")])
        g[0] += cl.net
        g[1] += cl.vat
        g[2] += cl.withholding
    tt = b.cac(root, "TaxTotal")
    b.amt(tt, "TaxAmount", sum(g[1] for g in groups.values()))
    for rate, (base, vat, _) in sorted(groups.items()):
        _tax_subtotal(b, tt, base, vat, rate, inv.exemption_code, inv.exemption_reason)

    wh_reason = ""
    if wh_rate:
        wh_reason = WITHHOLDING_CODES.get(inv.withholding_code, ("KDV TEVKİFAT", 0))[0]
        wt_total = sum(g[2] for g in groups.values())
        vat_base = sum(g[1] for g in groups.values())
        _withholding(b, root, vat_base, wt_total, wh_rate, inv.withholding_code, wh_reason)

    gross = sum(cl.gross for cl in calcs)
    discount = sum(cl.discount for cl in calcs)
    net = sum(cl.net for cl in calcs)
    vat = sum(cl.vat for cl in calcs)
    withheld = sum(cl.withholding for cl in calcs)
    lmt = b.cac(root, "LegalMonetaryTotal")
    b.amt(lmt, "LineExtensionAmount", gross)
    b.amt(lmt, "TaxExclusiveAmount", net)
    b.amt(lmt, "TaxInclusiveAmount", net + vat)
    b.amt(lmt, "AllowanceTotalAmount", discount)
    b.amt(lmt, "PayableAmount", net + vat - withheld)

    for i, (ln, cl) in enumerate(zip(inv.lines, calcs), start=1):
        il = b.cac(root, "InvoiceLine")
        b.cbc(il, "ID", i)
        b.cbc(il, "InvoicedQuantity", fmt_qty(ln.qty), unitCode=ln.unit or "C62")
        b.amt(il, "LineExtensionAmount", cl.net)
        if cl.discount:
            ac = b.cac(il, "AllowanceCharge")
            b.cbc(ac, "ChargeIndicator", "false")
            b.cbc(ac, "MultiplierFactorNumeric", fmt(Decimal(ln.discount_rate) / 100))
            b.amt(ac, "Amount", cl.discount)
            b.amt(ac, "BaseAmount", cl.gross)
        ltt = b.cac(il, "TaxTotal")
        b.amt(ltt, "TaxAmount", cl.vat)
        _tax_subtotal(b, ltt, cl.net, cl.vat, int(ln.vat_rate), inv.exemption_code, inv.exemption_reason)
        if wh_rate:
            _withholding(b, il, cl.vat, cl.withholding, wh_rate, inv.withholding_code, wh_reason)
        item = b.cac(il, "Item")
        b.cbc(item, "Name", ln.description)
        if ln.product is not None and ln.product.code:
            sii = b.cac(item, "SellersItemIdentification")
            b.cbc(sii, "ID", ln.product.code)
        price = b.cac(il, "Price")
        b.amt(price, "PriceAmount", ln.unit_price)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def validate_for_issue(inv, company):
    """Business-rule checks before an invoice can be issued. Returns list of error strings (English keys)."""
    errors = []
    if not company.get("name") or len(company.get("tax_id", "")) not in (10, 11):
        errors.append("Company name and a valid VKN/TCKN must be set in Settings.")
    if not company.get("tax_office"):
        errors.append("Company tax office must be set in Settings.")
    if not company.get("city") or not company.get("district"):
        errors.append("Company city and district must be set in Settings.")
    if not inv.lines:
        errors.append("Invoice has no lines.")
    c = inv.contact
    if inv.profile != "EARSIVFATURA":
        if len(c.tax_id or "") not in (10, 11):
            errors.append("Customer needs a valid VKN/TCKN for e-Fatura.")
        if not c.efatura_user:
            errors.append("Customer is not registered as an e-Fatura user; use e-Arşiv instead.")
    elif c.tax_id and len(c.tax_id) not in (10, 11):
        errors.append("Customer VKN must be 10 digits or TCKN 11 digits.")
    if inv.type_code == "IADE" and not inv.return_ref_number:
        errors.append("Return invoices must reference the original invoice number.")
    if inv.type_code == "TEVKIFAT" and (not inv.withholding_code or not inv.withholding_rate):
        errors.append("Withholding invoices need a withholding code and rate.")
    has_zero = any(int(l.vat_rate) == 0 for l in inv.lines)
    if has_zero and not inv.exemption_code:
        errors.append("Lines with 0% VAT require a VAT exemption code.")
    if inv.payable <= 0:
        errors.append("Invoice total must be greater than zero.")
    return errors
