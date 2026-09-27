"""Phones & tablets: set-up page with QR codes, shop certificate download and the home-screen app
(web app manifest, icons, service worker, offline page)."""

from flask import Blueprint, Response, abort, current_app, jsonify, render_template, request, url_for

from .. import __version__
from ..i18n import _
from ..models import Setting
from ..services import branding, mobile

bp = Blueprint("mobile", __name__)

ICONS = {"192": (192, "any"), "512": (512, "any"), "maskable-512": (512, "maskable"), "apple-180": (180, "apple")}


def _platform():
    ua = request.headers.get("User-Agent", "")
    if any(k in ua for k in ("iPhone", "iPad", "iPod")):
        return "ios"
    if "Android" in ua:
        return "android"
    return None


@bp.route("/connect")
def connect():
    """Shown on the PC: scan with the phone to set it up and open the app."""
    ips = mobile.addresses()
    chosen = request.args.get("ip")
    ip = chosen if chosen in ips else (ips[0] if ips else None)
    info = mobile.serve_info()
    ctx = {"ips": ips, "ip": ip, "info": info}
    if ip:
        ctx.update(start_url=mobile.start_url(ip), app_url=mobile.app_url(ip))
        ctx.update(start_qr=mobile.qr_svg(ctx["start_url"]), app_qr=mobile.qr_svg(ctx["app_url"]))
    return render_template("mobile/connect.html", **ctx)


@bp.route("/connect/start")
def start():
    """Opened on the phone (over plain http in https mode): install the certificate, then open the app."""
    info = mobile.serve_info()
    ip = mobile.request_ip() or (mobile.addresses() or ["127.0.0.1"])[0]
    from ..tls import ca_display_name

    return render_template("mobile/start.html", platform=_platform(), info=info, app_url=mobile.app_url(ip),
                           ca_name=ca_display_name(current_app.config["DATA_DIR"]))


@bp.route("/connect/ca.crt")
def ca():
    from ..tls import ca_der

    data = ca_der(current_app.config["DATA_DIR"])
    if request.args.get("save"):
        # Android installs CA certificates only from Settings: save the file to Downloads
        return Response(data, mimetype="application/octet-stream",
                        headers={"Content-Disposition": 'attachment; filename="Atolye-sertifika.crt"',
                                 "Cache-Control": "no-store"})
    # iPhone/iPad: Safari offers to install the profile
    return Response(data, mimetype="application/x-x509-ca-cert",
                    headers={"Content-Disposition": 'inline; filename="Atolye-sertifika.crt"', "Cache-Control": "no-store"})


@bp.route("/manifest.webmanifest")
def manifest():
    name = branding.short_name()
    icons = [
        {"src": url_for("mobile.icon", name="192"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": url_for("mobile.icon", name="512"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": url_for("mobile.icon", name="maskable-512"), "sizes": "512x512", "type": "image/png",
         "purpose": "maskable"},
    ]
    shortcuts = [{"name": _("New service order"), "url": url_for("orders.new"),
                  "icons": [{"src": url_for("mobile.icon", name="192"), "sizes": "192x192"}]}]
    if Setting.get("assistant.enabled"):
        shortcuts.insert(0, {"name": _("Assistant"), "url": url_for("assistant.page"),
                             "icons": [{"src": url_for("mobile.icon", name="192"), "sizes": "192x192"}]})
    data = {
        "id": "/",
        "name": name,
        "short_name": name if len(name) <= 12 else branding.initials(name),
        "description": _("Service & invoicing"),
        "lang": "tr",
        "start_url": url_for("dashboard.index"),
        "scope": "/",
        "display": "standalone",
        "orientation": "any",
        "background_color": "#f6f7f9",
        "theme_color": "#2563eb",
        "icons": icons,
        "shortcuts": shortcuts,
    }
    resp = jsonify(data)
    resp.mimetype = "application/manifest+json"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@bp.route("/app-icon/<name>.png")
def icon(name):
    if name not in ICONS:
        abort(404)
    size, variant = ICONS[name]
    resp = Response(mobile.app_icon(size, variant), mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@bp.route("/apple-touch-icon.png")
@bp.route("/apple-touch-icon-precomposed.png")
def apple_icon():
    return icon("apple-180")


@bp.route("/sw.js")
def service_worker():
    js = render_template("mobile/sw.js", version=__version__, offline_url=url_for("mobile.offline"))
    resp = Response(js, mimetype="text/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@bp.route("/offline")
def offline():
    return render_template("mobile/offline.html")
