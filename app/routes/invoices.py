from datetime import date, timedelta

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import or_

from ..extensions import db
from ..i18n import _
from ..models import (
    EXEMPTION_CODES,
    VAT_RATES,
    WITHHOLDING_CODES,
    Account,
    Contact,
    Invoice,
    InvoiceLine,
    Product,
    Setting,
    Transaction,
)
from ..services import invoicing
from ..services.invoicing import InvoiceError
from ..services.words import amount_in_words
from . import Pager, f_date, f_dec, f_int, f_str, parse_lines, q_date

bp = Blueprint("invoices", __name__, url_prefix="/invoices")


def _get(iid):
    inv = db.session.get(Invoice, iid)
    if inv is None:
        abort(404)
    return inv


def default_profile_for(contact):
    if contact is not None and contact.efatura_user:
        return Setting.get("invoice.default_profile") or "TICARIFATURA"
    return "EARSIVFATURA"


def new_invoice_for(contact):
    due_days = int(Setting.get("invoice.default_due_days") or 0)
    inv = Invoice(
        contact=contact,
        profile=default_profile_for(contact),
        type_code="SATIS",
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=due_days) if due_days else None,
        created_by_id=g.user.id,
        notes="",
    )
    db.session.add(inv)
    if contact is not None and contact.withholding_buyer:
        inv.type_code = "TEVKIFAT"
        inv.withholding_code = "603"
        inv.withholding_rate = WITHHOLDING_CODES["603"][1]
    return inv


def _flash_errors(e):
    for m in e.messages:
        flash(_(m), "error")


@bp.route("/")
def index():
    status = request.args.get("status", "")
    q = (request.args.get("q") or "").strip()
    dfrom, dto = q_date("from"), q_date("to")
    query = Invoice.query.join(Contact)
    if status == "open":
        query = query.filter(Invoice.status.in_(Invoice.POSTED), Invoice.type_code != "IADE")
    elif status:
        query = query.filter(Invoice.status == status)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Invoice.number.ilike(like), Contact.name.ilike(like), Contact.tax_id.like(like)))
    if dfrom:
        query = query.filter(Invoice.issue_date >= dfrom)
    if dto:
        query = query.filter(Invoice.issue_date <= dto)
    invoices = query.order_by(Invoice.issue_date.desc(), Invoice.id.desc()).limit(1000).all()
    if status == "open":
        invoices = [i for i in invoices if i.open_amount > 0]
    posted = [i for i in invoices if i.status in Invoice.POSTED]
    sum_payable = sum((-i.payable if i.type_code == "IADE" else i.payable) for i in posted)
    return render_template("invoices/index.html", pager=Pager(invoices), sum_payable=sum_payable, count=len(invoices),
                           status=status, q=q, dfrom=dfrom, dto=dto)


def _fill(inv):
    cid = f_int("contact_id")
    contact = db.session.get(Contact, cid) if cid else None
    inv.contact = contact
    inv.profile = f_str("profile") if f_str("profile") in Invoice.PROFILES else "EARSIVFATURA"
    inv.type_code = f_str("type_code") if f_str("type_code") in Invoice.TYPES else "SATIS"
    inv.issue_date = f_date("issue_date", date.today())
    inv.due_date = f_date("due_date")
    inv.notes = f_str("notes")
    inv.order_ref = f_str("order_ref")
    inv.exemption_code = f_str("exemption_code")
    inv.exemption_reason = EXEMPTION_CODES.get(inv.exemption_code, "") if inv.exemption_code else ""
    if inv.type_code == "TEVKIFAT":
        inv.withholding_code = f_str("withholding_code")
        inv.withholding_rate = f_int("withholding_rate", 0) or WITHHOLDING_CODES.get(inv.withholding_code, ("", 0))[1]
    else:
        inv.withholding_code, inv.withholding_rate = "", 0
    if inv.type_code == "IADE":
        inv.return_ref_number = f_str("return_ref_number").upper()
        inv.return_ref_date = f_date("return_ref_date")
    else:
        inv.return_ref_number, inv.return_ref_date = "", None
    inv.lines = parse_lines(InvoiceLine)
    inv.recompute()


def _form(inv):
    products = Product.query.filter_by(archived=False).order_by(Product.name).all()
    return render_template("invoices/form.html", inv=inv, products=products, vat_rates=VAT_RATES,
                           withholding_codes=WITHHOLDING_CODES, exemption_codes=EXEMPTION_CODES)


def _save(inv, is_new):
    with db.session.no_autoflush:
        _fill(inv)
        errors = []
        if inv.contact is None:
            errors.append(_("Select a customer."))
        if not inv.lines:
            errors.append(_("Add at least one line."))
        if errors:
            for e in errors:
                flash(e, "error")
            html = _form(inv)
            db.session.rollback()
            return html
    if is_new:
        db.session.add(inv)
    db.session.commit()
    action = request.form.get("action")
    if action == "issue":
        return _issue_and_maybe_send(inv, send=False)
    if action == "issue_send":
        return _issue_and_maybe_send(inv, send=True)
    flash(_("Draft saved."), "success")
    return redirect(url_for("invoices.view", iid=inv.id))


@bp.route("/new", methods=["GET", "POST"])
def new():
    contact = db.session.get(Contact, request.args.get("contact_id", type=int) or 0)
    inv = new_invoice_for(contact)
    if request.method == "POST":
        return _save(inv, is_new=True)
    with db.session.no_autoflush:
        inv.lines = [InvoiceLine(description="", qty=1, unit="C62", unit_price=0,
                                 vat_rate=int(Setting.get("invoice.default_vat") or 20), discount_rate=0)]
        html = _form(inv)
    db.session.rollback()
    return html


@bp.route("/<int:iid>/edit", methods=["GET", "POST"])
def edit(iid):
    inv = _get(iid)
    if not inv.is_editable:
        flash(_("Issued invoices cannot be edited."), "error")
        return redirect(url_for("invoices.view", iid=inv.id))
    if request.method == "POST":
        return _save(inv, is_new=False)
    return _form(inv)


@bp.route("/<int:iid>")
def view(iid):
    inv = _get(iid)
    accounts = Account.query.filter_by(archived=False).order_by(Account.name).all()
    return render_template("invoices/view.html", inv=inv, accounts=accounts,
                           totals=inv.recompute() if inv.is_draft else _totals(inv))


def _totals(inv):
    from ..services.calc import compute_totals

    return compute_totals(inv.lines, inv.withholding_rate if inv.type_code == "TEVKIFAT" else 0)


def _issue_and_maybe_send(inv, send):
    try:
        invoicing.issue(inv, g.user)
    except InvoiceError as e:
        db.session.rollback()
        _flash_errors(e)
        return redirect(url_for("invoices.view", iid=inv.id))
    flash(_("Invoice {n} issued.", n=inv.number), "success")
    if send:
        return _send(inv)
    return redirect(url_for("invoices.view", iid=inv.id))


def _send(inv):
    try:
        res = invoicing.send(inv, g.user)
        flash(_("Invoice sent: {msg}", msg=_(res.message)), "success")
    except InvoiceError as e:
        _flash_errors(e)
    return redirect(url_for("invoices.view", iid=inv.id))


@bp.route("/<int:iid>/issue", methods=["POST"])
def issue(iid):
    return _issue_and_maybe_send(_get(iid), send=request.form.get("send") == "1")


@bp.route("/<int:iid>/send", methods=["POST"])
def send(iid):
    return _send(_get(iid))


@bp.route("/<int:iid>/rebuild", methods=["POST"])
def rebuild(iid):
    inv = _get(iid)
    try:
        invoicing.rebuild_xml(inv, g.user)
        flash(_("XML rebuilt. You can send it again."), "success")
    except InvoiceError as e:
        _flash_errors(e)
    return redirect(url_for("invoices.view", iid=inv.id))


@bp.route("/<int:iid>/refresh", methods=["POST"])
def refresh(iid):
    inv = _get(iid)
    try:
        invoicing.refresh_status(inv, g.user)
        flash(_("Status updated."), "success")
    except InvoiceError as e:
        _flash_errors(e)
    return redirect(url_for("invoices.view", iid=inv.id))


@bp.route("/<int:iid>/mark", methods=["POST"])
def mark(iid):
    inv = _get(iid)
    try:
        invoicing.mark_manual(inv, f_str("status"), g.user)
        flash(_("Status updated."), "success")
    except InvoiceError as e:
        _flash_errors(e)
    return redirect(url_for("invoices.view", iid=inv.id))


@bp.route("/<int:iid>/cancel", methods=["POST"])
def cancel(iid):
    inv = _get(iid)
    was_draft = inv.is_draft
    try:
        invoicing.cancel(inv, g.user, f_str("reason"))
    except InvoiceError as e:
        _flash_errors(e)
        return redirect(url_for("invoices.view", iid=iid))
    if was_draft:
        flash(_("Draft deleted."), "success")
        return redirect(url_for("invoices.index"))
    flash(_("Invoice cancelled."), "success")
    return redirect(url_for("invoices.view", iid=iid))


@bp.route("/<int:iid>/return", methods=["POST"])
def make_return(iid):
    """Create a draft return (İADE) invoice pre-filled from an issued invoice."""
    src = _get(iid)
    inv = new_invoice_for(src.contact)
    inv.profile = src.profile if src.profile != "TICARIFATURA" else "TEMELFATURA"
    inv.type_code = "IADE"
    inv.return_ref_number = src.number
    inv.return_ref_date = src.issue_date
    inv.withholding_code, inv.withholding_rate = "", 0
    inv.lines = [InvoiceLine(position=l.position, product=l.product, description=l.description, qty=l.qty,
                             unit=l.unit, unit_price=l.unit_price, discount_rate=l.discount_rate,
                             vat_rate=l.vat_rate) for l in src.lines]
    inv.recompute()
    db.session.add(inv)
    db.session.commit()
    flash(_("Draft return invoice created. Remove lines that are not returned."), "success")
    return redirect(url_for("invoices.edit", iid=inv.id))


@bp.route("/<int:iid>/pay", methods=["POST"])
def pay(iid):
    inv = _get(iid)
    acct = db.session.get(Account, f_int("account_id") or 0)
    amount = f_dec("amount")
    if acct is None or amount <= 0:
        flash(_("Choose an account and a positive amount."), "error")
        return redirect(url_for("invoices.view", iid=inv.id))
    direction = "out" if inv.type_code == "IADE" else "in"
    db.session.add(Transaction(
        date=f_date("date", date.today()), account=acct, direction=direction, amount=amount,
        kind="collection" if direction == "in" else "payment", contact_id=inv.contact_id, invoice_id=inv.id,
        description=f"{inv.number}", category=_("Sales"), created_by_id=g.user.id,
    ))
    inv.log("success", _("Payment recorded: {amt} → {acct}", amt=f"{amount:.2f}", acct=acct.name), g.user.id)
    db.session.commit()
    flash(_("Payment recorded."), "success")
    return redirect(url_for("invoices.view", iid=inv.id))


@bp.route("/<int:iid>/xml")
def xml(iid):
    inv = _get(iid)
    data = invoicing.read_xml(inv)
    if data is None:
        if not inv.is_draft:
            abort(404)
        from ..services.ubl import build_invoice_xml

        inv.number = inv.number or "TASLAK000000000"
        data = build_invoice_xml(inv, Setting.group("company"))
        db.session.rollback()
    return Response(data, mimetype="application/xml",
                    headers={"Content-Disposition": f'attachment; filename="{inv.number or inv.uuid}.xml"'})


@bp.route("/<int:iid>/print")
def print_view(iid):
    inv = _get(iid)
    return render_template("invoices/print.html", inv=inv, totals=_totals(inv), company=Setting.group("company"),
                           words=amount_in_words(inv.payable), footer=Setting.get("invoice.footer_note"))


@bp.route("/api/product/<int:pid>")
def api_product(pid):
    p = db.session.get(Product, pid)
    if p is None:
        abort(404)
    return {"id": p.id, "name": p.name, "code": p.code, "unit": p.unit, "price": f"{p.price:.2f}",
            "vat_rate": p.vat_rate, "stock": f"{p.stock_qty}"}
