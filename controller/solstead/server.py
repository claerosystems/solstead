"""TLS server. Run with: python3 -m solstead.server CONFIG or `solstead serve CONFIG`."""

from __future__ import annotations

import signal
import ssl
import sys
import threading
from http.server import ThreadingHTTPServer

from .api import Handler
from .config import Config
from .controller import Controller

SOCKET_TIMEOUT_SECONDS = 40


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, config: Config, controller: Controller | None = None):
        super().__init__((config.bind, config.port), Handler)
        self.controller = controller or Controller(config)
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.context.load_cert_chain(config.tls_cert, config.tls_key)

    def get_request(self):
        connection, address = self.socket.accept()
        connection.settimeout(SOCKET_TIMEOUT_SECONDS)
        try:
            return self.context.wrap_socket(connection, server_side=True), address
        except Exception:
            connection.close()
            raise


def serve(config: Config) -> None:
    with Server(config) as server:
        # systemd stops us with SIGTERM; shut down cleanly so ffmpeg children do not outlive us.
        def on_signal(*_):
            threading.Thread(target=server.shutdown, daemon=True).start()

        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, on_signal)
        server.controller.start()
        mode = "event recording" if config.events_enabled else "manual recording only"
        print(
            f"Solstead controller ready on https://{config.bind}:{config.port} ({mode})", flush=True
        )
        try:
            server.serve_forever()
        finally:
            server.controller.stop()
            print("Solstead controller stopped", flush=True)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: python3 -m solstead.server CONFIG", file=sys.stderr)
        return 2
    serve(Config.load(argv[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
