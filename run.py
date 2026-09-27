"""Start the application server.

    python run.py                 # http://0.0.0.0:8080, reachable from other PCs on the LAN
    python run.py --port 9000
    python run.py --host 127.0.0.1  # this computer only
"""

import argparse
import logging
import socket

from app import create_app
from app.services.backup import start_scheduler


def main():
    parser = argparse.ArgumentParser(description="Atölye ERP server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--threads", type=int, default=8)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    app = create_app()
    start_scheduler(app)

    try:
        lan_ip = socket.gethostbyname(socket.gethostname())
    except OSError:
        lan_ip = "127.0.0.1"
    print(f"\n  Atölye ERP is running.\n  This computer:  http://127.0.0.1:{args.port}\n"
          f"  Other devices:  http://{lan_ip}:{args.port}\n  Data folder:    {app.config['DATA_DIR']}\n")

    from waitress import serve

    serve(app, host=args.host, port=args.port, threads=args.threads)


if __name__ == "__main__":
    main()
