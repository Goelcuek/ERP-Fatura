"""Phones and tablets: connection addresses, QR codes, home-screen app icons and the http helper.

In https mode (run.py --https) the app also answers on the plain http port with a tiny helper:
it serves the phone set-up page and the shop certificate (a phone can't open https pages it
doesn't trust yet) and redirects everything else to the https address.
"""

import io
import os
import re
import unicodedata
from functools import lru_cache
from urllib.parse import quote

from flask import current_app, request

HTTP_PORT = 8080
HTTPS_PORT = 8443
ACCENT_TOP, ACCENT_BOTTOM = (59, 130, 246), (29, 78, 216)  # same gradient as the favicon

# pages the http helper serves itself instead of redirecting to https
HELPER_PATHS = ("/connect/start", "/connect/ca.crt", "/branding/logo")
HELPER_PREFIXES = ("/static/", "/app-icon/")


def serve_info():
    """How this server is reachable: {"https": bool, "port": int, "http_port": int|None}.

    run.py records it at start; otherwise (flask run, tests) it is derived from the request.
    """
    info = current_app.config.get("SERVE")
    if info:
        return info
    port = request.host.rsplit(":", 1)[1] if ":" in request.host.rsplit("]", 1)[-1] else None
    https = request.scheme == "https"
    port = int(port) if port and port.isdigit() else (443 if https else 80)
    return {"https": https, "port": port, "http_port": None if https else port}


def request_ip():
    """The address the viewer used to reach us, if it is an IP (phones must use the same network)."""
    host = request.host.rsplit(":", 1)[0].strip("[]") if request.host.count(":") <= 1 else ""
    return host if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", host) else None


def addresses():
    """LAN IPs phones can use, the one the viewer is on first."""
    from ..tls import lan_addresses

    ips = lan_addresses()
    here = request_ip()
    if here and not here.startswith("127."):
        ips = [here] + [ip for ip in ips if ip != here]
    return ips


def app_url(ip):
    info = serve_info()
    scheme = "https" if info["https"] else "http"
    default = 443 if info["https"] else 80
    port = "" if info["port"] == default else f":{info['port']}"
    return f"{scheme}://{ip}{port}/"


def start_url(ip):
    """Phone set-up page, reachable before the phone trusts the certificate (http helper)."""
    info = serve_info()
    if info["https"] and info.get("http_port"):
        return f"http://{ip}:{info['http_port']}/connect/start"
    return app_url(ip) + "connect/start"


# ---------------------------------------------------------------- QR codes

@lru_cache(maxsize=64)
def qr_svg(text):
    """Inline SVG QR code (dark modules on a white quiet zone, scales with CSS)."""
    import qrcode
    import qrcode.image.svg

    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2,
                      error_correction=qrcode.constants.ERROR_CORRECT_M)
    svg = img.to_string(encoding="unicode")
    svg = re.sub(r'\s(width|height)="[^"]*"', "", svg, count=2)
    return svg.replace("<svg ", '<svg class="qr" role="img" shape-rendering="crispEdges" ', 1)


# ---------------------------------------------------------------- app icons

def _font(size):
    from PIL import ImageFont

    windir = os.environ.get("WINDIR", r"C:\Windows")
    candidates = [os.path.join(windir, "Fonts", f) for f in ("segoeuib.ttf", "arialbd.ttf")]
    candidates += ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                   "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                   "/System/Library/Fonts/Supplemental/Arial Bold.ttf"]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size), True
            except OSError:
                continue
    return ImageFont.load_default(size=size), False  # Latin only: Ç → C


def _ascii(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text.replace("İ", "I")) if not unicodedata.combining(c))


def render_icon(size, initials, logo=None, maskable=False, rounded=True):
    """PNG bytes of the app icon: the company logo on white, or the monogram on the brand gradient.

    maskable: full-bleed, content inside the central 80% safe zone (Android crops it to its shape).
    rounded=False: square without transparency (iOS rounds the corners itself).
    """
    from PIL import Image, ImageDraw

    scale = 4 if size <= 256 else 2  # draw big, shrink: smooth edges
    s = size * scale
    bg = Image.new("RGB", (s, s))
    if logo:
        bg.paste((255, 255, 255), (0, 0, s, s))
    else:
        top, bottom = ACCENT_TOP, ACCENT_BOTTOM
        grad = ImageDraw.Draw(bg)
        for y in range(s):
            t = y / (s - 1)
            grad.line([(0, y), (s, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    safe = 0.62 if maskable else 0.74  # share of the icon the content may use
    if logo:
        with Image.open(io.BytesIO(logo)) as im:
            im = im.convert("RGBA")
            ratio = s * safe / max(im.width, im.height)  # fit the safe zone, up or down
            im = im.resize((max(1, round(im.width * ratio)), max(1, round(im.height * ratio))), Image.LANCZOS)
            bg.paste(im, ((s - im.width) // 2, (s - im.height) // 2), im)
    else:
        font, unicode_ok = _font(int(s * (0.36 if len(initials) > 1 else 0.46)))
        text = initials if unicode_ok else _ascii(initials)
        draw = ImageDraw.Draw(bg)
        box = draw.textbbox((0, 0), text, font=font)
        vbox = draw.textbbox((0, 0), _ascii(text), font=font)  # centre the letters, not the cedilla
        w, h = box[2] - box[0], vbox[3] - vbox[1]
        draw.text(((s - w) / 2 - box[0], (s - h) / 2 - vbox[1]), text, font=font, fill=(255, 255, 255))
    out = bg
    if rounded and not maskable:
        mask = Image.new("L", (s, s), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.22), fill=255)
        out = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        out.paste(bg, (0, 0), mask)
    out = out.resize((size, size), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue()


_icon_cache = {}


def app_icon(size, variant="any"):
    """Cached icon for the current branding (the cache key changes when the logo or name does)."""
    from . import branding

    logo_file = branding.logo_path()
    initials = branding.initials(branding.short_name())
    key = (size, variant, initials, logo_file, os.path.getmtime(logo_file) if logo_file else 0)
    if key not in _icon_cache:
        logo = None
        if logo_file:
            with open(logo_file, "rb") as fh:
                logo = fh.read()
        if len(_icon_cache) > 32:
            _icon_cache.clear()
        _icon_cache[key] = render_icon(size, initials, logo, maskable=variant == "maskable",
                                       rounded=variant == "any")
    return _icon_cache[key]


def windows_ico(path, initials, logo=None):
    """Write a multi-size .ico for Windows shortcuts."""
    from PIL import Image

    big = Image.open(io.BytesIO(render_icon(256, initials, logo)))
    big.save(path, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


# ---------------------------------------------------------------- http helper (https mode)

def http_helper(app, https_port):
    """WSGI app for the plain http port while serving https: phone set-up page + redirects."""

    def wsgi(environ, start_response):
        path = environ.get("PATH_INFO") or "/"
        if path in HELPER_PATHS or path.startswith(HELPER_PREFIXES):
            return app(environ, start_response)
        host = environ.get("HTTP_HOST") or environ.get("SERVER_NAME") or "localhost"
        host = host.rsplit(":", 1)[0] if not host.endswith("]") else host
        query = environ.get("QUERY_STRING")
        # PATH_INFO arrives latin-1 decoded (PEP 3333): re-quote the original bytes
        location = f"https://{host}:{https_port}{quote(path.encode('latin-1'))}" + (f"?{query}" if query else "")
        start_response("302 Found", [("Location", location), ("Content-Length", "0"), ("Cache-Control", "no-store")])
        return [b""]

    return wsgi
