"""First-run setup wizard: after the administrator account (auth.setup) it walks through the company,
logo, invoice numbers, the Uyumsoft connection and backups — everything a new installation needs,
without touching any file. Each step can be skipped and changed later in Settings."""

import os
from datetime import date

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from ..extensions import db
from ..i18n import _
from ..models import Setting
from ..web import admin_required
from . import f_bool, f_str
from .settings import (company_values, flash_connection_test, save_backup_folder, save_company_form,
                       save_integrator_form, save_series_form)

bp = Blueprint("wizard", __name__, url_prefix="/setup")

STEPS = ["account", "company", "invoicing", "efatura", "backup", "done"]
STEP_TITLES = {"account": "Your account", "company": "Company & logo", "invoicing": "Invoice numbers",
               "efatura": "e-Fatura connection", "backup": "Backups", "done": "Ready"}
INTEGRATOR = "uyumsoft"

bp.record_once(lambda state: state.app.jinja_env.globals.update(
    WIZARD_STEPS=[(key, STEP_TITLES[key]) for key in STEPS]))


@bp.before_request
@admin_required
def _admin_only():
    return None


def _render(step, **ctx):
    # remember how far the wizard got, for the "continue setup" banner
    if Setting.get("setup.pending") and STEPS.index(step) > STEPS.index(Setting.get("setup.step") or "company"):
        Setting.set("setup.step", step)
        db.session.commit()
    return render_template(f"setup/{step}.html", step=step, **ctx)


def _next(step):
    return redirect(url_for("wizard." + STEPS[STEPS.index(step) + 1]))


@bp.route("/continue")
def resume():
    step = Setting.get("setup.step") or "company"
    return redirect(url_for("wizard." + (step if step in STEPS[1:] else "company")))


@bp.route("/company", methods=["GET", "POST"])
def company():
    if request.method == "POST":
        if not f_str("name"):
            flash(_("Enter the company title."), "error")
            return _render("company", s={**company_values(), **request.form.to_dict()})
        error = save_company_form()
        if error:
            flash(error, "error")
            return _render("company", s={**company_values(), **request.form.to_dict()})
        db.session.commit()
        return _next("company")
    return _render("company", s=company_values())


@bp.route("/invoicing", methods=["GET", "POST"])
def invoicing():
    if request.method == "POST":
        errors = save_series_form()
        if errors:
            for e in errors:
                flash(e, "error")
            return _render("invoicing", s={**Setting.group("invoice"), **request.form.to_dict()})
        db.session.commit()
        return _next("invoicing")
    s = Setting.group("invoice")
    year = date.today().year
    for kind in ("efatura", "earsiv"):  # show what is set up: "continue after <last number>"
        if int(s.get("start_year") or 0) == year and int(s[kind + "_start"] or 1) > 1:
            s[kind + "_last"] = f"{s[kind + '_prefix']}{year}{int(s[kind + '_start']) - 1:09d}"
    return _render("invoicing", s=s)


@bp.route("/efatura", methods=["GET", "POST"])
def efatura():
    if request.method == "POST":
        if f_str("integrator") == INTEGRATOR:
            save_integrator_form(INTEGRATOR, xml=False)
            db.session.commit()
            if request.form.get("test") == "1":
                flash_connection_test(INTEGRATOR)
                return redirect(url_for("wizard.efatura"))
        else:  # decide later: practise with simulated sending, nothing reaches GİB
            Setting.set("integrator.name", "mock")
            db.session.commit()
        return _next("efatura")
    cfg = (Setting.get("integrator.config") or {}).get(INTEGRATOR, {})
    chosen = db.session.get(Setting, "integrator.name")  # None until someone picked one
    return _render("efatura", cfg=cfg, uyumsoft=chosen is None or Setting.get("integrator.name") == INTEGRATOR)


def _suggested_backup_folders():
    """Cloud-synced folders on this PC (Windows sets these variables for OneDrive)."""
    out = []
    for var in ("OneDriveCommercial", "OneDrive"):
        base = os.environ.get(var)
        if base and os.path.isdir(base):
            path = os.path.join(base, "Atolye-Yedek")
            if path not in out:
                out.append(path)
    return out


@bp.route("/backup", methods=["GET", "POST"])
def backup():
    from ..services import backup as backup_svc

    if request.method == "POST":
        Setting.set("backup.enabled", f_bool("enabled"))
        error = save_backup_folder("extra_dir", f_str("extra_dir"))
        if error:
            flash(error, "error")
            return _render("backup", s={**Setting.group("backup"), **request.form.to_dict()},
                           suggestions=_suggested_backup_folders())
        db.session.commit()
        if f_bool("backup_now"):  # proves the second folder works
            try:
                path = backup_svc.create_backup(label="manual")
                backup_svc.copy_to_extra(path)
                flash(_("Backup created: {name}", name=os.path.basename(path)), "success")
            except Exception as e:  # show any filesystem error to the user
                flash(_("Backup failed: {err}", err=str(e)), "error")
                return redirect(url_for("wizard.backup"))
        return _next("backup")
    return _render("backup", s=Setting.group("backup"), suggestions=_suggested_backup_folders())


@bp.route("/done")
def done():
    from ..services import branding
    from ..services.integrators import REGISTRY
    from ..services.invoicing import InvoiceError, next_invoice_number

    Setting.set("setup.pending", False)
    db.session.commit()
    c = Setting.group("company")
    numbers = {}
    for profile in ("TICARIFATURA", "EARSIVFATURA"):
        try:
            numbers[profile] = next_invoice_number(profile, date.today().year)
        except InvoiceError:
            numbers[profile] = None
    key = Setting.get("integrator.name") or "mock"
    cfg = (Setting.get("integrator.config") or {}).get(key, {})
    return _render("done", company=c, has_logo_file=bool(branding.logo_path()), numbers=numbers,
                   integrator=REGISTRY[key].label if key in REGISTRY else key, integrator_key=key,
                   environment=cfg.get("environment", "test"), b=Setting.group("backup"),
                   data_dir=current_app.config["DATA_DIR"])
