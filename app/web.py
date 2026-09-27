"""Request plumbing: authentication, CSRF, template filters and helpers."""

import secrets
from datetime import date, datetime
from decimal import Decimal
from functools import wraps

from flask import abort, flash, g, redirect, request, session, url_for
from markupsafe import Markup

from .extensions import db
from .i18n import _
from .models import UNITS, Setting, User

PUBLIC_ENDPOINTS = {"auth.login", "auth.setup", "static"}


def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not g.user or not g.user.is_admin:
            abort(403)
        return fn(*a, **kw)

    return wrapper


def csrf_token():
    tok = session.get("_csrf")
    if not tok:
        tok = session["_csrf"] = secrets.token_urlsafe(32)
    return tok


def fmt_money(value, currency="₺", blank_zero=False):
    if value is None:
        return ""
    v = Decimal(value).quantize(Decimal("0.01"))
    if blank_zero and v == 0:
        return "—"
    s = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    sign = "−" if v < 0 else ""
    return f"{sign}{s} {currency}".strip()


def fmt_num(value, places=2):
    if value is None:
        return ""
    v = Decimal(value)
    if places is None:
        s = format(v.normalize(), "f")
        if "." in s:
            whole, frac = s.split(".")
        else:
            whole, frac = s, ""
        whole = f"{int(whole):,}".replace(",", ".")
        return whole + ("," + frac if frac else "")
    s = f"{v:,.{places}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_input(value, places=2):
    """Number formatted for an <input> (plain, dot decimal)."""
    if value is None:
        return ""
    v = Decimal(value)
    if places == "qty":
        return format(v.normalize(), "f")
    return f"{v:.{places}f}"


def fmt_date(value):
    if not value:
        return ""
    if isinstance(value, str):
        return value
    return value.strftime("%d.%m.%Y")


def fmt_datetime(value):
    if not value:
        return ""
    return value.strftime("%d.%m.%Y %H:%M")


def fmt_bytes(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def rel_days(d):
    if not d:
        return ""
    if isinstance(d, datetime):
        d = d.date()
    diff = (d - date.today()).days
    if diff == 0:
        return _("today")
    if diff == 1:
        return _("tomorrow")
    if diff == -1:
        return _("yesterday")
    if diff > 0:
        return _("in {n} days").format(n=diff)
    return _("{n} days ago").format(n=-diff)


STATUS_TONES = {
    # invoices
    "draft": "neutral",
    "issued": "info",
    "sent": "info",
    "exported": "info",
    "accepted": "success",
    "rejected": "danger",
    "error": "danger",
    "cancelled": "muted",
    # service orders
    "received": "neutral",
    "diagnosing": "violet",
    "awaiting_approval": "warning",
    "in_repair": "info",
    "awaiting_parts": "orange",
    "ready": "success",
    "delivered": "muted",
}

STATUS_LABELS = {
    "draft": "Draft",
    "issued": "Issued",
    "sent": "Sent",
    "exported": "Exported",
    "accepted": "Accepted",
    "rejected": "Rejected",
    "error": "Error",
    "cancelled": "Cancelled",
    "received": "Received",
    "diagnosing": "Diagnosing",
    "awaiting_approval": "Awaiting approval",
    "in_repair": "In repair",
    "awaiting_parts": "Awaiting parts",
    "ready": "Ready for pickup",
    "delivered": "Delivered",
}

PROFILE_LABELS = {
    "TEMELFATURA": "e-Fatura · Basic",
    "TICARIFATURA": "e-Fatura · Commercial",
    "EARSIVFATURA": "e-Arşiv",
}

TYPE_LABELS = {"SATIS": "Sale", "IADE": "Return", "TEVKIFAT": "Withholding", "ISTISNA": "Exempt"}

KIND_LABELS = {
    "collection": "Collection",
    "payment": "Payment",
    "income": "Other income",
    "expense": "Expense",
    "transfer": "Transfer",
}


def status_badge(status):
    tone = STATUS_TONES.get(status, "neutral")
    label = _(STATUS_LABELS.get(status, status))
    return Markup(f'<span class="badge badge-{tone}"><span class="dot"></span>{label}</span>')


def icon(name, cls=""):
    return Markup(f'<svg class="icon {cls}" aria-hidden="true"><use href="#i-{name}"></use></svg>')


def init_web(app):
    app.jinja_env.filters.update(
        money=fmt_money,
        num=fmt_num,
        inp=fmt_input,
        d=fmt_date,
        dt=fmt_datetime,
        bytes=fmt_bytes,
        reldays=rel_days,
    )
    from .services.integrators import REGISTRY

    app.jinja_env.globals.update(
        INTEGRATOR_LABELS={k: c.label for k, c in REGISTRY.items()},
        csrf_token=csrf_token,
        status_badge=status_badge,
        icon=icon,
        STATUS_LABELS=STATUS_LABELS,
        PROFILE_LABELS=PROFILE_LABELS,
        TYPE_LABELS=TYPE_LABELS,
        KIND_LABELS=KIND_LABELS,
        UNITS=UNITS,
        today=date.today,
    )

    @app.before_request
    def load_user():
        g.user = None
        uid = session.get("uid")
        if uid:
            u = db.session.get(User, uid)
            if u and u.active:
                g.user = u
        if request.endpoint in PUBLIC_ENDPOINTS or request.endpoint is None:
            return None
        if User.query.count() == 0:
            return redirect(url_for("auth.setup"))
        if g.user is None:
            return redirect(url_for("auth.login", next=request.full_path if request.method == "GET" else None))
        return None

    @app.before_request
    def check_csrf():
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and not app.config.get("WTF_CSRF_DISABLED"):
            sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
            if not sent or sent != session.get("_csrf"):
                flash(_("Your session expired. Please try again."), "error")
                return redirect(request.referrer or url_for("dashboard.index"))
        return None

    @app.context_processor
    def inject():
        return {"company_name": Setting.get("company.name") or "Atölye"}

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp
