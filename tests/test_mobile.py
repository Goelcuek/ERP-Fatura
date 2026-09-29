"""Phones & tablets: shop certificate authority, set-up page, home-screen app, http helper, Windows set-up."""

import io
import json
import socket
import ssl
import threading

import pytest

from app import tls
from app.services import mobile

LAN = ["127.0.0.1", "192.168.1.50"]


@pytest.fixture
def lan(monkeypatch):
    names = ["localhost", "werkstatt", "werkstatt.local"]
    monkeypatch.setattr(tls, "local_addresses", lambda: (names, list(LAN)))
    return LAN


def handshake(data_dir, cert, key, hostname):
    """Real TLS handshake against a client that trusts only the shop CA (OpenSSL enforces name constraints)."""
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(cert, key)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def serve():
        conn, _ = listener.accept()
        try:
            server_ctx.wrap_socket(conn, server_side=True).close()
        except (ssl.SSLError, OSError):
            conn.close()

    threading.Thread(target=serve, daemon=True).start()
    client_ctx = ssl.create_default_context(cafile=tls.ca_cert_path(data_dir))
    try:
        with client_ctx.wrap_socket(socket.create_connection(listener.getsockname()), server_hostname=hostname):
            return True
    except ssl.SSLCertVerificationError:
        return False
    finally:
        listener.close()


# ---------------------------------------------------------------- certificates

def test_shop_ca_signs_a_trusted_server_certificate(tmp_path, lan):
    d = str(tmp_path)
    cert, key = tls.ensure_cert(d)
    for host in ("127.0.0.1", "192.168.1.50", "localhost", "werkstatt.local"):
        assert handshake(d, cert, key, host), host
    assert not handshake(d, cert, key, "192.168.1.99")  # not an address of this PC
    assert tls.ensure_cert(d) == (cert, key)
    assert "Atölye" in tls.ca_display_name(d)
    assert len(tls.ca_fingerprint(d)) == 40


def test_new_address_reissues_certificate_but_keeps_the_ca(tmp_path, lan, monkeypatch):
    d = str(tmp_path)
    cert, key = tls.ensure_cert(d)
    ca_before = open(tls.ca_cert_path(d), "rb").read()
    leaf_before = open(cert, "rb").read()
    monkeypatch.setattr(tls, "local_addresses", lambda: (["localhost"], ["127.0.0.1", "10.0.0.7"]))
    cert, key = tls.ensure_cert(d)
    assert open(cert, "rb").read() != leaf_before
    assert open(tls.ca_cert_path(d), "rb").read() == ca_before  # phones keep trusting it
    assert handshake(d, cert, key, "10.0.0.7")


def test_ca_cannot_vouch_for_internet_sites(tmp_path, lan):
    """Name constraints: even with the CA key, a certificate for an internet name is refused."""
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    d = str(tmp_path)
    ca, ca_key = tls.ensure_ca(d)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc)
    rogue = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "bank.example")]))
             .issuer_name(ca.subject).public_key(key.public_key()).serial_number(1)
             .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
             .add_extension(x509.SubjectAlternativeName([x509.DNSName("bank.example"),
                                                         x509.IPAddress(__import__("ipaddress").ip_address("8.8.8.8"))]),
                            critical=False)
             .sign(ca_key, hashes.SHA256()))
    (tmp_path / "rogue.pem").write_bytes(rogue.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "rogue.key").write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                                           serialization.PrivateFormat.TraditionalOpenSSL,
                                                           serialization.NoEncryption()))
    assert not handshake(d, str(tmp_path / "rogue.pem"), str(tmp_path / "rogue.key"), "bank.example")


def test_only_private_addresses_go_into_certificates(monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: "Shop_PC")  # underscore: not a valid DNS name
    names, ips = tls.local_addresses()
    assert names == ["localhost"]
    assert all(tls._is_private(ip) for ip in ips)
    assert not tls._is_private("8.8.8.8") and tls._is_private("172.20.1.1")


# ---------------------------------------------------------------- pages

def test_connect_page_shows_qr_codes(app, client, lan):
    app.config["SERVE"] = {"https": True, "port": 8443, "http_port": 8080}
    r = client.get("/connect")
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert html.count('<svg class="qr"') == 2
    assert "http://192.168.1.50:8080/connect/start" in html
    assert "https://192.168.1.50:8443/" in html


def test_connect_page_without_https_warns(app, client, lan):
    app.config["SERVE"] = {"https": False, "port": 8080, "http_port": 8080}
    html = client.get("/connect").get_data(as_text=True)
    assert html.count('<svg class="qr"') == 1
    assert "http://192.168.1.50:8080/" in html
    assert "Kur.bat" in html


def test_connect_page_needs_login(app):
    r = app.test_client().get("/connect")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_phone_start_page_is_public_and_platform_aware(app, lan):
    app.config["SERVE"] = {"https": True, "port": 8443, "http_port": 8080}
    c = app.test_client()
    iphone = c.get("/connect/start", headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)"},
                   base_url="http://192.168.1.50:8080").get_data(as_text=True)
    android = c.get("/connect/start", headers={"User-Agent": "Mozilla/5.0 (Linux; Android 15; Pixel 9)"},
                    base_url="http://192.168.1.50:8080").get_data(as_text=True)
    assert iphone.index("iPhone / iPad") < iphone.index("Android")
    assert android.index("Android") < android.index("iPhone / iPad")
    assert "https://192.168.1.50:8443/" in iphone
    assert "Sertifika Güven Ayarları" in iphone and "Atölye ERP Yerel Sertifika Kurumu" in iphone


def test_certificate_download(app):
    c = app.test_client()
    r = c.get("/connect/ca.crt")
    assert r.status_code == 200 and r.mimetype == "application/x-x509-ca-cert"
    assert r.data[:1] == b"\x30"  # DER
    r = c.get("/connect/ca.crt?save=1")
    assert "attachment" in r.headers["Content-Disposition"]


def test_manifest_icons_and_service_worker(app):
    c = app.test_client()
    m = json.loads(c.get("/manifest.webmanifest").data)
    assert m["display"] == "standalone" and m["start_url"] == "/"
    assert m["name"] == "Test Servis"
    assert {i["purpose"] for i in m["icons"]} == {"any", "maskable"}
    assert any(s["url"] == "/assistant/" for s in m["shortcuts"])
    from PIL import Image

    for icon in m["icons"]:
        r = c.get(icon["src"])
        assert r.mimetype == "image/png"
        size = int(icon["sizes"].split("x")[0])
        assert Image.open(io.BytesIO(r.data)).size == (size, size)
    apple = Image.open(io.BytesIO(c.get("/apple-touch-icon.png").data))
    assert apple.size == (180, 180) and apple.mode == "RGB"  # iOS: no transparency
    sw = c.get("/sw.js")
    assert sw.mimetype == "text/javascript" and "/offline" in sw.get_data(as_text=True)
    offline = c.get("/offline").get_data(as_text=True)
    assert "Atölye bilgisayarına ulaşılamıyor" in offline


def test_pages_link_the_manifest(client):
    html = client.get("/").get_data(as_text=True)
    assert 'rel="manifest"' in html and "serviceWorker" in html and "/connect" in html


def test_icon_uses_logo_when_set(app):
    from PIL import Image

    from app.models import Setting
    from app.services import branding

    buf = io.BytesIO()
    Image.new("RGB", (40, 20), (200, 0, 0)).save(buf, "PNG")
    with app.test_request_context():
        branding.save_logo(buf.getvalue())
        Setting.set("company.name", "Test Servis Ltd. Şti.")
        png = mobile.app_icon(192, "maskable")
    center = Image.open(io.BytesIO(png)).convert("RGB").getpixel((96, 96))
    corner = Image.open(io.BytesIO(png)).convert("RGB").getpixel((2, 2))
    assert center[0] > 150 and center[1] < 60  # the red logo
    assert corner == (255, 255, 255)


def test_http_helper_redirects_to_https_but_serves_setup(app):
    helper = mobile.http_helper(app, 8443)
    calls = []

    def start_response(status, headers):
        calls.append((status, dict(headers)))

    env = {"REQUEST_METHOD": "GET", "PATH_INFO": "/orders/5", "QUERY_STRING": "tab=x", "HTTP_HOST": "192.168.1.50:8080",
           "SERVER_NAME": "x", "SERVER_PORT": "8080", "wsgi.url_scheme": "http", "wsgi.input": io.BytesIO(),
           "wsgi.errors": io.StringIO(), "SERVER_PROTOCOL": "HTTP/1.1"}
    helper(env, start_response)
    assert calls[-1][0].startswith("302") and calls[-1][1]["Location"] == "https://192.168.1.50:8443/orders/5?tab=x"
    helper(dict(env, PATH_INFO="/contacts/ş".encode().decode("latin-1"), QUERY_STRING=""), start_response)
    assert calls[-1][1]["Location"] == "https://192.168.1.50:8443/contacts/%C5%9F"
    body = b"".join(helper(dict(env, PATH_INFO="/connect/ca.crt", QUERY_STRING=""), start_response))
    assert calls[-1][0].startswith("200") and body[:1] == b"\x30"


def test_http_helper_serves_this_pc_directly(app):
    """On the PC itself (loopback) the whole app is served over http://localhost — no redirect, no certificate."""
    helper = mobile.http_helper(app, 8443)
    calls = []
    env = {"REQUEST_METHOD": "GET", "PATH_INFO": "/login", "QUERY_STRING": "", "HTTP_HOST": "localhost:8080",
           "SERVER_NAME": "localhost", "SERVER_PORT": "8080", "wsgi.url_scheme": "http", "wsgi.input": io.BytesIO(),
           "wsgi.errors": io.StringIO(), "SERVER_PROTOCOL": "HTTP/1.1", "REMOTE_ADDR": "127.0.0.1"}
    body = b"".join(helper(env, lambda status, headers: calls.append(status)))
    assert calls[-1].startswith("200") and b"<form" in body
    helper(dict(env, REMOTE_ADDR="::1"), lambda status, headers: calls.append(status))
    assert calls[-1].startswith("200")
    # another device on the LAN is still sent to https
    helper(dict(env, REMOTE_ADDR="192.168.1.77", HTTP_HOST="192.168.1.50:8080"),
           lambda status, headers: calls.append((status, dict(headers))))
    assert calls[-1][0].startswith("302") and calls[-1][1]["Location"].startswith("https://192.168.1.50:8443/")


def test_session_cookie_secure_only_over_https(app):
    c = app.test_client()
    plain = c.get("/login", base_url="http://localhost").headers.get("Set-Cookie", "")
    secure = app.test_client().get("/login", base_url="https://localhost").headers.get("Set-Cookie", "")
    assert "session=" in plain and "Secure" not in plain  # http://localhost on the PC
    assert "session=" in secure and "Secure" in secure    # phones over https


# ---------------------------------------------------------------- Windows set-up

def test_windows_setup_commands():
    from app import winsetup

    fw = winsetup.cmds_firewall([r"C:\Atölye ERP\python\pythonw.exe"])
    add = fw[-1]
    assert "remoteip=localsubnet" in add and "localport=8080,8443" in add and "dir=in" in add
    assert r"program=C:\Atölye ERP\python\pythonw.exe" in add
    assert fw[0] == ["netsh", "advfirewall", "firewall", "delete", "rule", "name=Atolye ERP"]  # only our own rule
    assert winsetup.cmd_kill(42) == ["taskkill", "/PID", "42", "/T", "/F", "/FI", "IMAGENAME eq pythonw.exe"]
    url = winsetup.url_file_content(winsetup.LOCAL_URL, r"C:\A\data\atolye.ico")
    assert url.startswith("[InternetShortcut]\r\nURL=http://localhost:8080/\r\n")


def test_windows_install_is_quiet_for_antivirus(tmp_path):
    """No elevation, no PowerShell, no certificate store, no firewall change during a normal install."""
    from app import winsetup

    env = {"ERP_DATA_DIR": str(tmp_path), "USERPROFILE": r"C:\Users\usta"}
    paths = winsetup.Paths(str(tmp_path), env)
    lines = []
    runner = winsetup.Runner(dry_run=True, out=lines.append)
    assert winsetup.install(paths, runner)
    text = "\n".join(lines).lower()
    for word in ("powershell", "certutil", "netsh", "runas", "encodedcommand"):
        assert word not in text, word
    norm = lambda p: p.replace("\\", "/")  # noqa: E731 (paths use / when this runs outside Windows)
    assert norm(paths.startup_lnk).endswith("Startup/Atolye.lnk") and "Users/usta" in norm(paths.startup_lnk)
    assert norm(paths.desktop_url).endswith("Users/usta/Desktop/Atolye.url")
    assert winsetup.server_args(paths).endswith("--https --background")


def test_windows_stop_and_firewall_dry_run(tmp_path):
    from app import winsetup

    paths = winsetup.Paths(str(tmp_path), {"ERP_DATA_DIR": str(tmp_path)})
    lines = []
    runner = winsetup.Runner(dry_run=True, out=lines.append)
    (tmp_path / "server.pid").write_text("4242")
    winsetup.stop(paths, runner)
    assert any("taskkill /PID 4242 /T /F" in line for line in lines)
    lines.clear()
    assert winsetup.firewall(paths, runner)
    assert any("netsh advfirewall firewall add rule" in line for line in lines)
