from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func, or_

from ..extensions import db
from ..i18n import _
from ..models import (
    Contact,
    InvoiceLine,
    Product,
    ServiceOrder,
    ServiceOrderEvent,
    ServiceOrderLine,
    Setting,
    User,
)
from ..web import STATUS_LABELS
from . import Pager, f_bool, f_date, f_dec, f_int, f_str, parse_lines

bp = Blueprint("orders", __name__, url_prefix="/service")


def _get(oid):
    o = db.session.get(ServiceOrder, oid)
    if o is None:
        abort(404)
    return o


def next_order_number():
    prefix = (Setting.get("orders.prefix") or "SRV").upper()
    stem = f"{prefix}-{date.today().year}-"
    last = db.session.query(func.max(ServiceOrder.number)).filter(ServiceOrder.number.like(stem + "%")).scalar()
    seq = int(last[len(stem):]) + 1 if last else 1
    return f"{stem}{seq:05d}"


def _fill(o):
    for field in ("device_type", "brand", "model", "serial_no", "accessories", "complaint", "diagnosis",
                  "work_done", "internal_notes"):
        setattr(o, field, f_str(field))
    o.priority = f_str("priority") or "normal"
    o.promised_date = f_date("promised_date")
    o.under_warranty = f_bool("under_warranty")
    est = f_str("estimate")
    o.estimate = f_dec("estimate") if est else None
    o.technician_id = f_int("technician_id")


@bp.route("/")
def index():
    status = request.args.get("status", "open")
    q = (request.args.get("q") or "").strip()
    query = ServiceOrder.query.join(Contact)
    if status == "open":
        query = query.filter(ServiceOrder.status.in_(ServiceOrder.OPEN))
    elif status in ServiceOrder.STATUSES:
        query = query.filter(ServiceOrder.status == status)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(ServiceOrder.number.ilike(like), Contact.name.ilike(like),
                                 ServiceOrder.serial_no.ilike(like), ServiceOrder.brand.ilike(like),
                                 ServiceOrder.model.ilike(like), ServiceOrder.device_type.ilike(like)))
    orders = query.order_by(ServiceOrder.received_at.desc()).all()
    counts = dict(db.session.query(ServiceOrder.status, func.count(ServiceOrder.id)).group_by(ServiceOrder.status))
    counts["open"] = sum(v for k, v in counts.items() if k in ServiceOrder.OPEN)
    counts["all"] = sum(v for k, v in counts.items() if k in ServiceOrder.STATUSES)
    return render_template("orders/index.html", pager=Pager(orders), status=status, q=q, counts=counts)


@bp.route("/new", methods=["GET", "POST"])
def new():
    contact = db.session.get(Contact, request.values.get("contact_id", type=int) or 0)
    o = ServiceOrder(priority="normal", promised_date=date.today() + timedelta(days=3))
    if request.method == "POST":
        if contact is None:
            flash(_("Select a customer."), "error")
        else:
            _fill(o)
            if not (o.device_type or o.brand or o.model):
                flash(_("Describe the device (type, brand or model)."), "error")
            else:
                o.number = next_order_number()
                db.session.add(o)
                o.contact = contact
                o.status = "received"
                o.events.append(ServiceOrderEvent(kind="status", message="received", user_id=g.user.id))
                db.session.commit()
                flash(_("Service order {n} created.", n=o.number), "success")
                if request.form.get("print"):
                    return redirect(url_for("orders.receipt", oid=o.id, auto=1))
                return redirect(url_for("orders.view", oid=o.id))
    return render_template("orders/form.html", o=o, contact=contact, technicians=_technicians(),
                           suggestions=_suggestions())


@bp.route("/<int:oid>/edit", methods=["GET", "POST"])
def edit(oid):
    o = _get(oid)
    if request.method == "POST":
        _fill(o)
        cid = f_int("contact_id")
        if cid and cid != o.contact_id and db.session.get(Contact, cid):
            o.contact_id = cid
        db.session.commit()
        flash(_("Service order saved."), "success")
        return redirect(url_for("orders.view", oid=o.id))
    return render_template("orders/form.html", o=o, contact=o.contact, technicians=_technicians(),
                           suggestions=_suggestions())


def _technicians():
    return User.query.filter_by(active=True, is_technician=True).order_by(User.full_name).all()


def _suggestions():
    """Previously used values for quick entry (datalists)."""
    def distinct(col):
        return [r[0] for r in db.session.query(col).filter(col != "").distinct().order_by(col).limit(200)]

    return {"device_type": distinct(ServiceOrder.device_type), "brand": distinct(ServiceOrder.brand)}


@bp.route("/<int:oid>")
def view(oid):
    o = _get(oid)
    products = Product.query.filter_by(archived=False).order_by(Product.name).all()
    return render_template("orders/view.html", o=o, totals=o.totals(), products=products)


@bp.route("/<int:oid>/status", methods=["POST"])
def status(oid):
    o = _get(oid)
    new_status = f_str("status")
    if new_status not in ServiceOrder.STATUSES or new_status == o.status:
        return redirect(url_for("orders.view", oid=o.id))
    note = f_str("note")
    o.status = new_status
    if new_status == "delivered":
        o.delivered_at = datetime.now().replace(microsecond=0)
    if new_status == "awaiting_approval":
        o.customer_approved = None
    o.events.insert(0, ServiceOrderEvent(kind="status", message=new_status + (f"|{note}" if note else ""),
                                         user_id=g.user.id))
    db.session.commit()
    flash(_("Status changed to “{s}”.", s=_(STATUS_LABELS[new_status])), "success")
    return redirect(url_for("orders.view", oid=o.id))


@bp.route("/<int:oid>/approval", methods=["POST"])
def approval(oid):
    o = _get(oid)
    approved = f_str("approved") == "1"
    o.customer_approved = approved
    msg = _("Customer approved the estimate.") if approved else _("Customer declined the estimate.")
    o.events.insert(0, ServiceOrderEvent(kind="note", message=msg, user_id=g.user.id))
    if approved and o.status == "awaiting_approval":
        o.status = "in_repair"
        o.events.insert(0, ServiceOrderEvent(kind="status", message="in_repair", user_id=g.user.id))
    db.session.commit()
    flash(msg, "success")
    return redirect(url_for("orders.view", oid=o.id))


@bp.route("/<int:oid>/note", methods=["POST"])
def note(oid):
    o = _get(oid)
    msg = f_str("message")
    if msg:
        o.events.insert(0, ServiceOrderEvent(kind="note", message=msg, user_id=g.user.id))
        db.session.commit()
    return redirect(url_for("orders.view", oid=o.id) + "#activity")


@bp.route("/<int:oid>/lines", methods=["POST"])
def lines(oid):
    o = _get(oid)
    if o.invoice_id and o.invoice and not o.invoice.is_draft:
        flash(_("This order is already invoiced; parts and labour can no longer be changed."), "error")
        return redirect(url_for("orders.view", oid=o.id))
    o.lines = parse_lines(ServiceOrderLine)
    db.session.commit()
    flash(_("Parts and labour saved."), "success")
    return redirect(url_for("orders.view", oid=o.id) + "#lines")


@bp.route("/<int:oid>/invoice", methods=["POST"])
def make_invoice(oid):
    o = _get(oid)
    if o.invoice_id and o.invoice and o.invoice.status != "cancelled":
        return redirect(url_for("invoices.view", iid=o.invoice_id))
    if not o.lines:
        flash(_("Add parts or labour before creating an invoice."), "error")
        return redirect(url_for("orders.view", oid=o.id))
    from .invoices import new_invoice_for

    inv = new_invoice_for(o.contact)
    inv.lines = [
        InvoiceLine(position=i, product=l.product, description=l.description, qty=l.qty, unit=l.unit,
                    unit_price=l.unit_price, discount_rate=l.discount_rate, vat_rate=l.vat_rate)
        for i, l in enumerate(o.lines)
    ]
    extra = " ".join(p for p in [o.device_type, o.brand, o.model] if p)
    if o.serial_no:
        extra += f" · S/N {o.serial_no}"
    inv.notes = f"{extra}".strip()
    inv.recompute()
    db.session.add(inv)
    db.session.flush()
    o.invoice_id = inv.id
    o.events.insert(0, ServiceOrderEvent(kind="note", message=_("Draft invoice created."), user_id=g.user.id))
    db.session.commit()
    flash(_("Draft invoice created. Review it and issue when ready."), "success")
    return redirect(url_for("invoices.edit", iid=inv.id))


@bp.route("/<int:oid>/receipt")
def receipt(oid):
    o = _get(oid)
    return render_template("orders/receipt.html", o=o, company=Setting.group("company"),
                           terms=Setting.get("orders.terms"), auto_print=request.args.get("auto") == "1")


@bp.route("/<int:oid>/delete", methods=["POST"])
def delete(oid):
    o = _get(oid)
    if o.invoice_id and o.invoice and o.invoice.status != "cancelled":
        flash(_("Invoiced orders cannot be deleted."), "error")
        return redirect(url_for("orders.view", oid=o.id))
    db.session.delete(o)
    db.session.commit()
    flash(_("Service order deleted."), "success")
    return redirect(url_for("orders.index"))
