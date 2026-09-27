"""Company branding: display name, monogram and logo (app, receipts, printouts and e-invoices).

Installation defaults come from ``customer.json`` in the project folder (or the file named by
``ERP_CUSTOMER_FILE``); the values saved in Settings always win.
"""

import base64
import json
import os
import re

from flask import current_app, has_app_context

LOGO_TYPES = {"png": b"\x89PNG\r\n\x1a\n", "jpg": b"\xff\xd8\xff"}
MIME = {"png": "image/png", "jpg": "image/jpeg"}
MAX_LOGO_BYTES = 300 * 1024  # the logo is embedded in every e-invoice, keep it small
LEGAL_SUFFIX = re.compile(
    r"\s+(san\.?|sanayi|tic\.?|ticaret|ltd\.?|limited|a\.?\s?ş\.?|anonim|şti\.?|şirketi)(\s|$).*$", re.I)


class BrandingError(Exception):
    pass


def load_customer_preset(base_dir):
    path = os.environ.get("ERP_CUSTOMER_FILE") or os.path.join(base_dir, "customer.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return {k: v for k, v in data.items() if not k.startswith("_")}
    except (OSError, ValueError):
        return {}


def preset():
    return current_app.config.get("CUSTOMER", {}) if has_app_context() else {}


def company_name():
    from ..models import Setting

    return Setting.get("company.name") or preset().get("company_name") or "Atölye"


def short_name(name=None):
    """Name without legal suffixes, for the sidebar: 'Çağ-Tek Makina San. ve Tic. Ltd. Şti.' → 'Çağ-Tek Makina'."""
    if name is None:
        p = preset()
        from ..models import Setting

        saved = Setting.get("company.name")
        if not saved and p.get("short_name"):
            return p["short_name"]
        name = saved or p.get("company_name") or "Atölye"
    return LEGAL_SUFFIX.sub("", name).strip() or name


def initials(name):
    words = [w for w in re.split(r"[\s\-]+", name) if w and w[0].isalnum()]
    return "".join(w[0] for w in words[:2]).upper() or "A"


# ---------------------------------------------------------------- logo

def _logo_rel():
    from ..models import Setting

    return Setting.get("company.logo") or ""


def logo_path():
    rel = _logo_rel()
    if not rel:
        return None
    path = os.path.join(current_app.config["DATA_DIR"], rel)
    return path if os.path.exists(path) else None


def logo_kind(data):
    for kind, magic in LOGO_TYPES.items():
        if data.startswith(magic):
            return kind
    return None


def save_logo(data):
    from ..models import Setting

    if len(data) > MAX_LOGO_BYTES:
        raise BrandingError("The logo must be smaller than 300 KB.")
    kind = logo_kind(data)
    if kind is None:
        raise BrandingError("The logo must be a PNG or JPG image.")
    remove_logo()
    rel = os.path.join("files", "branding", f"logo.{kind}")
    path = os.path.join(current_app.config["DATA_DIR"], rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    Setting.set("company.logo", rel)


def remove_logo():
    from ..models import Setting

    path = logo_path()
    if path:
        os.remove(path)
    Setting.set("company.logo", "")


def logo_data_uri():
    path = logo_path()
    if not path:
        return None
    with open(path, "rb") as fh:
        data = fh.read()
    kind = logo_kind(data) or "png"
    return f"data:{MIME[kind]};base64,{base64.b64encode(data).decode()}"
