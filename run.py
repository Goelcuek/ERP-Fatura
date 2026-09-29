"""Start the application server.

    python run.py                   # http://0.0.0.0:8080, reachable from other PCs on the LAN
    python run.py --https           # https on 8443 (+ phone set-up helper on 8080): microphone & home-screen app
    python run.py --port 9000
    python run.py --host 127.0.0.1  # this computer only
    pythonw run.py --https --background   # no window: logs to data/logs/server.log (Windows autostart)
"""

import argparse
import logging
import os
import socket
import sys

from app import create_app
from app.services.backup import start_scheduler


def start_assistant_model(app):
    """Start the bundled Ollama (if shipped) and download the model on first run, in the background."""
    if os.environ.get("ERP_NO_MODEL_DOWNLOAD"):  # e.g. automated install tests
        return
    with app.app_context():
        from app.services.assistant import agent, ollama

        cfg = agent.settings()
        if not (cfg["enabled"] and cfg["use_llm"]):
            return
        mgr = ollama.manager(app)
        if cfg["auto_download"]:
            mgr.ensure_model_async(ollama.api_root(cfg["effective_base_url"]), cfg["model"])
        elif mgr.bundled and cfg["server"] == "auto":
            import threading

            threading.Thread(target=mgr.start, daemon=True).start()


def exit_on_signals():
    """Turn terminate / console-close signals into a normal exit so cleanup (e.g. stopping Ollama) runs."""
    import signal
    import sys

    def handler(signum, frame):
        sys.exit(0)

    for name in ("SIGTERM", "SIGHUP", "SIGBREAK"):  # SIGBREAK: console window closed on Windows
        sig = getattr(signal, name, None)
        if sig is not None:
            signal.signal(sig, handler)


def port_in_use(host, port):
    with socket.socket() as s:
        try:
            s.bind(("0.0.0.0" if host in ("", "0.0.0.0") else host, port))
            return False
        except OSError:
            return True


def go_background(data_dir):
    """Log to a file instead of a console (pythonw has none) and record the process id for Durdur.bat."""
    logs = os.path.join(data_dir, "logs")
    os.makedirs(logs, exist_ok=True)
    log = os.path.join(logs, "server.log")
    if os.path.exists(log) and os.path.getsize(log) > 5 * 1024 * 1024:
        os.replace(log, log + ".1")
    out = open(log, "a", buffering=1, encoding="utf-8")
    sys.stdout = sys.stderr = out
    pid_file = os.path.join(data_dir, "server.pid")
    with open(pid_file, "w") as fh:
        fh.write(str(os.getpid()))

    import atexit

    def remove_pid():
        try:
            with open(pid_file) as fh:
                if fh.read().strip() == str(os.getpid()):
                    os.remove(pid_file)
        except OSError:
            pass

    atexit.register(remove_pid)


def start_http_helper(app, host, http_port, https_port, threads=4):
    """Plain-http companion of the https server: phone set-up page, certificate download, redirects."""
    import threading

    from waitress import create_server

    from app.services.mobile import http_helper

    try:
        server = create_server(http_helper(app, https_port), host=host, port=http_port, threads=threads)
    except OSError as e:
        logging.warning("http helper not started on port %s: %s", http_port, e)
        return False
    threading.Thread(target=server.run, name="http-helper", daemon=True).start()
    return True


def main():
    parser = argparse.ArgumentParser(description="Atölye ERP server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=None, help="default 8080, or 8443 with --https")
    parser.add_argument("--http-port", type=int, default=8080, help="with --https: phone set-up helper port (0 = off)")
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--https", action="store_true", help="serve over https with the shop's own certificate")
    parser.add_argument("--background", action="store_true", help="no console: log to data/logs/server.log")
    args = parser.parse_args()
    port = args.port or (8443 if args.https else 8080)

    if args.background:
        from app import BASE_DIR

        data_dir = os.path.abspath(os.environ.get("ERP_DATA_DIR", os.path.join(BASE_DIR, "data")))
        os.makedirs(data_dir, exist_ok=True)
        if port_in_use(args.host, port):  # e.g. a second Windows user logging in: one server is enough
            return
        go_background(data_dir)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    app = create_app()
    http_port = args.http_port if args.https and args.http_port and args.http_port != port else None
    app.config["SERVE"] = {"https": args.https, "port": port, "http_port": http_port if args.https else port}
    exit_on_signals()
    start_scheduler(app)
    start_assistant_model(app)

    from app.tls import lan_addresses

    scheme = "https" if args.https else "http"
    print(f"\n  Atölye ERP is running.\n  This computer:  {scheme}://127.0.0.1:{port}")
    for ip in lan_addresses():
        print(f"  Other devices:  {scheme}://{ip}:{port}")
    print(f"  Phones:         {scheme}://127.0.0.1:{port}/connect  (QR codes)")
    print(f"  Data folder:    {app.config['DATA_DIR']}\n")

    if args.https:
        from cheroot import wsgi
        from cheroot.ssl.builtin import BuiltinSSLAdapter

        from app.tls import ensure_cert

        cert, key = ensure_cert(app.config["DATA_DIR"])
        if http_port and not start_http_helper(app, args.host, http_port, port):
            app.config["SERVE"]["http_port"] = None
        server = wsgi.Server((args.host, port), app, numthreads=args.threads)
        server.ssl_adapter = BuiltinSSLAdapter(cert, key)
        try:
            server.start()
        except (KeyboardInterrupt, SystemExit):  # Ctrl+C, or a terminate signal (see exit_on_signals)
            server.stop()  # its worker threads would otherwise keep the process alive
    else:
        from waitress import serve

        serve(app, host=args.host, port=port, threads=args.threads)


if __name__ == "__main__":
    main()
