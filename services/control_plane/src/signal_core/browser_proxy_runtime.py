"""Credential-free forward-proxy transport; policy executes over a private pipe."""

import base64
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

MAX_LINE = 8 * 1024 * 1024


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:
        self.forward()

    def do_HEAD(self) -> None:
        self.forward()

    def do_CONNECT(self) -> None:
        self.send_error(403)

    def do_POST(self) -> None:
        self.send_error(403)

    do_PUT = do_POST
    do_DELETE = do_POST
    do_PATCH = do_POST
    do_OPTIONS = do_POST

    def forward(self) -> None:
        if (
            len(self.path) > 2048
            or self.headers.get("Transfer-Encoding")
            or self.headers.get("Content-Length", "0") != "0"
            or any(
                self.headers.get(header)
                for header in ("Authorization", "Cookie", "Proxy-Authorization", "Upgrade")
            )
        ):
            self.send_error(403)
            return
        # Nothing from browser headers is propagated to the public request.
        print(json.dumps({"method": self.command, "url": self.path}), flush=True)
        line = sys.stdin.buffer.readline(MAX_LINE + 1)
        if not line.endswith(b"\n") or len(line) > MAX_LINE:
            self.send_error(503)
            raise RuntimeError("BROWSER_PROXY_PIPE_UNAVAILABLE")
        result = json.loads(line)
        body = base64.b64decode(result["body"], validate=True)
        self.send_response(result["status"])
        self.send_header("Content-Type", result["media_type"])
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


def main() -> None:
    HTTPServer(("0.0.0.0", 8080), ProxyHandler).serve_forever(poll_interval=0.1)


if __name__ == "__main__":
    main()
