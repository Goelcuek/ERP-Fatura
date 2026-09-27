"""Start the application server.

    python run.py                   # http://0.0.0.0:8080, reachable from other PCs on the LAN
    python run.py --https           # https on port 8443: needed for the microphone on phones/tablets
    python run.py --port 9000
    python run.py --host 127.0.0.1  # this computer only
"""

import argparse
import logging

from app import create_app
from app.services.backup import start_scheduler


def start_assistant_model(app):
    """Start the bundled Ollama (if shipped) and download the model on first run, in the background."""
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


def main():
    parser = argparse.ArgumentParser(description="Atölye ERP server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=None, help="default 8080, or 8443 with --https")
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--https", action="store_true", help="serve over https with a self-signed certificate")
    args = parser.parse_args()
    port = args.port or (8443 if args.https else 8080)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    app = create_app()
    exit_on_signals()
    start_scheduler(app)
    start_assistant_model(app)

    from app.tls import local_addresses

    scheme = "https" if args.https else "http"
    _names, ips = local_addresses()
    lan = [ip for ip in ips if not ip.startswith("127.")]
    print(f"\n  Atölye ERP is running.\n  This computer:  {scheme}://127.0.0.1:{port}")
    for ip in lan:
        print(f"  Other devices:  {scheme}://{ip}:{port}")
    print(f"  Data folder:    {app.config['DATA_DIR']}\n")

    if args.https:
        from cheroot import wsgi
        from cheroot.ssl.builtin import BuiltinSSLAdapter

        from app.tls import ensure_cert

        cert, key = ensure_cert(app.config["DATA_DIR"])
        app.config["SESSION_COOKIE_SECURE"] = True
        server = wsgi.Server((args.host, port), app, numthreads=args.threads)
        server.ssl_adapter = BuiltinSSLAdapter(cert, key)
        print("  First visit on each device shows a certificate warning: choose “Advanced → Continue”.\n")
        try:
            server.start()
        except KeyboardInterrupt:
            server.stop()
    else:
        from waitress import serve

        serve(app, host=args.host, port=port, threads=args.threads)


if __name__ == "__main__":
    main()
