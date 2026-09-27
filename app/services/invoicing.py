"""Invoice lifecycle: draft → issued (numbered, XML frozen) → sent/exported → accepted/rejected."""

import os
from datetime import datetime

from flask import current_app
from sqlalchemy import func

from ..extensions import db
from ..i18n import _
from ..models import Invoice, Setting
from ..web import STATUS_LABELS
from .integrators import IntegratorError, get_integrator
from .ubl import build_invoice_xml, validate_for_issue


class InvoiceError(Exception):
    def __init__(self, messages):
        self.messages = messages if isinstance(messages, list) else [messages]
        super().__init__("; ".join(self.messages))


def series_prefix(profile):
    key = "invoice.earsiv_prefix" if profile == "EARSIVFATURA" else "invoice.efatura_prefix"
    prefix = (Setting.get(key) or "").upper()
    if len(prefix) != 3 or not prefix.isalnum():
        raise InvoiceError("Invoice series prefix must be exactly 3 letters/digits (Settings → Invoicing).")
    return prefix


def next_invoice_number(profile, year):
    """GİB format: 3-char series + 4-digit year + 9-digit sequence, e.g. EFT2026000000001."""
    prefix = series_prefix(profile)
    stem = f"{prefix}{year}"
    last = db.session.query(func.max(Invoice.number)).filter(Invoice.number.like(stem + "%")).scalar()
    start = int(Setting.get("invoice.earsiv_start" if profile == "EARSIVFATURA" else "invoice.efatura_start") or 1)
    seq = max(int(last[len(stem):]) + 1 if last else 1, start)
    return f"{stem}{seq:09d}"


def xml_dir():
    d = os.path.join(current_app.config["DATA_DIR"], "files", "invoices")
    os.makedirs(d, exist_ok=True)
    return d


def read_xml(inv):
    if inv.xml_path:
        path = os.path.join(current_app.config["DATA_DIR"], inv.xml_path)
        if os.path.exists(path):
            with open(path, "rb") as fh:
                return fh.read()
    return None


def apply_stock(inv, sign):
    """sign=-1 removes sold parts from stock, +1 puts them back."""
    if inv.type_code == "IADE":
        sign = -sign
    for ln in inv.lines:
        p = ln.product
        if p is not None and p.track_stock and p.kind == "part":
            p.stock_qty = p.stock_qty + sign * ln.qty


def issue(inv, user=None):
    """Validate, number and freeze the invoice XML. Does not contact the integrator."""
    if not inv.is_draft:
        raise InvoiceError("Only draft invoices can be issued.")
    company = Setting.group("company")
    inv.recompute()
    errors = validate_for_issue(inv, company)
    if errors:
        raise InvoiceError(errors)
    now = datetime.now()
    inv.issue_time = now.time().replace(microsecond=0)
    inv.number = next_invoice_number(inv.profile, inv.issue_date.year)
    xml = build_invoice_xml(inv, company)
    rel = os.path.join("files", "invoices", f"{inv.number}_{inv.uuid}.xml")
    xml_dir()
    with open(os.path.join(current_app.config["DATA_DIR"], rel), "wb") as fh:
        fh.write(xml)
    inv.xml_path = rel
    inv.status = "issued"
    apply_stock(inv, -1)
    inv.log("info", _("Issued as {n}", n=inv.number), user.id if user else None)
    for order in inv.service_orders:
        if order.status == "ready":
            order.status = "delivered"
            order.delivered_at = now.replace(microsecond=0)
    db.session.commit()
    return xml


def rebuild_xml(inv, user=None):
    """After a failed send: regenerate the XML from current customer/company data, keeping the number."""
    if inv.status != "error":
        raise InvoiceError("XML can only be rebuilt for invoices whose sending failed.")
    company = Setting.group("company")
    errors = validate_for_issue(inv, company)
    if errors:
        raise InvoiceError(errors)
    xml = build_invoice_xml(inv, company)
    with open(os.path.join(current_app.config["DATA_DIR"], inv.xml_path), "wb") as fh:
        fh.write(xml)
    inv.log("info", _("XML rebuilt from current data"), user.id if user else None)
    db.session.commit()
    return xml


def send(inv, user=None):
    """Submit an issued invoice through the configured integrator."""
    if inv.status not in ("issued", "error"):
        raise InvoiceError("Only issued invoices can be sent.")
    xml = read_xml(inv)
    if xml is None:
        raise InvoiceError("Invoice XML file is missing.")
    integ = get_integrator()
    uid = user.id if user else None
    try:
        res = integ.send(inv, xml, receiver_alias=inv.contact.efatura_alias or "")
    except IntegratorError as e:
        inv.status = "error"
        inv.status_message = str(e)
        inv.log("error", _("Send failed ({integ}): {err}", integ=_(integ.label), err=str(e)), uid)
        db.session.commit()
        raise InvoiceError(str(e))
    inv.integrator = integ.key
    inv.integrator_ref = res.reference
    inv.status = res.status
    inv.status_message = res.message
    inv.sent_at = datetime.now().replace(microsecond=0)
    inv.log("success", f"{_(integ.label)}: {_(res.message)}", uid)
    db.session.commit()
    return res


def refresh_status(inv, user=None):
    integ = get_integrator(inv.integrator or None)
    try:
        res = integ.get_status(inv)
    except IntegratorError as e:
        raise InvoiceError(str(e))
    if res.status and res.status != inv.status:
        inv.log("info", _("Status: {old} → {new}", old=_(STATUS_LABELS.get(inv.status, inv.status)),
                            new=_(STATUS_LABELS.get(res.status, res.status))) + (f". {_(res.message)}" if res.message else ""),
                user.id if user else None)
        if res.status == "rejected":
            apply_stock(inv, +1)
        inv.status = res.status
    inv.status_message = res.message or inv.status_message
    db.session.commit()
    return res


def cancel(inv, user=None, reason=""):
    uid = user.id if user else None
    if inv.status in ("draft",):
        db.session.delete(inv)
        db.session.commit()
        return None
    if inv.status == "cancelled":
        raise InvoiceError("Invoice is already cancelled.")
    if inv.status in ("sent", "accepted") and inv.integrator:
        integ = get_integrator(inv.integrator)
        try:
            integ.cancel(inv)
        except IntegratorError as e:
            raise InvoiceError(str(e))
    if inv.status != "rejected":
        apply_stock(inv, +1)
    inv.status = "cancelled"
    inv.log("info", _("Cancelled") + (f": {reason}" if reason else ""), uid)
    db.session.commit()
    return inv


def mark_manual(inv, status, user=None):
    """Manual status override used with the XML export integrator."""
    allowed = {"exported": ["sent", "accepted", "rejected"], "sent": ["accepted", "rejected"], "error": ["sent"]}
    if status not in allowed.get(inv.status, []):
        raise InvoiceError("This status change is not allowed.")
    if status == "rejected":
        apply_stock(inv, +1)
    inv.log("info", _("Status manually set: {old} → {new}", old=_(STATUS_LABELS.get(inv.status, inv.status)),
                             new=_(STATUS_LABELS.get(status, status))), user.id if user else None)
    inv.status = status
    db.session.commit()
