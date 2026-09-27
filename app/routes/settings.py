import os
import tempfile
from dataclasses import fields

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file, url_for
from werkzeug.utils import secure_filename

from ..extensions import db
from ..i18n import LANGUAGES, _
from ..models import Setting, User
from ..services import backup as backup_svc
from ..services.integrators import CAPABILITIES, REGISTRY, IntegratorError, XmlOptions
from ..web import admin_required
from . import f_bool, f_int, f_str

bp = Blueprint("settings", __name__, url_prefix="/settings")

COMPANY_FIELDS = ["name", "tax_id", "tax_office", "mersis", "trade_registry", "address", "district", "city",
                  "postal_code", "country", "phone", "email", "website", "iban"]


@bp.before_request
@admin_required
def _admin_only():
    return None


@bp.route("/", methods=["GET", "POST"])
def company():
    if request.method == "POST":
        for f in COMPANY_FIELDS:
            v = f_str(f)
            if f == "tax_id":
                v = "".join(ch for ch in v if ch.isdigit())
            Setting.set(f"company.{f}", v)
        from ..services import branding

        try:
            upload = request.files.get("logo")
            if upload and upload.filename:
                branding.save_logo(upload.read())
            elif request.form.get("remove_logo"):
                branding.remove_logo()
        except branding.BrandingError as e:
            db.session.rollback()
            flash(_(str(e)), "error")
            return redirect(url_for("settings.company"))
        db.session.commit()
        flash(_("Company details saved."), "success")
        return redirect(url_for("settings.company"))
    s = Setting.group("company")
    if not s.get("name"):
        s["name"] = current_app.config["CUSTOMER"].get("company_name", "")
    return render_template("settings/company.html", s=s, section="company")


@bp.route("/invoicing", methods=["GET", "POST"])
def invoicing():
    if request.method == "POST":
        errors = []
        for key in ("efatura_prefix", "earsiv_prefix"):
            v = f_str(key).upper()
            if len(v) != 3 or not v.isalnum():
                errors.append(_("Series prefixes must be exactly 3 letters or digits."))
                break
        if f_str("efatura_prefix").upper() == f_str("earsiv_prefix").upper():
            errors.append(_("e-Fatura and e-Arşiv series must be different."))
        if errors:
            for e in errors:
                flash(e, "error")
            return redirect(url_for("settings.invoicing"))
        Setting.set("invoice.efatura_prefix", f_str("efatura_prefix").upper())
        Setting.set("invoice.earsiv_prefix", f_str("earsiv_prefix").upper())
        Setting.set("invoice.efatura_start", max(1, f_int("efatura_start", 1)))
        Setting.set("invoice.earsiv_start", max(1, f_int("earsiv_start", 1)))
        Setting.set("invoice.default_profile", f_str("default_profile") or "TICARIFATURA")
        Setting.set("invoice.default_vat", f_int("default_vat", 20))
        Setting.set("invoice.default_due_days", f_int("default_due_days", 0))
        Setting.set("invoice.footer_note", f_str("footer_note"))
        Setting.set("orders.prefix", (f_str("orders_prefix") or "SRV").upper())
        Setting.set("orders.terms", f_str("orders_terms"))
        db.session.commit()
        flash(_("Settings saved."), "success")
        return redirect(url_for("settings.invoicing"))
    return render_template("settings/invoicing.html", s=Setting.group("invoice"), o=Setting.group("orders"),
                           section="invoicing")


@bp.route("/integrator", methods=["GET", "POST"])
def integrator():
    all_cfg = Setting.get("integrator.config") or {}
    if request.method == "POST":
        key = f_str("integrator")
        if key not in REGISTRY:
            abort(400)
        cls = REGISTRY[key]
        cfg = dict(all_cfg.get(key, {}))
        for fld in cls.fields:
            name = f"{key}__{fld.name}"
            if fld.kind == "checkbox":
                cfg[fld.name] = "1" if request.form.get(name) else "0"
                continue
            val = request.form.get(name, "")
            if fld.kind == "password" and not val:
                continue  # keep stored secret when the field is left blank
            cfg[fld.name] = val.strip()
        # XML options: store only deviations from the integrator's defaults
        for opt in fields(XmlOptions):
            chosen = bool(request.form.get(f"{key}__xml_{opt.name}"))
            if chosen == getattr(cls.xml_defaults, opt.name):
                cfg.pop("xml_" + opt.name, None)
            else:
                cfg["xml_" + opt.name] = "1" if chosen else "0"
        all_cfg[key] = cfg
        Setting.set("integrator.config", all_cfg)
        Setting.set("integrator.name", key)
        db.session.commit()
        if request.form.get("test") == "1":
            try:
                msg = cls(config=cfg, data_dir=current_app.config["DATA_DIR"]).test_connection()
                flash(_("Connection OK: {msg}", msg=_(msg)), "success")
            except IntegratorError as e:
                flash(_("Connection failed: {err}", err=_(str(e))), "error")
        else:
            flash(_("Integrator settings saved."), "success")
        return redirect(url_for("settings.integrator", show=key))
    current = Setting.get("integrator.name") or "mock"
    instances = {k: cls(config=all_cfg.get(k, {}), data_dir=current_app.config["DATA_DIR"])
                 for k, cls in REGISTRY.items()}
    return render_template("settings/integrator.html", registry=REGISTRY, instances=instances, current=current,
                           shown=request.args.get("show") if request.args.get("show") in REGISTRY else current,
                           configs=all_cfg, xml_fields=[f.name for f in fields(XmlOptions)],
                           xml_labels=XmlOptions.LABELS, capabilities=CAPABILITIES, section="integrator")


@bp.route("/assistant", methods=["GET", "POST"])
def assistant():
    from ..services.assistant import agent, ollama, speech

    if request.method == "POST":
        Setting.set("assistant.enabled", f_bool("enabled"))
        Setting.set("assistant.use_llm", f_bool("use_llm"))
        Setting.set("assistant.server", "custom" if f_str("server") == "custom" else "auto")
        Setting.set("assistant.auto_download", f_bool("auto_download"))
        Setting.set("assistant.base_url", f_str("base_url") or Setting.DEFAULTS["assistant.base_url"])
        Setting.set("assistant.model", f_str("model") or Setting.DEFAULTS["assistant.model"])
        if request.form.get("api_key"):
            Setting.set("assistant.api_key", f_str("api_key"))
        try:
            Setting.set("assistant.temperature", min(max(float(f_str("temperature") or 0.2), 0.0), 1.5))
        except ValueError:
            pass
        Setting.set("assistant.timeout", max(10, f_int("timeout", 120)))
        Setting.set("voice.engine", f_str("voice_engine") if f_str("voice_engine") in ("auto", "browser", "local", "off")
                    else "auto")
        Setting.set("voice.lang", f_str("voice_lang") or "tr-TR")
        Setting.set("voice.whisper_model", f_str("whisper_model") or "small")
        Setting.set("voice.speak_replies", f_bool("speak_replies"))
        Setting.set("voice.auto_send", f_bool("auto_send"))
        db.session.commit()
        cfg = agent.settings()
        if cfg["use_llm"] and cfg["auto_download"]:
            ollama.manager().ensure_model_async(ollama.api_root(cfg["effective_base_url"]), cfg["model"])
        if request.form.get("test") == "1":
            ok, msg = agent.health()
            flash(_(msg) if ok else _("Connection failed: {err}", err=_(msg)), "success" if ok else "error")
        else:
            flash(_("Settings saved."), "success")
        return redirect(url_for("settings.assistant"))
    cfg = agent.settings()
    return render_template("settings/assistant.html", s=Setting.group("assistant"), v=Setting.group("voice"),
                           cfg=cfg, whisper=speech.available(), section="assistant")


@bp.route("/assistant/model", methods=["GET", "POST"])
def assistant_model():
    """JSON model status for the settings page; POST starts the server and the download."""
    from ..services.assistant import agent, ollama

    cfg = agent.settings()
    root = ollama.api_root(cfg["effective_base_url"])
    if request.method == "POST":
        ollama.manager().ensure_model_async(root, cfg["model"])
    st = agent.model_status(cfg)
    st["server_url"] = cfg["effective_base_url"]
    st["using_bundled"] = cfg["bundled"] and cfg["server"] == "auto"
    return st


@bp.route("/users")
def users():
    return render_template("settings/users.html", users=User.query.order_by(User.full_name).all(), section="users")


@bp.route("/users/new", methods=["GET", "POST"])
@bp.route("/users/<int:uid>", methods=["GET", "POST"])
def user_form(uid=None):
    u = db.session.get(User, uid) if uid else User(role="staff", is_technician=True, active=True, lang="tr")
    if uid and u is None:
        abort(404)
    if request.method == "POST":
        username = f_str("username").lower()
        pw = request.form.get("password", "")
        dup = User.query.filter(User.username == username, User.id != (u.id or 0)).first()
        if not username or dup:
            flash(_("Username is empty or already taken."), "error")
        elif not uid and len(pw) < 8:
            flash(_("Password must be at least 8 characters."), "error")
        elif pw and len(pw) < 8:
            flash(_("Password must be at least 8 characters."), "error")
        elif u.id == g.user.id and (f_str("role") != "admin" or not f_bool("active")):
            flash(_("You cannot remove your own admin rights or deactivate yourself."), "error")
        else:
            u.username = username
            u.full_name = f_str("full_name")
            u.role = "admin" if f_str("role") == "admin" else "staff"
            u.is_technician = f_bool("is_technician")
            u.active = f_bool("active")
            u.lang = f_str("lang") if f_str("lang") in LANGUAGES else "tr"
            if pw:
                u.set_password(pw)
            if not uid:
                db.session.add(u)
            db.session.commit()
            flash(_("User saved."), "success")
            return redirect(url_for("settings.users"))
    return render_template("settings/user_form.html", u=u, section="users")


# ---------------------------------------------------------------- backups


@bp.route("/backup", methods=["GET", "POST"])
def backup():
    if request.method == "POST":
        Setting.set("backup.enabled", f_bool("enabled"))
        Setting.set("backup.interval_hours", max(1, f_int("interval_hours", 24)))
        Setting.set("backup.keep", max(1, f_int("keep", 30)))
        for key in ("dir", "extra_dir"):
            path = f_str(key)
            if path:
                try:
                    os.makedirs(path, exist_ok=True)
                    if not os.access(path, os.W_OK):
                        raise OSError("not writable")
                except OSError as e:
                    flash(_("Folder is not usable: {path} ({err})", path=path, err=str(e)), "error")
                    return redirect(url_for("settings.backup"))
            Setting.set(f"backup.{key}", path)
        db.session.commit()
        flash(_("Backup settings saved."), "success")
        return redirect(url_for("settings.backup"))
    return render_template(
        "settings/backup.html",
        s=Setting.group("backup"),
        backups=backup_svc.list_backups(),
        location=backup_svc.backup_dir(),
        data_dir=current_app.config["DATA_DIR"],
        section="backup",
    )


@bp.route("/backup/create", methods=["POST"])
def backup_create():
    try:
        path = backup_svc.create_backup(label="manual")
        backup_svc.copy_to_extra(path)
        flash(_("Backup created: {name}", name=os.path.basename(path)), "success")
    except Exception as e:  # show any filesystem error to the user
        flash(_("Backup failed: {err}", err=str(e)), "error")
    return redirect(url_for("settings.backup"))


def _backup_path(name):
    name = secure_filename(name)
    path = os.path.join(backup_svc.backup_dir(), name)
    if not name.startswith(backup_svc.PREFIX) or not os.path.exists(path):
        abort(404)
    return path


@bp.route("/backup/download/<name>")
def backup_download(name):
    return send_file(_backup_path(name), as_attachment=True, download_name=name)


@bp.route("/backup/download-now")
def backup_download_now():
    path = backup_svc.create_backup(label="download")
    return send_file(path, as_attachment=True, download_name=os.path.basename(path))


@bp.route("/backup/delete/<name>", methods=["POST"])
def backup_delete(name):
    os.remove(_backup_path(name))
    flash(_("Backup deleted."), "success")
    return redirect(url_for("settings.backup"))


@bp.route("/backup/restore", methods=["POST"])
def backup_restore():
    if request.form.get("confirm") != "RESTORE":
        flash(_("Type RESTORE to confirm."), "error")
        return redirect(url_for("settings.backup"))
    name = request.form.get("name")
    upload = request.files.get("file")
    tmp_path = None
    try:
        if upload and upload.filename:
            fd, tmp_path = tempfile.mkstemp(suffix=".zip")
            os.close(fd)
            upload.save(tmp_path)
            path = tmp_path
        elif name:
            path = _backup_path(name)
        else:
            flash(_("Choose a backup to restore."), "error")
            return redirect(url_for("settings.backup"))
        manifest, safety = backup_svc.restore(path)
        flash(_("Restored backup from {date}. A safety backup of the previous data was saved as {name}.",
                date=manifest.get("created_at", "?"), name=os.path.basename(safety)), "success")
    except backup_svc.BackupError as e:
        flash(_("Restore failed: {err}", err=_(str(e))), "error")
        return redirect(url_for("settings.backup"))
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
    return redirect(url_for("auth.login"))
