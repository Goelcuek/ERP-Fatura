from datetime import datetime
from decimal import Decimal

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import or_

from ..extensions import db
from ..i18n import _
from ..models import Contact, Expense, Invoice, ServiceOrder, Transaction
from ..services.integrators import IntegratorError, get_integrator
from . import f_bool, f_str

bp = Blueprint("contacts", __name__, url_prefix="/contacts")


def _get(cid):
    c = db.session.get(Contact, cid)
    if c is None:
        abort(404)
    return c


def _fill(c):
    c.kind = f_str("kind") or "customer"
    c.is_company = f_str("is_company", "1") == "1"
    c.first_name = f_str("first_name")
    c.last_name = f_str("last_name")
    if c.is_company:
        c.name = f_str("name")
    else:
        c.name = f_str("name") or " ".join(p for p in [c.first_name, c.last_name] if p)
    c.tax_id = "".join(ch for ch in f_str("tax_id") if ch.isdigit())
    for field in ("tax_office", "address", "district", "city", "postal_code", "country", "phone", "email",
                  "contact_person", "notes"):
        setattr(c, field, f_str(field))
    c.country = c.country or "Türkiye"
    c.withholding_buyer = f_bool("withholding_buyer")
    manual_alias = f_str("efatura_alias")
    c.efatura_user = f_bool("efatura_user")
    c.efatura_alias = manual_alias if c.efatura_user else ""


def _validate(c):
    errors = []
    if not c.name:
        errors.append(_("Name is required."))
    if c.tax_id and len(c.tax_id) not in (10, 11):
        errors.append(_("VKN must be 10 digits, TCKN 11 digits."))
    if c.tax_id and len(c.tax_id) == 11 and not tckn_valid(c.tax_id):
        errors.append(_("TCKN checksum is invalid."))
    if c.tax_id:
        dup = Contact.query.filter(Contact.tax_id == c.tax_id, Contact.id != (c.id or 0)).first()
        if dup:
            errors.append(_("Another contact already uses this tax number: {name}", name=dup.name))
    return errors


def tckn_valid(t):
    if len(t) != 11 or not t.isdigit() or t[0] == "0":
        return False
    d = [int(x) for x in t]
    if ((sum(d[0:9:2]) * 7) - sum(d[1:8:2])) % 10 != d[9]:
        return False
    return sum(d[:10]) % 10 == d[10]


@bp.route("/")
def index():
    q = (request.args.get("q") or "").strip()
    kind = request.args.get("kind", "")
    show_archived = request.args.get("archived") == "1"
    query = Contact.query.filter(Contact.archived.is_(show_archived))
    if kind in ("customer", "supplier"):
        query = query.filter(Contact.kind.in_([kind, "both"]))
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Contact.name.ilike(like), Contact.tax_id.like(like), Contact.phone.like(like),
                                 Contact.city.ilike(like), Contact.email.ilike(like)))
    contacts = query.order_by(Contact.name).all()
    balances = {c.id: c.balance() for c in contacts}
    return render_template("customers/index.html", contacts=contacts, balances=balances, q=q, kind=kind,
                           show_archived=show_archived)


@bp.route("/new", methods=["GET", "POST"])
def new():
    c = Contact(kind=request.args.get("kind", "customer"), is_company=True, country="Türkiye")
    if request.method == "POST":
        _fill(c)
        errors = _validate(c)
        if not errors:
            db.session.add(c)
            db.session.commit()
            flash(_("Contact saved."), "success")
            nxt = request.args.get("next")
            if nxt == "order":
                return redirect(url_for("orders.new", contact_id=c.id))
            if nxt == "invoice":
                return redirect(url_for("invoices.new", contact_id=c.id))
            return redirect(url_for("contacts.view", cid=c.id))
        for e in errors:
            flash(e, "error")
    return render_template("customers/form.html", c=c)


@bp.route("/<int:cid>/edit", methods=["GET", "POST"])
def edit(cid):
    c = _get(cid)
    if request.method == "POST":
        with db.session.no_autoflush:
            _fill(c)
            errors = _validate(c)
            if errors:
                for e in errors:
                    flash(e, "error")
                html = render_template("customers/form.html", c=c)
                db.session.rollback()
                return html
        db.session.commit()
        flash(_("Contact saved."), "success")
        return redirect(url_for("contacts.view", cid=c.id))
    return render_template("customers/form.html", c=c)


@bp.route("/<int:cid>")
def view(cid):
    c = _get(cid)
    tab = request.args.get("tab", "orders")
    orders = ServiceOrder.query.filter_by(contact_id=c.id).order_by(ServiceOrder.received_at.desc()).all()
    invoices = Invoice.query.filter_by(contact_id=c.id).order_by(Invoice.issue_date.desc(), Invoice.id.desc()).all()
    return render_template("customers/view.html", c=c, tab=tab, orders=orders, invoices=invoices,
                           ledger=statement(c), balance=c.balance())


def statement(c):
    """Chronological account statement (cari hesap ekstresi) with running balance."""
    rows = []
    for inv in Invoice.query.filter(Invoice.contact_id == c.id, Invoice.status.in_(Invoice.POSTED)):
        sign = -1 if inv.type_code == "IADE" else 1
        rows.append({"date": inv.issue_date, "doc": inv.number, "kind": "invoice", "obj": inv,
                     "debit": inv.payable if sign > 0 else Decimal("0"),
                     "credit": inv.payable if sign < 0 else Decimal("0"),
                     "desc": _("Return invoice") if sign < 0 else _("Sales invoice")})
    for e in Expense.query.filter(Expense.contact_id == c.id):
        rows.append({"date": e.date, "doc": e.doc_no, "kind": "expense", "obj": e, "debit": Decimal("0"),
                     "credit": e.total, "desc": e.category + (f" · {e.description}" if e.description else "")})
    for t in Transaction.query.filter(Transaction.contact_id == c.id):
        rows.append({"date": t.date, "doc": "", "kind": "tx", "obj": t,
                     "debit": t.amount if t.direction == "out" else Decimal("0"),
                     "credit": t.amount if t.direction == "in" else Decimal("0"),
                     "desc": f"{t.account.name} · {t.description or ''}".strip(" ·")})
    rows.sort(key=lambda r: (r["date"], 0 if r["kind"] != "tx" else 1))
    bal = Decimal("0")
    for r in rows:
        bal += r["debit"] - r["credit"]
        r["balance"] = bal
    return rows


@bp.route("/<int:cid>/archive", methods=["POST"])
def archive(cid):
    c = _get(cid)
    c.archived = not c.archived
    db.session.commit()
    flash(_("Contact archived.") if c.archived else _("Contact restored."), "success")
    return redirect(url_for("contacts.view", cid=c.id))


@bp.route("/<int:cid>/delete", methods=["POST"])
def delete(cid):
    c = _get(cid)
    if c.invoices or c.service_orders or Transaction.query.filter_by(contact_id=c.id).first() \
            or Expense.query.filter_by(contact_id=c.id).first():
        flash(_("This contact has records and cannot be deleted. Archive it instead."), "error")
        return redirect(url_for("contacts.view", cid=c.id))
    db.session.delete(c)
    db.session.commit()
    flash(_("Contact deleted."), "success")
    return redirect(url_for("contacts.index"))


@bp.route("/<int:cid>/check-efatura", methods=["POST"])
def check_efatura(cid):
    c = _get(cid)
    if not c.tax_id:
        flash(_("Enter a VKN/TCKN first."), "error")
        return redirect(url_for("contacts.view", cid=c.id))
    try:
        aliases = get_integrator().check_user(c.tax_id)
    except IntegratorError as e:
        flash(_("e-Fatura query failed: {err}", err=_(str(e))), "error")
        return redirect(url_for("contacts.view", cid=c.id))
    c.efatura_user = bool(aliases)
    c.efatura_alias = aliases[0].alias if aliases else ""
    c.efatura_checked_at = datetime.now().replace(microsecond=0)
    db.session.commit()
    if aliases:
        flash(_("Registered e-Fatura user. Alias: {alias}", alias=c.efatura_alias), "success")
    else:
        flash(_("Not an e-Fatura user — invoices will be issued as e-Arşiv."), "info")
    return redirect(request.referrer or url_for("contacts.view", cid=c.id))


@bp.route("/api/search")
def api_search():
    q = (request.args.get("q") or "").strip()
    query = Contact.query.filter(Contact.archived.is_(False))
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Contact.name.ilike(like), Contact.tax_id.like(like), Contact.phone.like(like)))
    rows = query.order_by(Contact.name).limit(15).all()
    return jsonify([
        {"id": c.id, "name": c.name, "tax_id": c.tax_id, "phone": c.phone, "city": c.city,
         "efatura": c.efatura_user, "withholding": c.withholding_buyer}
        for c in rows
    ])

