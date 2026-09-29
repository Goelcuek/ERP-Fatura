"""Install and exercise the built Windows package on a real Windows machine (GitHub Actions).

Run with the package's own Python, from anywhere:

    C:\\Atolye\\python\\python.exe scripts\\smoke_windows.py C:\\Atolye

Set ERP_NO_MODEL_DOWNLOAD=1 so the AI model (several GB) is not downloaded. Exits non-zero on the
first failed check; every step is printed so the Actions log shows what worked.
"""

import os
import socket
import subprocess
import sys
import time

BASE = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else r"C:\Atolye")
PY = os.path.join(BASE, "python", "python.exe")
failures = []


def check(label, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + label + (f"  ({detail})" if detail else ""), flush=True)
    if not ok:
        failures.append(label)
    return ok


def winsetup(*args, timeout=180):
    res = subprocess.run([PY, "-m", "app.winsetup", *args], cwd=BASE, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=timeout)
    print(res.stdout + res.stderr, flush=True)
    return res.returncode


def port_open(port, host="127.0.0.1"):
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def main():
    import requests  # the package's own copy

    sys.path.insert(0, BASE)
    from app import winsetup as ws
    from app.tls import lan_addresses

    print(f"== package at {BASE}", flush=True)
    check("bundled Python runs the app code", True)
    out = subprocess.run([os.path.join(BASE, "vendor", "ollama", "ollama.exe"), "--version"], capture_output=True,
                         text=True, timeout=60)
    check("bundled ollama.exe starts", out.returncode == 0, (out.stdout + out.stderr).strip()[:80])

    print("== Kur.bat (install)", flush=True)
    check("install exits cleanly", winsetup("install", "--no-browser") == 0)
    paths = ws.Paths(BASE)
    check("startup shortcut created", os.path.exists(paths.startup_lnk), paths.startup_lnk)
    check("desktop shortcut created", os.path.exists(paths.desktop_url), paths.desktop_url)
    if os.path.exists(paths.startup_lnk):
        import comtypes.client

        sc = comtypes.client.CreateObject("WScript.Shell", dynamic=True).CreateShortcut(paths.startup_lnk)
        check("startup shortcut starts pythonw run.py --https --background",
              sc.TargetPath.lower() == paths.pythonw.lower() and "--background" in sc.Arguments,
              f"{sc.TargetPath} {sc.Arguments}")
    if os.path.exists(paths.desktop_url):
        check("desktop shortcut opens http://localhost:8080",
              "URL=http://localhost:8080/" in open(paths.desktop_url, encoding="mbcs").read())

    print("== the running app", flush=True)
    r = requests.get("http://localhost:8080/", allow_redirects=False, timeout=30)
    check("this PC: http://localhost:8080 serves the app (no https redirect)",
          r.status_code == 302 and r.headers["Location"].endswith("/setup"), f"{r.status_code} {r.headers.get('Location')}")
    r = requests.get("http://localhost:8080/setup", timeout=30)
    check("setup wizard page loads", r.status_code == 200 and "_csrf" in r.text, str(r.status_code))
    ca = os.path.join(BASE, "data", "tls", "ca.crt")
    r = requests.get("https://127.0.0.1:8443/setup", verify=ca, timeout=30)
    check("https with the shop certificate verifies", r.status_code == 200, str(r.status_code))
    lan = lan_addresses()
    if lan:
        ip = lan[0]
        r = requests.get(f"http://{ip}:8080/setup", allow_redirects=False, timeout=30)
        check(f"other devices ({ip}) are sent to https",
              r.status_code == 302 and r.headers["Location"] == f"https://{ip}:8443/setup", r.headers.get("Location"))
        r = requests.get(f"https://{ip}:8443/setup", verify=ca, timeout=30)
        check(f"https certificate covers the LAN address {ip}", r.status_code == 200, str(r.status_code))
        r = requests.get(f"http://{ip}:8080/connect/ca.crt", timeout=30)
        check("phones can download the certificate", r.status_code == 200 and r.content[:1] == b"\x30")
    else:
        print("      (no LAN address on this machine: LAN checks skipped)")
    r = requests.get("https://127.0.0.1:8443/setup", verify=ca, headers={"Host": "attacker.example"}, timeout=30)
    check("foreign Host header is refused", r.status_code == 400, str(r.status_code))

    print("== Telefon-Izni.bat (firewall, as administrator)", flush=True)
    check("firewall permission added", winsetup("firewall") == 0)
    shown = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", "name=Atolye ERP"], capture_output=True,
                           text=True, errors="replace")
    check("firewall rule exists", shown.returncode == 0 and "Atolye ERP" in shown.stdout)

    print("== Durdur.bat (stop)", flush=True)
    winsetup("stop")
    deadline = time.time() + 20
    while port_open(8080) and time.time() < deadline:
        time.sleep(1)
    check("app stopped", not port_open(8080) and not port_open(8443))

    print("== Kaldir.bat (uninstall, as administrator)", flush=True)
    check("uninstall exits cleanly", winsetup("uninstall") == 0)
    check("startup shortcut removed", not os.path.exists(paths.startup_lnk))
    check("desktop shortcut removed", not os.path.exists(paths.desktop_url))
    shown = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", "name=Atolye ERP"], capture_output=True,
                           text=True, errors="replace")
    check("firewall rule removed", shown.returncode != 0)
    check("data folder kept", os.path.isdir(os.path.join(BASE, "data")))

    print(f"\n{'ALL CHECKS PASSED' if not failures else 'FAILED: ' + ', '.join(failures)}", flush=True)
    log = os.path.join(BASE, "data", "logs", "server.log")
    if failures and os.path.exists(log):
        print("---- data/logs/server.log ----\n" + open(log, encoding="utf-8", errors="replace").read()[-6000:])
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
