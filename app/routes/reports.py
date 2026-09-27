import csv
import io
from datetime import date
from decimal import Decimal

from flask import Blueprint, Response, abort, render_template, request

from ..extensions import db
from ..i18n import _
from ..models import Contact, Expense, Invoice, Transaction
from ..web import KIND_LABELS, PROFILE_LABELS, STATUS_LABELS, TYPE_LABELS
from . import q_date

bp = Blueprint("reports", __name__, url_prefix="/reports")

MONTHS_TR = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]
MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def month_name(i):
    from ..i18n import current_lang

    return (MONTHS_TR if current_lang() == "tr" else MONTHS_EN)[i - 1]


def year_summary(year):
    z = Decimal("0")
    months = {m: {"month": m, "sales": z, "returns": z, "sales_vat": z, "withheld": z, "expenses": z,
                  "expense_vat": z, "collected": z, "paid": z} for m in range(1, 13)}
    start, end = date(year, 1, 1), date(year + 1, 1, 1)
    for inv in Invoice.query.filter(Invoice.status.in_(Invoice.POSTED), Invoice.issue_date >= start,
                                    Invoice.issue_date < end):
        m = months[inv.issue_date.month]
        if inv.type_code == "IADE":
            m["returns"] += inv.net_total
            m["sales_vat"] -= inv.vat_total
        else:
            m["sales"] += inv.net_total
            m["sales_vat"] += inv.vat_total
            m["withheld"] += inv.withholding_total
    for e in Expense.query.filter(Expense.date >= start, Expense.date < end):
        m = months[e.date.month]
        m["expenses"] += e.net
        m["expense_vat"] += e.vat
    for t in Transaction.query.filter(Transaction.date >= start, Transaction.date < end,
                                      Transaction.kind != "transfer"):
        m = months[t.date.month]
        if t.direction == "in":
            m["collected"] += t.amount
        else:
            m["paid"] += t.amount
    rows = []
    for m in months.values():
        m["net_sales"] = m["sales"] - m["returns"]
        m["profit"] = m["net_sales"] - m["expenses"]
        # KDV the business declares = computed VAT − withheld part (paid by buyer) − deductible VAT
        m["vat_due"] = m["sales_vat"] - m["withheld"] - m["expense_vat"]
        m["label"] = month_name(m["month"])
        rows.append(m)
    keys = [k for k in rows[0] if isinstance(rows[0][k], Decimal)]
    total = {k: sum((r[k] for r in rows), z) for k in keys}
    return rows, total


@bp.route("/")
def index():
    year = request.args.get("year", type=int) or date.today().year
    rows, total = year_summary(year)
    first = db.session.query(db.func.min(Invoice.issue_date)).scalar()
    years = list(range(date.today().year, (first.year if first else date.today().year) - 1, -1))
    series = [{"month": f"{year}-{r['month']:02d}", "income": r["net_sales"], "expense": r["expenses"]}
              for r in rows]
    return render_template("reports/index.html", year=year, rows=rows, total=total, years=years, series=series)


@bp.route("/receivables")
def receivables():
    contacts = Contact.query.filter_by(archived=False).order_by(Contact.name).all()
    rows = []
    for c in contacts:
        bal = c.balance()
        if bal == 0:
            continue
        overdue = sum((i.open_amount for i in c.invoices if i.is_overdue and i.type_code != "IADE"), Decimal("0"))
        rows.append({"c": c, "balance": bal, "overdue": overdue})
    rows.sort(key=lambda r: -r["balance"])
    recv = sum((r["balance"] for r in rows if r["balance"] > 0), Decimal("0"))
    pay = sum((-r["balance"] for r in rows if r["balance"] < 0), Decimal("0"))
    return render_template("reports/receivables.html", rows=rows, receivable=recv, payable=pay)


# ---------------------------------------------------------------- CSV exports for the accountant


def _dec(v):
    return f"{Decimal(v or 0):.2f}".replace(".", ",")


def _csv(filename, header, rows):
    buf = io.StringIO()
    buf.write("﻿")  # BOM so Excel opens UTF-8 (Turkish characters) correctly
    w = csv.writer(buf, delimiter=";")
    w.writerow(header)
    w.writerows(rows)
    return Response(buf.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@bp.route("/export/<what>.csv")
def export(what):
    dfrom = q_date("from", date(date.today().year, 1, 1))
    dto = q_date("to", date.today())
    suffix = f"{dfrom:%Y%m%d}-{dto:%Y%m%d}"
    if what == "invoices":
        q = Invoice.query.filter(Invoice.status != "draft", Invoice.issue_date >= dfrom, Invoice.issue_date <= dto)
        rows = [[i.issue_date.strftime("%d.%m.%Y"), i.number, i.uuid, _(PROFILE_LABELS[i.profile]),
                 _(TYPE_LABELS[i.type_code]), i.contact.name, i.contact.tax_id, i.contact.tax_office,
                 _dec(i.net_total), _dec(i.vat_total), _dec(i.withholding_total), _dec(i.payable),
                 _(STATUS_LABELS.get(i.status, i.status))]
                for i in q.order_by(Invoice.issue_date, Invoice.number)]
        header = [_("Date"), _("Invoice no"), "ETTN", _("Profile"), _("Type"), _("Customer"), _("VKN/TCKN"),
                  _("Tax office"), _("Net"), _("VAT"), _("Withheld VAT"), _("Payable"), _("Status")]
        return _csv(f"faturalar-{suffix}.csv", header, rows)
    if what == "invoice-lines":
        q = Invoice.query.filter(Invoice.status.in_(Invoice.POSTED), Invoice.issue_date >= dfrom,
                                 Invoice.issue_date <= dto)
        rows = []
        for i in q.order_by(Invoice.issue_date, Invoice.number):
            for ln in i.lines:
                rows.append([i.issue_date.strftime("%d.%m.%Y"), i.number, i.contact.name, ln.description,
                             f"{ln.qty.normalize():f}".replace(".", ","), ln.unit, _dec(ln.unit_price),
                             _dec(ln.discount_rate), ln.vat_rate])
        header = [_("Date"), _("Invoice no"), _("Customer"), _("Description"), _("Qty"), _("Unit"),
                  _("Unit price"), _("Discount %"), _("VAT %")]
        return _csv(f"fatura-satirlari-{suffix}.csv", header, rows)
    if what == "expenses":
        q = Expense.query.filter(Expense.date >= dfrom, Expense.date <= dto).order_by(Expense.date)
        rows = [[e.date.strftime("%d.%m.%Y"), e.doc_no, e.contact.name if e.contact else "",
                 e.contact.tax_id if e.contact else "", e.category, e.description, _dec(e.net), e.vat_rate,
                 _dec(e.vat), _dec(e.total)] for e in q]
        header = [_("Date"), _("Document no"), _("Supplier"), _("VKN/TCKN"), _("Category"), _("Description"),
                  _("Net"), _("VAT %"), _("VAT"), _("Total")]
        return _csv(f"giderler-{suffix}.csv", header, rows)
    if what == "transactions":
        q = Transaction.query.filter(Transaction.date >= dfrom, Transaction.date <= dto).order_by(
            Transaction.date, Transaction.id)
        rows = [[t.date.strftime("%d.%m.%Y"), t.account.name, _(KIND_LABELS.get(t.kind, t.kind)),
                 t.contact.name if t.contact else "", t.category, t.description,
                 _dec(t.amount) if t.direction == "in" else "", _dec(t.amount) if t.direction == "out" else ""]
                for t in q]
        header = [_("Date"), _("Account"), _("Type"), _("Contact"), _("Category"), _("Description"), _("In"),
                  _("Out")]
        return _csv(f"kasa-banka-{suffix}.csv", header, rows)
    if what == "contacts":
        rows = [[c.name, c.tax_id, c.tax_office, c.phone, c.email, c.address, c.district, c.city,
                 _dec(c.balance())] for c in Contact.query.order_by(Contact.name)]
        header = [_("Name"), _("VKN/TCKN"), _("Tax office"), _("Phone"), _("Email"), _("Address"), _("District"),
                  _("City"), _("Balance")]
        return _csv("cariler.csv", header, rows)
    abort(404)
