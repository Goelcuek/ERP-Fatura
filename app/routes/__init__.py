from datetime import date, datetime

from flask import request

from ..services.calc import to_decimal


def register_blueprints(app):
    from . import assistant, auth, books, contacts, dashboard, invoices, mobile, orders, products, reports, settings

    for mod in (auth, dashboard, contacts, orders, invoices, products, books, reports, settings, assistant, mobile):
        app.register_blueprint(mod.bp)


def f_str(name, default=""):
    return (request.form.get(name) or default).strip()


def f_int(name, default=None):
    try:
        return int(request.form.get(name))
    except (TypeError, ValueError):
        return default


def f_dec(name, default="0"):
    return to_decimal(request.form.get(name), default)


def f_bool(name):
    return request.form.get(name) in ("1", "on", "true", "yes")


def parse_date(value, default=None):
    if not value:
        return default
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return default


def f_date(name, default=None):
    return parse_date(request.form.get(name), default)


def q_date(name, default=None):
    return parse_date(request.args.get(name), default)


def parse_lines(line_cls):
    """Build line objects from the line editor's parallel form arrays."""
    from ..extensions import db
    from ..models import Product

    form = request.form
    descs = form.getlist("line_description")
    lines = []
    for i, desc in enumerate(descs):
        desc = (desc or "").strip()
        if not desc:
            continue

        def col(key, default=""):
            vals = form.getlist(key)
            return vals[i] if i < len(vals) else default

        pid = col("line_product_id")
        product = db.session.get(Product, int(pid)) if pid and pid.isdigit() else None
        vat = col("line_vat", "20")
        lines.append(
            line_cls(
                position=len(lines),
                product=product,
                description=desc[:300],
                qty=to_decimal(col("line_qty", "1"), "1"),
                unit=col("line_unit", "C62") or "C62",
                unit_price=to_decimal(col("line_price", "0")),
                discount_rate=min(max(to_decimal(col("line_discount", "0")), 0), 100),
                vat_rate=int(vat) if str(vat).isdigit() else 20,
            )
        )
    return lines


def month_bounds(d=None):
    d = d or date.today()
    start = d.replace(day=1)
    end = (start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1))
    return start, end


class Pager:
    def __init__(self, items, per_page=50):
        self.total = len(items)
        self.per_page = per_page
        self.pages = max(1, -(-self.total // per_page))
        self.page = min(max(request.args.get("page", 1, type=int), 1), self.pages)
        start = (self.page - 1) * per_page
        self.items = items[start:start + per_page]
        self.first = start + 1 if self.total else 0
        self.last = start + len(self.items)

    def url(self, page):
        from flask import url_for

        args = dict(request.args)
        args["page"] = page
        return url_for(request.endpoint, **(request.view_args or {}), **args)
