"""Business operations shared by the web pages and the assistant.

Every function takes the acting user and a `source` ("web" or "assistant") so the
activity log shows how a change was made. Callers commit.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func

from ..extensions import db
from ..i18n import _
from ..models import (
    WITHHOLDING_CODES,
    Account,
    Contact,
    Expense,
    Invoice,
    InvoiceLine,
    Product,
    ServiceOrder,
    ServiceOrderEvent,
    ServiceOrderLine,
    Setting,
    Transaction,
)
from .calc import r2, split_gross, to_decimal


class WorkshopError(Exception):
    pass


def _uid(user):
    return user.id if user is not None else None


# ---------------------------------------------------------------- service orders

def next_order_number():
    prefix = (Setting.get("orders.prefix") or "SRV").upper()
    stem = f"{prefix}-{date.today().year}-"
    last = db.session.query(func.max(ServiceOrder.number)).filter(ServiceOrder.number.like(stem + "%")).scalar()
    seq = int(last[len(stem):]) + 1 if last else 1
    return f"{stem}{seq:05d}"


def log_order(order, user, message, kind="note", source="web"):
    order.events.insert(0, ServiceOrderEvent(kind=kind, message=message, user_id=_uid(user), source=source))


def change_status(order, new_status, user, note="", source="web"):
    """Returns the previous status (for undo)."""
    if new_status not in ServiceOrder.STATUSES:
        raise WorkshopError(f"Unknown status: {new_status}")
    old = order.status
    if new_status == old:
        return old
    order.status = new_status
    if new_status == "delivered":
        order.delivered_at = datetime.now().replace(microsecond=0)
    elif old == "delivered":
        order.delivered_at = None
    if new_status == "awaiting_approval":
        order.customer_approved = None
    log_order(order, user, new_status + (f"|{note}" if note else ""), kind="status", source=source)
    return old


def append_work_done(order, text):
    text = (text or "").strip()
    if text:
        order.work_done = (order.work_done + "\n" if order.work_done else "") + text


def is_locked(order):
    return bool(order.invoice_id and order.invoice and not order.invoice.is_draft
                and order.invoice.status != "cancelled")


def add_lines(order, items, user, source="web"):
    """items: dicts with description, qty, unit_price, unit, vat_rate, product (optional)."""
    if is_locked(order):
        raise WorkshopError("This order is already invoiced; parts and labour can no longer be changed.")
    pos = len(order.lines)
    added = []
    for it in items:
        p = it.get("product")
        line = ServiceOrderLine(
            position=pos, product=p,
            description=(it.get("description") or (p.name if p else "")).strip()[:300],
            qty=to_decimal(it.get("qty") or 1, "1"),
            unit=it.get("unit") or (p.unit if p else "C62"),
            unit_price=to_decimal(it["unit_price"]) if it.get("unit_price") not in (None, "") else (p.price if p else Decimal("0")),
            discount_rate=Decimal("0"),
            vat_rate=int(it["vat_rate"]) if it.get("vat_rate") not in (None, "") else (p.vat_rate if p else 20),
        )
        if not line.description:
            raise WorkshopError("Each line needs a description or a catalogue item.")
        order.lines.append(line)
        added.append(line)
        pos += 1
    if added:
        log_order(order, user, _("Added: {items}", items=", ".join(f"{l.qty.normalize():f} × {l.description}" for l in added)),
                  source=source)
    return added


def create_order(contact, user, source="web", **fields):
    o = ServiceOrder(priority=fields.pop("priority", None) or "normal", number=next_order_number(), status="received")
    for k, v in fields.items():
        setattr(o, k, v)
    if o.promised_date is None:
        o.promised_date = date.today() + timedelta(days=3)
    db.session.add(o)
    o.contact = contact
    o.events.append(ServiceOrderEvent(kind="status", message="received", user_id=_uid(user), source=source))
    return o


# ---------------------------------------------------------------- invoices

def default_profile_for(contact):
    if contact is not None and contact.efatura_user:
        return Setting.get("invoice.default_profile") or "TICARIFATURA"
    return "EARSIVFATURA"


def new_invoice_for(contact, user):
    due_days = int(Setting.get("invoice.default_due_days") or 0)
    inv = Invoice(
        contact=contact,
        profile=default_profile_for(contact),
        type_code="SATIS",
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=due_days) if due_days else None,
        created_by_id=_uid(user),
        notes="",
    )
    db.session.add(inv)
    if contact is not None and contact.withholding_buyer:
        inv.type_code = "TEVKIFAT"
        inv.withholding_code = "603"
        inv.withholding_rate = WITHHOLDING_CODES["603"][1]
    return inv


def draft_invoice_from_order(order, user, source="web"):
    if order.invoice_id and order.invoice and order.invoice.status != "cancelled":
        return order.invoice, False
    if not order.lines:
        raise WorkshopError("Add parts or labour before creating an invoice.")
    inv = new_invoice_for(order.contact, user)
    inv.lines = [
        InvoiceLine(position=i, product=l.product, description=l.description, qty=l.qty, unit=l.unit,
                    unit_price=l.unit_price, discount_rate=l.discount_rate, vat_rate=l.vat_rate)
        for i, l in enumerate(order.lines)
    ]
    extra = " ".join(p for p in [order.device_type, order.brand, order.model] if p)
    if order.serial_no:
        extra += f" · S/N {order.serial_no}"
    inv.notes = extra.strip()
    inv.recompute()
    db.session.flush()
    order.invoice_id = inv.id
    log_order(order, user, _("Draft invoice created."), source=source)
    return inv, True


def record_invoice_payment(inv, account, amount, user, on=None, source="web"):
    amount = to_decimal(amount)
    if account is None or amount <= 0:
        raise WorkshopError("Choose an account and a positive amount.")
    if inv.status not in Invoice.POSTED:
        raise WorkshopError("Payments can only be recorded on issued invoices.")
    direction = "out" if inv.type_code == "IADE" else "in"
    t = Transaction(date=on or date.today(), account=account, direction=direction, amount=amount,
                    kind="collection" if direction == "in" else "payment", contact_id=inv.contact_id,
                    invoice_id=inv.id, description=f"{inv.number}", category=_("Sales"), created_by_id=_uid(user))
    db.session.add(t)
    inv.log("success", _("Payment recorded: {amt} → {acct}", amt=f"{amount:.2f}", acct=account.name), _uid(user))
    return t


# ---------------------------------------------------------------- books & stock

def create_expense(user, *, category, amount, includes_vat, vat_rate, description="", contact=None, doc_no="",
                   on=None, pay_account=None):
    e = Expense(date=on or date.today(), contact=contact, doc_no=doc_no, category=category or "Diğer",
                description=description, vat_rate=int(vat_rate), created_by_id=_uid(user))
    amount = to_decimal(amount)
    if includes_vat:
        e.net, e.vat = split_gross(amount, e.vat_rate)
    else:
        e.net = r2(amount)
        e.vat = r2(amount * e.vat_rate / 100)
    e.total = e.net + e.vat
    if e.total <= 0:
        raise WorkshopError("Amount must be greater than zero.")
    db.session.add(e)
    db.session.flush()
    if pay_account is not None:
        db.session.add(Transaction(date=e.date, account=pay_account, direction="out", amount=e.total, kind="expense",
                                   contact_id=e.contact_id, expense_id=e.id, category=e.category,
                                   description=e.description or e.doc_no, created_by_id=_uid(user)))
    return e


def adjust_stock(product, change):
    change = to_decimal(change)
    if not product.track_stock:
        raise WorkshopError("Stock is not tracked for this item.")
    product.stock_qty = product.stock_qty + change
    return product.stock_qty


def accounts():
    return Account.query.filter_by(archived=False).order_by(Account.name).all()


def find_contact(cid):
    return db.session.get(Contact, cid) if cid else None


def find_product(pid):
    return db.session.get(Product, pid) if pid else None
