from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from sqlalchemy import or_

from ..extensions import db
from ..i18n import _
from ..models import UNITS, VAT_RATES, InvoiceLine, Product, ServiceOrderLine
from . import f_bool, f_dec, f_int, f_str

bp = Blueprint("products", __name__, url_prefix="/products")


def _get(pid):
    p = db.session.get(Product, pid)
    if p is None:
        abort(404)
    return p


@bp.route("/")
def index():
    q = (request.args.get("q") or "").strip()
    kind = request.args.get("kind", "")
    low = request.args.get("low") == "1"
    query = Product.query.filter_by(archived=request.args.get("archived") == "1")
    if kind in ("part", "service"):
        query = query.filter_by(kind=kind)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Product.name.ilike(like), Product.code.ilike(like)))
    if low:
        query = query.filter(Product.kind == "part", Product.track_stock.is_(True),
                             Product.stock_qty <= Product.min_stock)
    products = query.order_by(Product.name).all()
    return render_template("products/index.html", products=products, q=q, kind=kind, low=low)


def _fill(p):
    p.code = f_str("code")
    p.name = f_str("name")
    p.kind = f_str("kind") if f_str("kind") in ("part", "service") else "part"
    p.unit = f_str("unit") if f_str("unit") in UNITS else "C62"
    p.price = f_dec("price")
    p.cost = f_dec("cost")
    p.vat_rate = f_int("vat_rate", 20)
    p.track_stock = p.kind == "part" and f_bool("track_stock")
    p.min_stock = f_dec("min_stock")


@bp.route("/new", methods=["GET", "POST"])
def new():
    p = Product(kind=request.args.get("kind", "part"), unit="C62", vat_rate=20, track_stock=True)
    if request.method == "POST":
        _fill(p)
        p.stock_qty = f_dec("stock_qty")
        if not p.name:
            flash(_("Name is required."), "error")
        else:
            db.session.add(p)
            db.session.commit()
            flash(_("Item saved."), "success")
            return redirect(url_for("products.index"))
    return render_template("products/form.html", p=p, units=UNITS, vat_rates=VAT_RATES)


@bp.route("/<int:pid>/edit", methods=["GET", "POST"])
def edit(pid):
    p = _get(pid)
    if request.method == "POST":
        _fill(p)
        if not p.name:
            flash(_("Name is required."), "error")
        else:
            db.session.commit()
            flash(_("Item saved."), "success")
            return redirect(url_for("products.index"))
    return render_template("products/form.html", p=p, units=UNITS, vat_rates=VAT_RATES)


@bp.route("/<int:pid>/stock", methods=["POST"])
def stock(pid):
    p = _get(pid)
    mode = f_str("mode")
    qty = f_dec("qty")
    if mode == "set":
        p.stock_qty = qty
    else:
        p.stock_qty = p.stock_qty + qty
    db.session.commit()
    flash(_("Stock updated: {name} → {qty}", name=p.name, qty=f"{p.stock_qty.normalize():f}"), "success")
    return redirect(request.referrer or url_for("products.index"))


@bp.route("/<int:pid>/archive", methods=["POST"])
def archive(pid):
    p = _get(pid)
    used = InvoiceLine.query.filter_by(product_id=p.id).first() or ServiceOrderLine.query.filter_by(
        product_id=p.id).first()
    if used or p.archived:
        p.archived = not p.archived
        msg = _("Item archived.") if p.archived else _("Item restored.")
    else:
        db.session.delete(p)
        msg = _("Item deleted.")
    db.session.commit()
    flash(msg, "success")
    return redirect(url_for("products.index"))
