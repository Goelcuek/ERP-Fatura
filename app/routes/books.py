import uuid
from datetime import date
from decimal import Decimal

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import or_

from ..extensions import db
from ..i18n import _
from ..models import VAT_RATES, Account, Contact, Expense, Transaction
from ..services.calc import r2, split_gross
from . import f_date, f_dec, f_int, f_str, month_bounds, q_date

bp = Blueprint("books", __name__, url_prefix="/books")


@bp.route("/")
def index():
    accounts = Account.query.filter_by(archived=False).order_by(Account.kind, Account.name).all()
    balances = {a.id: a.balance() for a in accounts}
    recent = Transaction.query.order_by(Transaction.date.desc(), Transaction.id.desc()).limit(25).all()
    return render_template("books/index.html", accounts=accounts, balances=balances, recent=recent,
                           total=sum(balances.values(), Decimal("0")))


# ---------------------------------------------------------------- accounts


@bp.route("/accounts/new", methods=["GET", "POST"])
@bp.route("/accounts/<int:aid>/edit", methods=["GET", "POST"])
def account_form(aid=None):
    a = db.session.get(Account, aid) if aid else Account(kind="cash")
    if aid and a is None:
        abort(404)
    if request.method == "POST":
        a.name = f_str("name")
        a.kind = f_str("kind") if f_str("kind") in ("cash", "bank", "pos") else "cash"
        a.iban = f_str("iban").replace(" ", "").upper()
        a.opening_balance = f_dec("opening_balance")
        a.archived = request.form.get("archived") == "1"
        if not a.name:
            flash(_("Name is required."), "error")
        else:
            if not aid:
                db.session.add(a)
            db.session.commit()
            flash(_("Account saved."), "success")
            return redirect(url_for("books.account", aid=a.id))
    return render_template("books/account_form.html", a=a)


@bp.route("/accounts/<int:aid>")
def account(aid):
    a = db.session.get(Account, aid)
    if a is None:
        abort(404)
    dfrom = q_date("from")
    dto = q_date("to")
    txs = Transaction.query.filter_by(account_id=a.id).order_by(Transaction.date, Transaction.id).all()
    bal = a.opening_balance
    rows = []
    for t in txs:
        bal += t.signed_amount
        rows.append((t, bal))
    if dfrom:
        rows = [r for r in rows if r[0].date >= dfrom]
    if dto:
        rows = [r for r in rows if r[0].date <= dto]
    rows.reverse()
    return render_template("books/account.html", a=a, rows=rows, balance=bal, dfrom=dfrom, dto=dto)


# ---------------------------------------------------------------- transactions


@bp.route("/tx/new", methods=["GET", "POST"])
def tx_new():
    kind = request.values.get("kind", "collection")
    if kind not in Transaction.KINDS or kind == "expense":
        kind = "collection"
    accounts = Account.query.filter_by(archived=False).order_by(Account.name).all()
    contact = db.session.get(Contact, request.values.get("contact_id", type=int) or 0)
    if not accounts:
        flash(_("Create a cash or bank account first."), "error")
        return redirect(url_for("books.account_form"))
    if request.method == "POST":
        amount = f_dec("amount")
        acct = db.session.get(Account, f_int("account_id") or 0)
        d = f_date("date", date.today())
        desc = f_str("description")
        if acct is None or amount <= 0:
            flash(_("Choose an account and a positive amount."), "error")
        elif kind == "transfer":
            to = db.session.get(Account, f_int("to_account_id") or 0)
            if to is None or to.id == acct.id:
                flash(_("Choose two different accounts."), "error")
            else:
                grp = str(uuid.uuid4())
                for a, direction in ((acct, "out"), (to, "in")):
                    db.session.add(Transaction(date=d, account=a, direction=direction, amount=amount,
                                               kind="transfer", description=desc, transfer_group=grp,
                                               created_by_id=g.user.id))
                db.session.commit()
                flash(_("Transfer recorded."), "success")
                return redirect(url_for("books.index"))
        else:
            cid = f_int("contact_id")
            if kind in ("collection", "payment") and not cid:
                flash(_("Select a customer or supplier."), "error")
            else:
                db.session.add(Transaction(
                    date=d, account=acct, direction="in" if kind in ("collection", "income") else "out",
                    amount=amount, kind=kind, contact_id=cid, category=f_str("category"), description=desc,
                    created_by_id=g.user.id,
                ))
                db.session.commit()
                flash(_("Transaction recorded."), "success")
                if request.form.get("return_to") == "contact" and cid:
                    return redirect(url_for("contacts.view", cid=cid, tab="ledger"))
                return redirect(url_for("books.index"))
    return render_template("books/tx_form.html", kind=kind, accounts=accounts, contact=contact,
                           today=date.today())


@bp.route("/tx/<int:tid>/delete", methods=["POST"])
def tx_delete(tid):
    t = db.session.get(Transaction, tid)
    if t is None:
        abort(404)
    if t.transfer_group:
        Transaction.query.filter_by(transfer_group=t.transfer_group).delete()
    else:
        db.session.delete(t)
    db.session.commit()
    flash(_("Transaction deleted."), "success")
    return redirect(request.referrer or url_for("books.index"))


# ---------------------------------------------------------------- expenses


@bp.route("/expenses")
def expenses():
    m_start, m_end = month_bounds()
    dfrom = q_date("from", m_start)
    dto = q_date("to")
    q = (request.args.get("q") or "").strip()
    cat = request.args.get("category", "")
    query = Expense.query.outerjoin(Contact).filter(Expense.date >= dfrom)
    if dto:
        query = query.filter(Expense.date <= dto)
    if cat:
        query = query.filter(Expense.category == cat)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Expense.description.ilike(like), Expense.doc_no.ilike(like),
                                 Contact.name.ilike(like)))
    rows = query.order_by(Expense.date.desc(), Expense.id.desc()).all()
    totals = {
        "net": sum((e.net for e in rows), Decimal("0")),
        "vat": sum((e.vat for e in rows), Decimal("0")),
        "total": sum((e.total for e in rows), Decimal("0")),
    }
    return render_template("books/expenses.html", rows=rows, totals=totals, dfrom=dfrom, dto=dto, q=q, cat=cat,
                           categories=Expense.CATEGORIES)


@bp.route("/expenses/new", methods=["GET", "POST"])
@bp.route("/expenses/<int:eid>/edit", methods=["GET", "POST"])
def expense_form(eid=None):
    e = db.session.get(Expense, eid) if eid else Expense(date=date.today(), vat_rate=20, category="Parça / Malzeme")
    if eid and e is None:
        abort(404)
    accounts = Account.query.filter_by(archived=False).order_by(Account.name).all()
    if request.method == "POST":
        e.date = f_date("date", date.today())
        e.contact_id = f_int("contact_id")
        e.doc_no = f_str("doc_no")
        e.category = f_str("category") or "Diğer"
        e.description = f_str("description")
        e.vat_rate = f_int("vat_rate", 20)
        amount = f_dec("amount")
        if f_str("amount_mode") == "gross":
            e.net, e.vat = split_gross(amount, e.vat_rate)
        else:
            e.net = r2(amount)
            e.vat = r2(amount * e.vat_rate / 100)
        e.total = e.net + e.vat
        if e.total <= 0:
            flash(_("Amount must be greater than zero."), "error")
        else:
            if not eid:
                db.session.add(e)
            db.session.flush()
            linked = [t for t in e.payments if t.kind == "expense"]
            if len(linked) == 1:  # keep the automatic payment in sync with edits
                linked[0].amount, linked[0].date, linked[0].contact_id = e.total, e.date, e.contact_id
            acct = db.session.get(Account, f_int("pay_account_id") or 0)
            if acct is not None and not e.payments:
                db.session.add(Transaction(date=e.date, account=acct, direction="out", amount=e.total,
                                           kind="expense", contact_id=e.contact_id, expense_id=e.id,
                                           category=e.category, description=e.description or e.doc_no,
                                           created_by_id=g.user.id))
            db.session.commit()
            flash(_("Expense saved."), "success")
            return redirect(url_for("books.expenses", **{"from": e.date.replace(day=1).isoformat()}))
    return render_template("books/expense_form.html", e=e, accounts=accounts, vat_rates=VAT_RATES,
                           categories=Expense.CATEGORIES)


@bp.route("/expenses/<int:eid>/delete", methods=["POST"])
def expense_delete(eid):
    e = db.session.get(Expense, eid)
    if e is None:
        abort(404)
    for t in list(e.payments):
        db.session.delete(t)
    db.session.delete(e)
    db.session.commit()
    flash(_("Expense deleted."), "success")
    return redirect(url_for("books.expenses"))
