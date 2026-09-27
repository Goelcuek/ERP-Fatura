"""One-time Windows set-up for the workshop PC (run by Kur.bat as administrator).

    python -m app.winsetup install     # certificate trust, firewall, autostart, desktop shortcut, start
    python -m app.winsetup uninstall   # undo all of that (data is kept)
    python -m app.winsetup stop        # stop the background server (e.g. before an update)
    python -m app.winsetup install --dry-run   # only print what would be done

install:
  1. creates the shop certificate (data/tls) and adds its CA to Windows' trusted roots, so the
     browsers on this PC open https://127.0.0.1:8443 without a warning;
  2. opens the ports 8080/8443 in Windows Firewall for this app's Python, local network only;
  3. puts a shortcut in the common Startup folder: the app starts hidden (pythonw) at every log-in;
  4. puts an “Atölye” shortcut on the public desktop;
  5. starts the app now and opens the phone set-up page.
"""

import argparse
import base64
import os
import subprocess
import sys
import time

from . import BASE_DIR

RULE_NAME = "Atolye ERP"
SHORTCUT_NAME = "Atolye"
HTTP_PORT, HTTPS_PORT = 8080, 8443


class Paths:
    def __init__(self, base_dir=BASE_DIR, env=None):
        env = os.environ if env is None else env
        self.base = base_dir
        self.data = os.path.abspath(env.get("ERP_DATA_DIR") or os.path.join(base_dir, "data"))
        pydir = os.path.join(base_dir, "python")
        if not os.path.isdir(pydir):  # not the bundle: use the running interpreter
            pydir = os.path.dirname(sys.executable)
        self.python = os.path.join(pydir, "python.exe")
        self.pythonw = os.path.join(pydir, "pythonw.exe")
        self.run_py = os.path.join(base_dir, "run.py")
        self.icon = os.path.join(self.data, "atolye.ico")
        program_data = env.get("ProgramData", r"C:\ProgramData")
        self.startup_lnk = os.path.join(program_data, "Microsoft", "Windows", "Start Menu", "Programs", "StartUp",
                                        SHORTCUT_NAME + ".lnk")
        public = env.get("PUBLIC", r"C:\Users\Public")
        self.desktop_url = os.path.join(public, "Desktop", SHORTCUT_NAME + ".url")
        self.ca = os.path.join(self.data, "tls", "ca.crt")
        self.pid = os.path.join(self.data, "server.pid")


# ---------------------------------------------------------------- command builders (pure, testable)

def _ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def cmd_trust_ca(ca_path):
    return ["certutil", "-addstore", "-f", "Root", ca_path]


def cmd_untrust_ca(thumbprint):
    return ["certutil", "-delstore", "Root", thumbprint]


def cmds_firewall(programs):
    """Remove old rules for these programs (including 'blocked' answers to Windows' first-run prompt),
    then allow the app's ports from the local network only (works on 'public' Wi-Fi profiles too)."""
    cmds = []
    for prog in programs:
        cmds.append(["netsh", "advfirewall", "firewall", "delete", "rule", "name=all", f"program={prog}"])
    cmds.append(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={RULE_NAME}"])
    for prog in programs:
        cmds.append(["netsh", "advfirewall", "firewall", "add", "rule", f"name={RULE_NAME}", "dir=in", "action=allow",
                     f"program={prog}", "protocol=TCP", f"localport={HTTP_PORT},{HTTPS_PORT}",
                     "remoteip=localsubnet", "profile=any", "enable=yes"])
    return cmds


def cmds_firewall_remove():
    return [["netsh", "advfirewall", "firewall", "delete", "rule", f"name={RULE_NAME}"]]


def cmd_shortcut(lnk, target, arguments, workdir, icon):
    script = (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(lnk)}); "
              f"$s.TargetPath = {_ps_quote(target)}; $s.Arguments = {_ps_quote(arguments)}; "
              f"$s.WorkingDirectory = {_ps_quote(workdir)}; $s.IconLocation = {_ps_quote(icon)}; "
              "$s.WindowStyle = 7; $s.Save()")
    # encoded: no quoting problems with spaces, quotes or Turkish letters in paths
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    return ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded]


def server_args(paths):
    return f'"{paths.run_py}" --https --background'


def url_file_content(url, icon):
    return f"[InternetShortcut]\r\nURL={url}\r\nIconFile={icon}\r\nIconIndex=0\r\n"


def cmd_kill(pid):
    return ["taskkill", "/PID", str(pid), "/T", "/F"]


# ---------------------------------------------------------------- steps

class Runner:
    def __init__(self, dry_run=False, out=print):
        self.dry_run = dry_run
        self.out = out
        self.failed = []

    def run(self, cmd, label, quiet_fail=False):
        if self.dry_run:
            self.out("   $ " + subprocess.list2cmdline(cmd))
            return True
        res = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if res.returncode != 0 and not quiet_fail:
            self.failed.append(label)
            self.out(f"   ! {label}: {(res.stdout + res.stderr).strip()[:300]}")
        return res.returncode == 0


def _branding(paths):
    """Monogram and logo for the icon, read from the database without starting the web app."""
    os.environ.setdefault("ERP_DATA_DIR", paths.data)
    from . import create_app
    from .services import branding

    app = create_app()
    with app.app_context():
        initials = branding.initials(branding.short_name())
        logo_file = branding.logo_path()
        logo = None
        if logo_file:
            with open(logo_file, "rb") as fh:
                logo = fh.read()
    return initials, logo


def install(paths, runner):
    from .services.mobile import windows_ico
    from .tls import ensure_cert

    say = runner.out
    say("1/5  Sertifika oluşturuluyor ve bu bilgisayara tanıtılıyor…")
    os.makedirs(paths.data, exist_ok=True)
    if not runner.dry_run:
        ensure_cert(paths.data)
    runner.run(cmd_trust_ca(paths.ca), "certutil")

    say("2/5  Güvenlik duvarında telefonlar için izin veriliyor (yalnızca yerel ağ)…")
    for cmd in cmds_firewall([paths.python, paths.pythonw]):
        runner.run(cmd, "netsh", quiet_fail="delete" in cmd)

    say("3/5  Windows açılınca otomatik başlatma ayarlanıyor…")
    if not runner.dry_run:
        initials, logo = _branding(paths)
        windows_ico(paths.icon, initials, logo)
    runner.run(cmd_shortcut(paths.startup_lnk, paths.pythonw, server_args(paths), paths.base, paths.icon), "startup")

    say("4/5  Masaüstüne “Atölye” kısayolu ekleniyor…")
    content = url_file_content(f"https://127.0.0.1:{HTTPS_PORT}/", paths.icon)
    if runner.dry_run:
        say(f"   > {paths.desktop_url}")
    else:
        try:
            os.makedirs(os.path.dirname(paths.desktop_url), exist_ok=True)
            with open(paths.desktop_url, "w", encoding="mbcs" if os.name == "nt" else "utf-8", errors="replace") as fh:
                fh.write(content)
        except OSError as e:
            runner.failed.append("desktop")
            say(f"   ! {e}")

    say("5/5  Uygulama başlatılıyor…")
    if not runner.dry_run:
        start_server(paths)
        if wait_for_port(HTTPS_PORT, 60):
            open_browser(f"https://127.0.0.1:{HTTPS_PORT}/connect")
        else:
            say("   ! Uygulama 60 saniyede açılmadı; ayrıntı: data\\logs\\server.log")
            runner.failed.append("start")
    say("")
    if runner.failed:
        say("Kurulum tamamlandı, ancak bazı adımlar başarısız oldu: " + ", ".join(runner.failed))
    else:
        say("Kurulum tamamlandı. Telefonları bağlamak için açılan sayfadaki QR kodlarını kullanın.")
    return not runner.failed


def uninstall(paths, runner):
    from .tls import ca_fingerprint

    say = runner.out
    say("Uygulama durduruluyor…")
    stop(paths, runner)
    say("Güvenlik duvarı kuralları kaldırılıyor…")
    for cmd in cmds_firewall_remove():
        runner.run(cmd, "netsh", quiet_fail=True)
    if os.path.exists(paths.ca):
        say("Sertifika güvenilir listesinden çıkarılıyor…")
        runner.run(cmd_untrust_ca(ca_fingerprint(paths.data)), "certutil", quiet_fail=True)
    say("Kısayollar siliniyor…")
    for f in (paths.startup_lnk, paths.desktop_url):
        if runner.dry_run:
            say(f"   x {f}")
        else:
            try:
                os.remove(f)
            except FileNotFoundError:
                pass
    say("Kaldırıldı. Verileriniz (data klasörü) silinmedi.")
    return True


def stop(paths, runner):
    try:
        with open(paths.pid) as fh:
            pid = int(fh.read().strip())
    except (OSError, ValueError):
        runner.out("Arka planda çalışan uygulama bulunamadı.")
        return True
    runner.run(cmd_kill(pid), "taskkill", quiet_fail=True)  # /T also ends the bundled Ollama
    if not runner.dry_run:
        try:
            os.remove(paths.pid)
        except OSError:
            pass
    runner.out("Uygulama durduruldu.")
    return True


def start_server(paths):
    """Start through the Startup shortcut via Explorer: runs as the logged-in user, not as administrator."""
    if os.path.exists(paths.startup_lnk):
        subprocess.Popen(["explorer.exe", paths.startup_lnk])
    else:
        subprocess.Popen([paths.pythonw, paths.run_py, "--https", "--background"], cwd=paths.base,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))


def wait_for_port(port, timeout):
    import socket

    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(1)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(1)
    return False


def open_browser(url):
    import webbrowser

    webbrowser.open(url)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m app.winsetup", description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=["install", "uninstall", "stop"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if os.name != "nt" and not args.dry_run:
        parser.error("Windows only (use --dry-run to see the steps)")
    try:  # Turkish text in the Windows console
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    paths = Paths()
    runner = Runner(dry_run=args.dry_run)
    ok = {"install": install, "uninstall": uninstall, "stop": stop}[args.action](paths, runner)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
