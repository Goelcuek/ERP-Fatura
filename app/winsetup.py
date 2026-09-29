"""Windows set-up for the workshop PC, kept to what an ordinary program does so antivirus stays calm.

    python -m app.winsetup install      # autostart + desktop shortcut + start (Kur.bat, no admin rights)
    python -m app.winsetup uninstall    # undo that (Kaldir.bat); data is kept
    python -m app.winsetup stop         # stop the background app (Durdur.bat)
    python -m app.winsetup firewall     # allow phones through Windows Firewall (Telefon-Izni.bat, as admin)
    python -m app.winsetup firewall --remove
    python -m app.winsetup install --dry-run   # only print what would be done

What install does — for the current Windows user only, no administrator rights, no PowerShell:
  1. creates the shop certificate in data/tls (used by phones; nothing is added to Windows);
  2. puts a shortcut in the user's Startup folder: the app starts in the background at log-in;
  3. puts an “Atölye” shortcut on the desktop, opening http://localhost:8080 (on this PC the
     browser allows the microphone and the app install without any certificate);
  4. starts the app and opens it.
Phones reach the app through Windows Firewall: Windows asks once when the app starts; if that was
missed or refused, Telefon-Izni.bat (run as administrator) adds the permission.
"""

import argparse
import os
import subprocess
import sys
import time

from . import BASE_DIR

RULE_NAME = "Atolye ERP"
SHORTCUT_NAME = "Atolye"
HTTP_PORT, HTTPS_PORT = 8080, 8443
LOCAL_URL = f"http://localhost:{HTTP_PORT}/"

# Windows known folders (per user; follows OneDrive/redirected folders)
FOLDERID_STARTUP = "{B97D20BB-F46A-4C97-BA10-5E3608430854}"
FOLDERID_DESKTOP = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"


def known_folder(guid):
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]

    g = GUID()
    ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p(guid), ctypes.byref(g))
    path = ctypes.c_wchar_p()
    if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(path)) != 0:
        raise OSError(f"known folder {guid} not found")
    try:
        return path.value
    finally:
        ctypes.windll.ole32.CoTaskMemFree(path)


class Paths:
    def __init__(self, base_dir=BASE_DIR, env=None, startup_dir=None, desktop_dir=None):
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
        if startup_dir is None or desktop_dir is None:
            if os.name == "nt":
                startup_dir = startup_dir or known_folder(FOLDERID_STARTUP)
                desktop_dir = desktop_dir or known_folder(FOLDERID_DESKTOP)
            else:  # --dry-run elsewhere: typical locations, for display only
                home = env.get("USERPROFILE", r"C:\Users\user")
                startup_dir = startup_dir or os.path.join(home, "AppData", "Roaming", "Microsoft", "Windows",
                                                          "Start Menu", "Programs", "Startup")
                desktop_dir = desktop_dir or os.path.join(home, "Desktop")
        self.startup_lnk = os.path.join(startup_dir, SHORTCUT_NAME + ".lnk")
        self.desktop_url = os.path.join(desktop_dir, SHORTCUT_NAME + ".url")
        self.pid = os.path.join(self.data, "server.pid")
        # left behind by the first version of the installer (machine-wide); removed on uninstall
        program_data = env.get("ProgramData", r"C:\ProgramData")
        public = env.get("PUBLIC", r"C:\Users\Public")
        self.legacy = [os.path.join(program_data, "Microsoft", "Windows", "Start Menu", "Programs", "StartUp",
                                    SHORTCUT_NAME + ".lnk"),
                       os.path.join(public, "Desktop", SHORTCUT_NAME + ".url")]


# ---------------------------------------------------------------- command builders (pure, testable)

def cmds_firewall(programs):
    """Allow the app's ports from the local network only (also on Wi-Fi marked 'public')."""
    cmds = [["netsh", "advfirewall", "firewall", "delete", "rule", f"name={RULE_NAME}"]]
    for prog in programs:
        cmds.append(["netsh", "advfirewall", "firewall", "add", "rule", f"name={RULE_NAME}", "dir=in", "action=allow",
                     f"program={prog}", "protocol=TCP", f"localport={HTTP_PORT},{HTTPS_PORT}",
                     "remoteip=localsubnet", "profile=any", "enable=yes"])
    return cmds


def cmds_firewall_remove():
    return [["netsh", "advfirewall", "firewall", "delete", "rule", f"name={RULE_NAME}"]]


def server_args(paths):
    return f'"{paths.run_py}" --https --background'


def url_file_content(url, icon):
    return f"[InternetShortcut]\r\nURL={url}\r\nIconFile={icon}\r\nIconIndex=0\r\n"


def cmd_kill(pid):
    # the image-name filter makes sure a recycled process id never hits another program
    return ["taskkill", "/PID", str(pid), "/T", "/F", "/FI", "IMAGENAME eq pythonw.exe"]


def make_shortcut(lnk, target, arguments, workdir, icon):
    """A normal Windows shortcut (.lnk), written through the Windows Script Host object — no PowerShell."""
    import comtypes.client

    shell = comtypes.client.CreateObject("WScript.Shell", dynamic=True)
    sc = shell.CreateShortcut(lnk)
    sc.TargetPath = target
    sc.Arguments = arguments
    sc.WorkingDirectory = workdir
    sc.IconLocation = icon
    sc.Description = "Atölye ERP"
    sc.Save()


def is_admin():
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


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

    def step(self, label, fn, *args):
        if self.dry_run:
            self.out(f"   > {label}")
            return
        try:
            fn(*args)
        except Exception as e:  # report and carry on with the other steps
            self.failed.append(label)
            self.out(f"   ! {label}: {e}")


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


def _write_url(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="mbcs" if os.name == "nt" else "utf-8", errors="replace") as fh:
        fh.write(content)


def _make_icon(paths):
    from .services.mobile import windows_ico

    initials, logo = _branding(paths)
    windows_ico(paths.icon, initials, logo)


def install(paths, runner, open_app=True):
    from .tls import ensure_cert

    say = runner.out
    say("1/4  Telefonlar için sertifika hazırlanıyor…")
    os.makedirs(paths.data, exist_ok=True)
    runner.step("sertifika", ensure_cert, paths.data)
    runner.step("simge", _make_icon, paths)

    say("2/4  Windows açılınca otomatik başlatma ayarlanıyor…")
    runner.step(paths.startup_lnk, make_shortcut, paths.startup_lnk, paths.pythonw, server_args(paths), paths.base,
                paths.icon)

    say("3/4  Masaüstüne “Atölye” kısayolu ekleniyor…")
    runner.step(paths.desktop_url, _write_url, paths.desktop_url, url_file_content(LOCAL_URL, paths.icon))

    say("4/4  Uygulama başlatılıyor…")
    if not runner.dry_run:
        start_server(paths)
        if wait_for_port(HTTP_PORT, 90):
            if open_app:
                open_browser(LOCAL_URL)
        else:
            say("   ! Uygulama 90 saniyede açılmadı; ayrıntı: data\\logs\\server.log")
            runner.failed.append("start")
    say("")
    if runner.failed:
        say("Kurulum tamamlandı, ancak bazı adımlar başarısız oldu: " + ", ".join(runner.failed))
    else:
        say("Kurulum tamamlandı. Uygulama tarayıcıda açılıyor (masaüstündeki “Atölye” kısayolu).")
        say("Windows güvenlik duvarı Python için izin sorarsa “İzin ver”e tıklayın: telefonlar bu sayede bağlanır.")
    return not runner.failed


def uninstall(paths, runner):
    say = runner.out
    say("Uygulama durduruluyor…")
    stop(paths, runner)
    say("Kısayollar siliniyor…")
    for f in [paths.startup_lnk, paths.desktop_url, *paths.legacy]:
        if runner.dry_run:
            say(f"   x {f}")
            continue
        try:
            os.remove(f)
        except FileNotFoundError:
            pass
        except OSError as e:  # e.g. an old machine-wide shortcut without admin rights
            say(f"   ! {f}: {e}")
    if is_admin() or runner.dry_run:
        say("Güvenlik duvarı izni kaldırılıyor…")
        for cmd in cmds_firewall_remove():
            runner.run(cmd, "netsh", quiet_fail=True)
    else:
        say("Not: Telefon-Izni.bat ile güvenlik duvarı izni verildiyse, onu da kaldırmak için Kaldir.bat")
        say("     dosyasına sağ tıklayıp “Yönetici olarak çalıştır”ı seçin.")
    say("Kaldırıldı. Verileriniz (data klasörü) silinmedi.")
    return True


def firewall(paths, runner, remove=False):
    if not runner.dry_run and not is_admin():
        runner.out("Bu işlem yönetici izni gerektirir: dosyaya sağ tıklayıp “Yönetici olarak çalıştır”ı seçin.")
        return False
    if remove:
        for cmd in cmds_firewall_remove():
            runner.run(cmd, "netsh", quiet_fail=True)
        runner.out("Telefon izni kaldırıldı.")
        return True
    for cmd in cmds_firewall([paths.python, paths.pythonw]):
        runner.run(cmd, "netsh", quiet_fail="delete" in cmd)
    if not runner.failed:
        runner.out("Telefonlar artık bu bilgisayara bağlanabilir (yalnızca aynı yerel ağdan).")
    return not runner.failed


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
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([paths.pythonw, paths.run_py, "--https", "--background"], cwd=paths.base, creationflags=flags,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
    parser.add_argument("action", choices=["install", "uninstall", "stop", "firewall"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--remove", action="store_true", help="firewall: remove the permission")
    parser.add_argument("--no-browser", action="store_true", help="install: don't open the browser")
    args = parser.parse_args(argv)
    if os.name != "nt" and not args.dry_run:
        parser.error("Windows only (use --dry-run to see the steps)")
    try:  # Turkish text in the Windows console
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    paths = Paths()
    runner = Runner(dry_run=args.dry_run)
    if args.action == "install":
        ok = install(paths, runner, open_app=not args.no_browser)
    elif args.action == "firewall":
        ok = firewall(paths, runner, remove=args.remove)
    else:
        ok = {"uninstall": uninstall, "stop": stop}[args.action](paths, runner)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
