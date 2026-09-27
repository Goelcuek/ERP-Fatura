"""Tools the assistant can use, grouped by risk:

* read    – look things up, never change data
* write   – everyday workshop updates; run immediately, logged as "via assistant", undoable in the UI
* confirm – legal or financial effect; the user must tap “Confirm” before it runs

Descriptions and schemas are deliberately short and flat: the target is a small local model.
All inputs are validated here; the model is never trusted with IDs it has not looked up.
"""

from dataclasses import dataclass, field
from datetime import date, datetime

from flask import url_for
from sqlalchemy import or_

from ...extensions import db
from ...i18n import _
from ...models import (
    Account,
    Contact,
    Expense,
    Invoice,
    Product,
    ServiceOrder,
    User,
)
from ...web import STATUS_LABELS, fmt_money
from .. import invoicing, workshop
from ..calc import to_decimal


class ToolError(Exception):
    pass


@dataclass
class Context:
    user: User
    page_order_id: int = None
    page_customer_id: int = None
    page_invoice_id: int = None
    links: list = field(default_factory=list)
    changed: bool = False
    undo: list = field(default_factory=list)

    def link(self, label, url):
        if {"label": label, "url": url} not in self.links:
            self.links.append({"label": label, "url": url})


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    risk: str
    fn: object
    confirm_text: object = None

    def spec(self):
        return {"name": self.name, "description": self.description, "parameters": self.parameters}


TOOLS = {}


def tool(name, description, properties=None, required=(), risk="read", confirm_text=None):
    def deco(fn):
        TOOLS[name] = Tool(name, description, {"type": "object", "properties": properties or {},
                                               "required": list(required)}, risk, fn, confirm_text)
        return fn
    return deco


# ---------------------------------------------------------------- helpers

def _int(v, what):
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ToolError(f"{what} must be a number")


def get_order(ref):
    """Accepts an id (240), a sequence (“240”) or a full number (SRV-2026-00240)."""
    if ref is None or ref == "":
        raise ToolError("order is required")
    s = str(ref).strip().upper()
    if s.isdigit():
        o = db.session.get(ServiceOrder, int(s))
        if o is None:
            o = ServiceOrder.query.filter(ServiceOrder.number.like(f"%-{int(s):05d}")).order_by(
                ServiceOrder.id.desc()).first()
    else:
        o = ServiceOrder.query.filter(ServiceOrder.number == s).first()
    if o is None:
        raise ToolError(f"Service order {ref} not found. Use find_orders first.")
    return o


def order_brief(o):
    return {"id": o.id, "number": o.number, "device": f"{o.device_type} {o.device_label}".strip(),
            "customer": o.contact.name, "status": o.status, "status_label": _(STATUS_LABELS[o.status]),
            "technician": o.technician.full_name if o.technician else None,
            "promised": o.promised_date.isoformat() if o.promised_date else None}


def get_contact(cid):
    c = db.session.get(Contact, _int(cid, "customer_id"))
    if c is None:
        raise ToolError("Customer not found. Use find_customers first.")
    return c


def get_invoice(iid):
    inv = db.session.get(Invoice, _int(iid, "invoice_id"))
    if inv is None:
        raise ToolError("Invoice not found. Use find_invoices first.")
    return inv


def invoice_brief(i):
    return {"id": i.id, "number": i.number or "draft", "customer": i.contact.name, "date": i.issue_date.isoformat(),
            "total": fmt_money(i.payable), "open": fmt_money(i.open_amount), "status": i.status,
            "overdue": i.is_overdue}


def _date(v):
    if not v:
        return None
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except ValueError:
        raise ToolError("dates must be YYYY-MM-DD")


STATUS_ENUM = list(ServiceOrder.STATUSES)

# ---------------------------------------------------------------- read tools


@tool("find_orders", "Find service (repair) orders. Use mine=true for the current user's jobs.",
      {"query": {"type": "string", "description": "order no, customer, brand, model or serial"},
       "status": {"type": "string", "enum": ["open"] + STATUS_ENUM + ["all"]},
       "mine": {"type": "boolean"}})
def find_orders(ctx, query="", status="open", mine=False):
    q = ServiceOrder.query.join(Contact)
    if status in (None, "", "open"):
        q = q.filter(ServiceOrder.status.in_(ServiceOrder.OPEN))
    elif status != "all":
        if status not in STATUS_ENUM:
            raise ToolError("unknown status")
        q = q.filter(ServiceOrder.status == status)
    if mine:
        q = q.filter(ServiceOrder.technician_id == ctx.user.id)
    if query:
        like = f"%{query}%"
        q = q.filter(or_(ServiceOrder.number.ilike(like), Contact.name.ilike(like), ServiceOrder.brand.ilike(like),
                         ServiceOrder.model.ilike(like), ServiceOrder.serial_no.ilike(like),
                         ServiceOrder.device_type.ilike(like)))
    rows = q.order_by(ServiceOrder.promised_date.is_(None), ServiceOrder.promised_date).limit(15).all()
    return {"count": len(rows), "orders": [order_brief(o) for o in rows]}


@tool("get_order", "Details of one service order, including parts and labour.",
      {"order": {"type": "string", "description": "order id or number"}}, ["order"])
def get_order_tool(ctx, order):
    o = get_order(order)
    t = o.totals()
    ctx.link(o.number, url_for("orders.view", oid=o.id))
    return {**order_brief(o), "serial_no": o.serial_no, "complaint": o.complaint, "diagnosis": o.diagnosis,
            "work_done": o.work_done, "accessories": o.accessories,
            "lines": [{"description": l.description, "qty": f"{l.qty.normalize():f}", "unit_price": fmt_money(l.unit_price)}
                      for l in o.lines],
            "total_incl_vat": fmt_money(t.payable), "invoice": o.invoice.number if o.invoice else None}


@tool("find_customers", "Find customers or suppliers by name, tax number or phone.",
      {"query": {"type": "string"}}, ["query"])
def find_customers(ctx, query):
    like = f"%{query}%"
    rows = Contact.query.filter(Contact.archived.is_(False), or_(
        Contact.name.ilike(like), Contact.tax_id.like(like), Contact.phone.like(like))).limit(10).all()
    return {"count": len(rows), "customers": [{"id": c.id, "name": c.name, "phone": c.phone, "tax_id": c.tax_id,
                                               "city": c.city} for c in rows]}


@tool("get_customer", "A customer's details, balance, open invoices and recent orders.",
      {"customer_id": {"type": "integer"}}, ["customer_id"])
def get_customer(ctx, customer_id):
    c = get_contact(customer_id)
    ctx.link(c.name, url_for("contacts.view", cid=c.id))
    bal = c.balance()
    open_inv = [invoice_brief(i) for i in c.invoices if i.open_amount > 0]
    return {"id": c.id, "name": c.name, "phone": c.phone, "email": c.email, "tax_id": c.tax_id,
            "balance": fmt_money(bal), "balance_meaning": "customer owes us" if bal > 0 else (
                "we owe them" if bal < 0 else "settled"),
            "open_invoices": open_inv[:10], "recent_orders": [order_brief(o) for o in c.service_orders[-5:]]}


@tool("find_parts", "Find parts and services in the catalogue with price and stock.",
      {"query": {"type": "string"}}, ["query"])
def find_parts(ctx, query):
    like = f"%{query}%"
    rows = Product.query.filter(Product.archived.is_(False),
                                or_(Product.name.ilike(like), Product.code.ilike(like))).limit(10).all()
    return {"count": len(rows), "items": [{"id": p.id, "code": p.code, "name": p.name, "price_excl_vat": fmt_money(p.price),
                                           "vat": p.vat_rate, "kind": p.kind,
                                           "stock": f"{p.stock_qty.normalize():f}" if p.track_stock else None}
                                          for p in rows]}


@tool("find_invoices", "Find invoices. status: unpaid, overdue, draft or all.",
      {"customer_id": {"type": "integer"},
       "status": {"type": "string", "enum": ["unpaid", "overdue", "draft", "all"]}})
def find_invoices(ctx, customer_id=None, status="unpaid"):
    q = Invoice.query
    if customer_id:
        q = q.filter(Invoice.contact_id == _int(customer_id, "customer_id"))
    if status == "draft":
        q = q.filter(Invoice.status == "draft")
    elif status in ("unpaid", "overdue"):
        q = q.filter(Invoice.status.in_(Invoice.POSTED), Invoice.type_code != "IADE")
    rows = q.order_by(Invoice.issue_date.desc()).limit(200).all()
    if status == "unpaid":
        rows = [i for i in rows if i.open_amount > 0]
    elif status == "overdue":
        rows = [i for i in rows if i.is_overdue]
    return {"count": len(rows), "invoices": [invoice_brief(i) for i in rows[:15]]}


@tool("business_summary", "Today's overview: open jobs by status, sales this month, receivables, cash.")
def business_summary(ctx):
    from ...routes.dashboard import monthly_series

    counts = {}
    for o in ServiceOrder.query.filter(ServiceOrder.status.in_(ServiceOrder.OPEN)):
        counts[_(STATUS_LABELS[o.status])] = counts.get(_(STATUS_LABELS[o.status]), 0) + 1
    month = monthly_series(1)[0]
    posted = Invoice.query.filter(Invoice.status.in_(Invoice.POSTED), Invoice.type_code != "IADE").all()
    receivable = sum((i.open_amount for i in posted), to_decimal(0))
    overdue = sum((i.open_amount for i in posted if i.is_overdue), to_decimal(0))
    cash = sum((a.balance() for a in Account.query.filter_by(archived=False)), to_decimal(0))
    due_today = ServiceOrder.query.filter(ServiceOrder.status.in_(ServiceOrder.OPEN),
                                          ServiceOrder.promised_date <= date.today()).count()
    return {"open_orders_by_status": counts, "orders_due_today_or_late": due_today,
            "sales_this_month_excl_vat": fmt_money(month["income"]), "expenses_this_month": fmt_money(month["expense"]),
            "receivables": fmt_money(receivable), "overdue": fmt_money(overdue), "cash_and_bank": fmt_money(cash)}


@tool("list_accounts", "Cash and bank accounts (needed to record payments).")
def list_accounts(ctx):
    return {"accounts": [{"id": a.id, "name": a.name, "kind": a.kind} for a in workshop.accounts()]}


# ---------------------------------------------------------------- write tools (immediate, undoable)


@tool("set_order_status", "Change a service order's status. ready = repair finished; delivered = customer took it.",
      {"order": {"type": "string", "description": "order id or number"},
       "status": {"type": "string", "enum": STATUS_ENUM},
       "work_done": {"type": "string", "description": "what was repaired, if the user said"}},
      ["order", "status"], risk="write")
def set_order_status(ctx, order, status, work_done=""):
    o = get_order(order)
    if status not in STATUS_ENUM:
        raise ToolError("unknown status")
    if status == "cancelled":
        raise ToolError("Cancelling must be done on the order page.")
    old = workshop.change_status(o, status, ctx.user, source="assistant")
    workshop.append_work_done(o, work_done)
    ctx.changed = True
    ctx.link(o.number, url_for("orders.view", oid=o.id))
    if old != status:
        ctx.undo.append({"type": "status", "order_id": o.id, "status": old,
                         "label": f"{o.number}: {_(STATUS_LABELS[old])}"})
    return {"ok": True, "order": o.number, "old_status": old, "new_status": status}


@tool("add_order_note", "Add a note to a service order's activity log.",
      {"order": {"type": "string"}, "note": {"type": "string"}}, ["order", "note"], risk="write")
def add_order_note(ctx, order, note):
    o = get_order(order)
    if not (note or "").strip():
        raise ToolError("note is empty")
    workshop.log_order(o, ctx.user, note.strip(), source="assistant")
    ctx.changed = True
    ctx.link(o.number, url_for("orders.view", oid=o.id))
    return {"ok": True, "order": o.number}


@tool("add_order_parts", "Add used parts or labour to a service order. Look up part_id with find_parts first.",
      {"order": {"type": "string"},
       "part_id": {"type": "integer", "description": "catalogue id, optional"},
       "description": {"type": "string"},
       "quantity": {"type": "number"},
       "unit_price": {"type": "number", "description": "excl. VAT; omit to use catalogue price"}},
      ["order", "quantity"], risk="write")
def add_order_parts(ctx, order, quantity, part_id=None, description="", unit_price=None):
    o = get_order(order)
    p = workshop.find_product(_int(part_id, "part_id")) if part_id else None
    if part_id and p is None:
        raise ToolError("part not found; use find_parts")
    try:
        lines = workshop.add_lines(o, [{"product": p, "description": description, "qty": quantity,
                                        "unit_price": unit_price}], ctx.user, source="assistant")
    except workshop.WorkshopError as e:
        raise ToolError(str(e))
    ctx.changed = True
    ctx.link(o.number, url_for("orders.view", oid=o.id))
    ln = lines[0]
    return {"ok": True, "order": o.number, "added": f"{ln.qty.normalize():f} × {ln.description} @ {fmt_money(ln.unit_price)}",
            "order_total_incl_vat": fmt_money(o.totals().payable)}


@tool("create_order", "Open a new service order when a customer drops off a device. Look up customer_id first.",
      {"customer_id": {"type": "integer"}, "device_type": {"type": "string"}, "brand": {"type": "string"},
       "model": {"type": "string"}, "serial_no": {"type": "string"}, "complaint": {"type": "string"},
       "accessories": {"type": "string"}},
      ["customer_id", "device_type"], risk="write")
def create_order(ctx, customer_id, device_type, brand="", model="", serial_no="", complaint="", accessories=""):
    c = get_contact(customer_id)
    o = workshop.create_order(c, ctx.user, source="assistant", device_type=device_type.strip(), brand=brand.strip(),
                              model=model.strip(), serial_no=serial_no.strip(), complaint=complaint.strip(),
                              accessories=accessories.strip())
    db.session.flush()
    ctx.changed = True
    ctx.link(o.number, url_for("orders.view", oid=o.id))
    ctx.link(_("Print receipt"), url_for("orders.receipt", oid=o.id))
    return {"ok": True, "order": o.number, "id": o.id}


@tool("create_draft_invoice", "Create a draft invoice from a finished service order (not sent yet).",
      {"order": {"type": "string"}}, ["order"], risk="write")
def create_draft_invoice(ctx, order):
    o = get_order(order)
    try:
        inv, created = workshop.draft_invoice_from_order(o, ctx.user, source="assistant")
    except workshop.WorkshopError as e:
        raise ToolError(str(e))
    db.session.flush()
    ctx.changed = True
    ctx.link(_("Draft invoice") if inv.is_draft else inv.number, url_for("invoices.view", iid=inv.id))
    return {"ok": True, "invoice_id": inv.id, "already_existed": not created, "status": inv.status,
            "total": fmt_money(inv.payable), "type": inv.profile}


# ---------------------------------------------------------------- confirm tools


def _c_create_customer(a):
    return _("Create customer “{name}”", name=a.get("name", "?")) + (f" · {a['phone']}" if a.get("phone") else "")


@tool("create_customer", "Create a new customer. Search with find_customers first to avoid duplicates.",
      {"name": {"type": "string"}, "phone": {"type": "string"}, "is_company": {"type": "boolean"},
       "tax_id": {"type": "string"}, "city": {"type": "string"}},
      ["name"], risk="confirm", confirm_text=_c_create_customer)
def create_customer(ctx, name, phone="", is_company=False, tax_id="", city=""):
    name = (name or "").strip()
    if not name:
        raise ToolError("name is required")
    tax_id = "".join(ch for ch in (tax_id or "") if ch.isdigit())
    if tax_id and len(tax_id) not in (10, 11):
        raise ToolError("tax_id must be 10 (VKN) or 11 (TCKN) digits")
    parts = name.split()
    c = Contact(kind="customer", is_company=bool(is_company), name=name, phone=phone or "", tax_id=tax_id,
                city=city or "", first_name="" if is_company else " ".join(parts[:-1]),
                last_name="" if is_company else (parts[-1] if len(parts) > 1 else ""))
    db.session.add(c)
    db.session.flush()
    ctx.changed = True
    ctx.link(c.name, url_for("contacts.view", cid=c.id))
    return {"ok": True, "customer_id": c.id}


def _c_issue(a):
    try:
        inv = get_invoice(a.get("invoice_id"))
    except ToolError:
        return _("Issue and send invoice #{id}", id=a.get("invoice_id"))
    return _("Issue and send to GİB: {customer} · {amount}", customer=inv.contact.name, amount=fmt_money(inv.payable))


@tool("issue_invoice", "Issue a draft invoice and send it to GİB through the integrator.",
      {"invoice_id": {"type": "integer"}}, ["invoice_id"], risk="confirm", confirm_text=_c_issue)
def issue_invoice(ctx, invoice_id):
    inv = get_invoice(invoice_id)
    try:
        invoicing.issue(inv, ctx.user)
        invoicing.send(inv, ctx.user)
    except invoicing.InvoiceError as e:
        raise ToolError("; ".join(_(m) for m in e.messages))
    ctx.changed = True
    ctx.link(inv.number, url_for("invoices.view", iid=inv.id))
    return {"ok": True, "number": inv.number, "status": inv.status}


def _c_payment(a):
    acct = db.session.get(Account, a.get("account_id") or 0)
    try:
        inv = get_invoice(a.get("invoice_id"))
        who = f"{inv.contact.name} ({inv.number})"
    except ToolError:
        who = "?"
    return _("Record payment {amount} from {who} into {account}", amount=fmt_money(to_decimal(a.get("amount"))),
             who=who, account=acct.name if acct else "?")


@tool("record_payment", "Record money received for an invoice. Use list_accounts for account_id.",
      {"invoice_id": {"type": "integer"}, "amount": {"type": "number"}, "account_id": {"type": "integer"}},
      ["invoice_id", "amount", "account_id"], risk="confirm", confirm_text=_c_payment)
def record_payment(ctx, invoice_id, amount, account_id):
    inv = get_invoice(invoice_id)
    acct = db.session.get(Account, _int(account_id, "account_id"))
    try:
        workshop.record_invoice_payment(inv, acct, amount, ctx.user)
    except workshop.WorkshopError as e:
        raise ToolError(str(e))
    ctx.changed = True
    ctx.link(inv.number, url_for("invoices.view", iid=inv.id))
    return {"ok": True, "invoice": inv.number, "still_open": fmt_money(inv.open_amount)}


def _c_expense(a):
    return _("Record expense: {category} · {amount} ({vat})", category=a.get("category", "?"),
             amount=fmt_money(to_decimal(a.get("amount"))),
             vat=_("incl. VAT") if a.get("includes_vat", True) else _("excl. VAT"))


@tool("record_expense", "Record a business expense (rent, parts purchase, bills).",
      {"category": {"type": "string", "enum": Expense.CATEGORIES}, "amount": {"type": "number"},
       "includes_vat": {"type": "boolean"}, "vat_rate": {"type": "integer", "enum": [0, 1, 10, 20]},
       "description": {"type": "string"}, "paid_from_account_id": {"type": "integer"}},
      ["category", "amount"], risk="confirm", confirm_text=_c_expense)
def record_expense(ctx, category, amount, includes_vat=True, vat_rate=20, description="", paid_from_account_id=None):
    if category not in Expense.CATEGORIES:
        category = "Diğer"
    acct = db.session.get(Account, _int(paid_from_account_id, "account")) if paid_from_account_id else None
    try:
        e = workshop.create_expense(ctx.user, category=category, amount=amount, includes_vat=includes_vat,
                                    vat_rate=vat_rate if vat_rate in (0, 1, 10, 20) else 20,
                                    description=description or "", pay_account=acct)
    except workshop.WorkshopError as err:
        raise ToolError(str(err))
    ctx.changed = True
    ctx.link(_("Expenses"), url_for("books.expenses"))
    return {"ok": True, "net": fmt_money(e.net), "vat": fmt_money(e.vat), "total": fmt_money(e.total)}


def _c_stock(a):
    p = db.session.get(Product, a.get("part_id") or 0)
    return _("Change stock of {part} by {change}", part=p.name if p else "?", change=a.get("change"))


@tool("adjust_stock", "Correct the stock of a part, e.g. after receiving goods (+) or a count (-).",
      {"part_id": {"type": "integer"}, "change": {"type": "number"}}, ["part_id", "change"], risk="confirm",
      confirm_text=_c_stock)
def adjust_stock(ctx, part_id, change):
    p = workshop.find_product(_int(part_id, "part_id"))
    if p is None:
        raise ToolError("part not found")
    try:
        new = workshop.adjust_stock(p, change)
    except workshop.WorkshopError as e:
        raise ToolError(str(e))
    ctx.changed = True
    return {"ok": True, "part": p.name, "stock": f"{new.normalize():f}"}


def specs():
    return [t.spec() for t in TOOLS.values()]


def run(name, args, ctx):
    """Execute a tool; returns (result_dict, is_error)."""
    t = TOOLS.get(name)
    if t is None:
        return {"error": f"Unknown tool {name}. Available: {', '.join(TOOLS)}"}, True
    if args is None:
        return {"error": "Arguments were not valid JSON. Call the tool again with a JSON object."}, True
    allowed = set(t.parameters["properties"])
    unknown = set(args) - allowed
    args = {k: v for k, v in args.items() if k in allowed and v is not None}
    missing = [r for r in t.parameters["required"] if r not in args or args[r] in ("", None)]
    if missing:
        return {"error": f"Missing required argument(s): {', '.join(missing)}"}, True
    try:
        result = t.fn(ctx, **args)
        db.session.commit()  # each tool call is its own transaction
    except ToolError as e:
        db.session.rollback()
        return {"error": str(e)}, True
    except (TypeError, ValueError, ArithmeticError) as e:
        db.session.rollback()
        return {"error": f"Invalid arguments: {e}"}, True
    if unknown:
        result["note"] = f"Ignored unknown argument(s): {', '.join(sorted(unknown))}"
    return result, False
