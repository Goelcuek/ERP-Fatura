from datetime import date, datetime, timedelta
from decimal import Decimal

from flask import Blueprint, render_template, request
from sqlalchemy import func, or_

from ..extensions import db
from ..models import Account, Contact, Expense, Invoice, Product, ServiceOrder, Setting
from . import month_bounds

bp = Blueprint("dashboard", __name__)


def monthly_series(months=12):
    """Net sales (returns subtracted) and net expenses per month, oldest first."""
    today = date.today()
    start = date(today.year, today.month, 1)
    for _ in range(months - 1):
        start = (start - timedelta(days=1)).replace(day=1)
    keys = []
    d = start
    while len(keys) < months:
        keys.append(d.strftime("%Y-%m"))
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)

    ym = func.strftime("%Y-%m", Invoice.issue_date)
    sales = dict(
        db.session.query(ym, func.sum(Invoice.net_total))
        .filter(Invoice.status.in_(Invoice.POSTED), Invoice.type_code != "IADE", Invoice.issue_date >= start)
        .group_by(ym)
        .all()
    )
    returns = dict(
        db.session.query(ym, func.sum(Invoice.net_total))
        .filter(Invoice.status.in_(Invoice.POSTED), Invoice.type_code == "IADE", Invoice.issue_date >= start)
        .group_by(ym)
        .all()
    )
    eym = func.strftime("%Y-%m", Expense.date)
    expenses = dict(
        db.session.query(eym, func.sum(Expense.net)).filter(Expense.date >= start).group_by(eym).all()
    )
    # func.sum over ScaledDecimal columns returns Decimals already
    rows = []
    for k in keys:
        inc = Decimal(sales.get(k) or 0) - Decimal(returns.get(k) or 0)
        rows.append({"month": k, "income": inc, "expense": Decimal(expenses.get(k) or 0)})
    return rows


@bp.route("/")
def index():
    m_start, m_end = month_bounds()
    posted = Invoice.status.in_(Invoice.POSTED)

    month_sales = (
        db.session.query(func.coalesce(func.sum(Invoice.net_total), 0))
        .filter(posted, Invoice.type_code != "IADE", Invoice.issue_date >= m_start, Invoice.issue_date < m_end)
        .scalar()
    )
    month_expense = (
        db.session.query(func.coalesce(func.sum(Expense.net), 0))
        .filter(Expense.date >= m_start, Expense.date < m_end)
        .scalar()
    )
    open_invoices = Invoice.query.filter(posted, Invoice.type_code != "IADE").all()
    receivable = sum((i.open_amount for i in open_invoices), Decimal("0"))
    overdue = [i for i in open_invoices if i.is_overdue]
    overdue.sort(key=lambda i: i.due_date)

    cash = sum((a.balance() for a in Account.query.filter_by(archived=False)), Decimal("0"))

    status_counts = dict(
        db.session.query(ServiceOrder.status, func.count(ServiceOrder.id))
        .filter(ServiceOrder.status.in_(ServiceOrder.OPEN))
        .group_by(ServiceOrder.status)
        .all()
    )
    open_orders = (
        ServiceOrder.query.filter(ServiceOrder.status.in_(ServiceOrder.OPEN))
        .order_by(ServiceOrder.promised_date.is_(None), ServiceOrder.promised_date, ServiceOrder.received_at)
        .limit(8)
        .all()
    )
    low_stock = (
        Product.query.filter(
            Product.archived.is_(False), Product.kind == "part", Product.track_stock.is_(True),
            Product.stock_qty <= Product.min_stock,
        )
        .order_by(Product.name)
        .limit(6)
        .all()
    )
    drafts = Invoice.query.filter(Invoice.status.in_(["draft", "error"])).count()

    backup_warning = None
    last = Setting.get("backup.last_at")
    if Setting.get("backup.last_error"):
        backup_warning = "error"
    elif not last:
        backup_warning = "never"
    else:
        try:
            age = datetime.now() - datetime.fromisoformat(last)
            if age > timedelta(hours=int(Setting.get("backup.interval_hours") or 24) * 2):
                backup_warning = "old"
        except ValueError:
            backup_warning = "never"

    return render_template(
        "dashboard.html",
        month_sales=Decimal(month_sales or 0),
        month_expense=Decimal(month_expense or 0),
        receivable=receivable,
        overdue=overdue[:6],
        overdue_total=sum((i.open_amount for i in overdue), Decimal("0")),
        cash=cash,
        status_counts=status_counts,
        open_orders=open_orders,
        open_total=sum(status_counts.values()),
        low_stock=low_stock,
        drafts=drafts,
        series=monthly_series(12),
        backup_warning=backup_warning,
    )


@bp.route("/search")
def search():
    q = (request.args.get("q") or "").strip()
    contacts = orders = invoices = []
    if q:
        like = f"%{q}%"
        contacts = (
            Contact.query.filter(or_(Contact.name.ilike(like), Contact.tax_id.like(like), Contact.phone.like(like),
                                     Contact.email.ilike(like)))
            .order_by(Contact.name).limit(20).all()
        )
        orders = (
            ServiceOrder.query.join(Contact)
            .filter(or_(ServiceOrder.number.ilike(like), ServiceOrder.serial_no.ilike(like),
                        ServiceOrder.brand.ilike(like), ServiceOrder.model.ilike(like), Contact.name.ilike(like)))
            .order_by(ServiceOrder.received_at.desc()).limit(20).all()
        )
        invoices = (
            Invoice.query.join(Contact)
            .filter(or_(Invoice.number.ilike(like), Invoice.uuid.ilike(like), Contact.name.ilike(like)))
            .order_by(Invoice.issue_date.desc()).limit(20).all()
        )
    return render_template("search.html", q=q, contacts=contacts, orders=orders, invoices=invoices)
