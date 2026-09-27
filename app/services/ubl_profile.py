"""Adapt the canonical UBL-TR invoice XML to what a specific integrator expects.

The canonical XML (built by ``ubl.build_invoice_xml`` when the invoice is issued) is the
business record and never changes. Right before sending, the active integrator's
``XmlOptions`` and ``customize_xml`` hook produce the payload that is actually sent;
that version is stored next to the canonical one.
"""

import base64
import os
import uuid

from lxml import etree

from .ubl import CAC, CBC

XSLT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xslt", "invoice.xslt")


LOGO_MARKER = b"<!--LOGO-->"


def default_xslt():
    """The bundled display template, with the company logo baked in when one is set."""
    with open(XSLT_PATH, "rb") as fh:
        xslt = fh.read()
    from flask import has_app_context

    if has_app_context():
        from .branding import logo_data_uri

        uri = logo_data_uri()
        if uri:
            img = f'<img src="{uri}" alt="" style="max-height:70px;max-width:240px;display:block;margin-bottom:8px"/>'
            xslt = xslt.replace(LOGO_MARKER, img.encode())
    return xslt


def children(root, tag):
    return [c for c in root if c.tag == tag]


def _insert_before(root, new, before_tags):
    """Insert `new` before the first child whose tag is in `before_tags` (keeps UBL element order)."""
    for i, c in enumerate(root):
        if c.tag in before_tags:
            root.insert(i, new)
            return
    root.append(new)


def set_sending_type(root, enabled):
    for adr in children(root, CAC + "AdditionalDocumentReference"):
        if adr.findtext(CBC + "DocumentTypeCode") == "SendingType":
            if not enabled:
                root.remove(adr)
            return
    if enabled and root.findtext(CBC + "ProfileID") == "EARSIVFATURA":
        adr = etree.Element(CAC + "AdditionalDocumentReference")
        etree.SubElement(adr, CBC + "ID").text = "ELEKTRONIK"
        etree.SubElement(adr, CBC + "IssueDate").text = root.findtext(CBC + "IssueDate")
        etree.SubElement(adr, CBC + "DocumentTypeCode").text = "SendingType"
        etree.SubElement(adr, CBC + "DocumentType").text = "ELEKTRONIK"
        _insert_before(root, adr, {CAC + "Signature", CAC + "AccountingSupplierParty"})


def embed_xslt(root, xslt_bytes, filename):
    for adr in children(root, CAC + "AdditionalDocumentReference"):
        if adr.findtext(CBC + "DocumentType") == "XSLT":
            root.remove(adr)
    if xslt_bytes is None:
        return
    adr = etree.Element(CAC + "AdditionalDocumentReference")
    etree.SubElement(adr, CBC + "ID").text = str(uuid.uuid5(uuid.NAMESPACE_URL, filename)).upper()
    etree.SubElement(adr, CBC + "IssueDate").text = root.findtext(CBC + "IssueDate")
    etree.SubElement(adr, CBC + "DocumentType").text = "XSLT"
    att = etree.SubElement(adr, CAC + "Attachment")
    obj = etree.SubElement(att, CBC + "EmbeddedDocumentBinaryObject", {
        "characterSetCode": "UTF-8", "encodingCode": "Base64", "filename": filename, "mimeCode": "application/xml"})
    obj.text = base64.b64encode(xslt_bytes).decode()
    _insert_before(root, adr, {CAC + "Signature", CAC + "AccountingSupplierParty"})


def apply_profile(xml, invoice, options, customize=None, xslt=None):
    parser = etree.XMLParser(remove_blank_text=True)
    root = etree.fromstring(xml, parser)
    set_sending_type(root, options.earsiv_sending_type)
    number = root.findtext(CBC + "ID") or "invoice"
    embed_xslt(root, (xslt or default_xslt()) if options.embed_xslt else None, f"{number}.xslt")
    u = root.find(CBC + "UUID")
    if u is not None and u.text:
        u.text = u.text.upper() if options.uppercase_uuid else u.text.lower()
    if customize:
        customize(root, invoice)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=options.pretty_print)


def render_html(xml):
    """Render an invoice with its embedded XSLT (or the default one) — what receivers will see."""
    root = etree.fromstring(xml)
    xslt = None
    for adr in children(root, CAC + "AdditionalDocumentReference"):
        if adr.findtext(CBC + "DocumentType") == "XSLT":
            obj = adr.find(f"{CAC}Attachment/{CBC}EmbeddedDocumentBinaryObject")
            if obj is not None and obj.text:
                xslt = base64.b64decode(obj.text)
    transform = etree.XSLT(etree.fromstring(xslt or default_xslt()))
    return bytes(transform(root))
