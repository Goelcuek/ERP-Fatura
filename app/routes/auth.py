from urllib.parse import urlparse

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file,
                   session, url_for)

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
        throttle = current_app.login_throttle
        username = f_str("username").lower()
        keys = (f"u:{username}", f"ip:{request.remote_addr}")  # lock the account and the device separately
        wait = throttle.retry_after(*keys)
        if wait:
            flash(_("Too many failed attempts. Please wait {n} seconds and try again.", n=wait), "error")
            return render_template("auth/login.html"), 429
        user = User.query.filter_by(username=username).first()
        if user and user.active and user.check_password(request.form.get("password", "")):
            throttle.record_success(*keys)
            session.clear()
            session["uid"] = user.id
            session.permanent = True
            return redirect(_safe_next(request.args.get("next")) or url_for("dashboard.index"))
        throttle.record_failure(*keys)
        flash(_("Invalid username or password."), "error")
    return render_template("auth/login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    """First run, step 1: the administrator account. The setup wizard (routes/setup.py) does the rest."""
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
            return render_template("setup/account.html", form=request.form, step="account")
        user = User(username=username, full_name=f_str("full_name"), role="admin", lang=f_str("lang") or "tr")
        user.set_password(pw)
        db.session.add(user)
        Setting.set("setup.pending", True)
        Setting.set("setup.step", "company")
        db.session.commit()
        session.clear()
        session["uid"] = user.id
        return redirect(url_for("wizard.company"))
    return render_template("setup/account.html", form={}, step="account")


@bp.route("/setup/restore", methods=["POST"])
def setup_restore():
    """First run on a new computer: bring everything over from a backup instead of setting up."""
    import os
    import tempfile

    from ..services import backup as backup_svc

    if User.query.count() > 0:
        return redirect(url_for("auth.login"))
    upload = request.files.get("file")
    if not upload or not upload.filename:
        flash(_("Choose a backup to restore."), "error")
        return redirect(url_for("auth.setup"))
    fd, tmp = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    try:
        upload.save(tmp)
        manifest, _safety = backup_svc.restore(tmp)
    except backup_svc.BackupError as e:
        flash(_("Restore failed: {err}", err=_(str(e))), "error")
        return redirect(url_for("auth.setup"))
    finally:
        os.remove(tmp)
    session.clear()
    flash(_("Restored backup from {date}. Sign in with your usual account.", date=manifest.get("created_at", "?")),
          "success")
    return redirect(url_for("auth.login"))


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
