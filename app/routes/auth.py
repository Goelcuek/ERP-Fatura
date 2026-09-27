from urllib.parse import urlparse

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file, session, url_for

from ..extensions import db
from ..i18n import LANGUAGES, _
from ..models import Setting, User
from . import f_str

bp = Blueprint("auth", __name__)


def _safe_next(target):
    if not target:
        return None
    p = urlparse(target)
    if p.scheme or p.netloc or not target.startswith("/"):
        return None
    return target


@bp.route("/login", methods=["GET", "POST"])
def login():
    if User.query.count() == 0:
        return redirect(url_for("auth.setup"))
    if request.method == "POST":
        user = User.query.filter_by(username=f_str("username").lower()).first()
        if user and user.active and user.check_password(request.form.get("password", "")):
            session.clear()
            session["uid"] = user.id
            session.permanent = True
            return redirect(_safe_next(request.args.get("next")) or url_for("dashboard.index"))
        flash(_("Invalid username or password."), "error")
    return render_template("auth/login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    """First-run wizard: create the admin account and basic company details."""
    if User.query.count() > 0:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        username = f_str("username").lower()
        pw = request.form.get("password", "")
        errors = []
        if not username or not f_str("full_name"):
            errors.append(_("Name and username are required."))
        if len(pw) < 8:
            errors.append(_("Password must be at least 8 characters."))
        if pw != request.form.get("password2", ""):
            errors.append(_("Passwords do not match."))
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("auth/setup.html", form=request.form)
        user = User(username=username, full_name=f_str("full_name"), role="admin", lang=f_str("lang") or "tr")
        user.set_password(pw)
        db.session.add(user)
        for key in ("name", "tax_id", "tax_office", "city", "district", "phone"):
            Setting.set(f"company.{key}", f_str(key))
        db.session.commit()
        session.clear()
        session["uid"] = user.id
        flash(_("Welcome! Complete your company details in Settings before issuing invoices."), "success")
        return redirect(url_for("dashboard.index"))
    return render_template("auth/setup.html", form={"name": current_app.config["CUSTOMER"].get("company_name", "")})


@bp.route("/branding/logo")
def logo():
    from ..services.branding import logo_path

    path = logo_path()
    if not path:
        abort(404)
    resp = send_file(path, max_age=300)
    resp.headers["Content-Security-Policy"] = "default-src 'none'"
    return resp


@bp.route("/profile", methods=["GET", "POST"])
def profile():
    user = g.user
    if request.method == "POST":
        user.full_name = f_str("full_name") or user.full_name
        lang = f_str("lang")
        if lang in LANGUAGES:
            user.lang = lang
        new = request.form.get("new_password", "")
        if new:
            if not user.check_password(request.form.get("current_password", "")):
                flash(_("Current password is wrong."), "error")
                return redirect(url_for("auth.profile"))
            if len(new) < 8:
                flash(_("Password must be at least 8 characters."), "error")
                return redirect(url_for("auth.profile"))
            user.set_password(new)
        db.session.commit()
        flash(_("Profile saved."), "success")
        return redirect(url_for("auth.profile"))
    return render_template("auth/profile.html")
