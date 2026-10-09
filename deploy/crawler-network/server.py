"""Synthetic HTTP origin used only inside the isolated crawler network lab."""

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        host = self.headers.get("Host", "")
        if host != "provider.example":
            self._send(404, "application/json", b'{"error":"not found"}')
            return
        if self.path == "/v1/private-redirect":
            self.send_response(307)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path not in {"/v1/decision", "/v1/assistant", "/token"}:
            self._send(404, "application/json", b'{"error":"not found"}')
            return
        length = self.headers.get("Content-Length", "")
        if not length.isdigit() or int(length) > 1024:
            self._send(400, "application/json", b'{"error":"invalid length"}')
            return
        request_body = self.rfile.read(int(length))
        if self.path == "/token":
            if self.headers.get("Content-Type") != "application/x-www-form-urlencoded":
                self._send(415, "application/json", b'{"error":"invalid media"}')
                return
            submitted = parse_qs(request_body.decode("ascii"), strict_parsing=True)
            body = json.dumps(
                {
                    "grant_type": submitted.get("grant_type"),
                    "has_client_secret": "client_secret" in submitted,
                    "has_code_verifier": "code_verifier" in submitted,
                    "cookie": self.headers.get("Cookie"),
                },
                sort_keys=True,
            ).encode()
            self._send(200, "application/json", body)
            return
        try:
            submitted = json.loads(request_body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send(400, "application/json", b'{"error":"invalid json"}')
            return
        body = json.dumps(
            {
                "authorization_present": self.headers.get("Authorization") is not None,
                "content_type": self.headers.get("Content-Type"),
                "cookie": self.headers.get("Cookie"),
                **(
                    {"google_api_key_present": self.headers.get("X-Goog-Api-Key") is not None}
                    if self.path == "/v1/assistant"
                    else {}
                ),
                "submitted": submitted,
            },
            sort_keys=True,
        ).encode()
        self._send(200, "application/json", body)

    def do_GET(self) -> None:
        host = self.headers.get("Host", "")
        if host == "provider.example" and self.path.startswith("/webmaster/api.svc/json/"):
            body = json.dumps(
                {
                    "method": self.path.split("?", 1)[0].rsplit("/", 1)[-1],
                    "authorization_present": self.headers.get("Authorization") is not None,
                    "cookie": self.headers.get("Cookie"),
                }
            ).encode()
            self._send(200, "application/json", body)
            return
        if host == "provider.example" and self.path == "/webmasters/v3/sites":
            self._send(200, "application/json", b'{"siteEntry":[]}')
            return
        if host == "live.example":
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "http://other.example/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path == "/timeout":
                time.sleep(3)
                return
            if self.path in {"/", "/stale"}:
                value = (
                    b'<html><head><meta name="description" content="Exact evidence"></head></html>'
                    if self.path == "/"
                    else b"<html><head></head></html>"
                )
                self._send(200, "text/html; charset=utf-8", value)
                return
        if self.path == "/.well-known/signal-site-verification.txt":
            if host == "proof.example":
                self._send(
                    200,
                    "text/plain; charset=utf-8",
                    b"signal-site-verification=network-lab\n",
                )
                return
            if host == "proof-redirect.example":
                self.send_response(302)
                self.send_header(
                    "Location",
                    "http://proof.example/.well-known/signal-site-verification.txt",
                )
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        if self.path == "/robots.txt":
            if host == "robots.example":
                self._send(
                    200,
                    "text/plain; charset=utf-8",
                    b"User-agent: SignalBot\nDisallow: /private\n",
                )
                return
            if host == "robots-redirect.example":
                self.send_response(302)
                self.send_header("Location", "http://robots.example/robots.txt")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if host == "robots-unsafe.example":
                self.send_response(302)
                self.send_header("Location", "http://169.254.169.254/robots.txt")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            statuses = {
                "robots-missing.example": 404,
                "robots-forbidden.example": 403,
                "robots-backoff.example": 429,
                "robots-failure.example": 503,
            }
            if host in statuses:
                self._send(statuses[host], "text/plain", b"status body")
                return
        if self.path == "/start":
            self.send_response(302)
            self.send_header("Location", "/final?b=2&a=1#ignored")
            self.send_header("Set-Cookie", "never-forward=this")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/final?b=2&a=1":
            self._send(200, "text/html; charset=utf-8", b"<html><title>Lab</title></html>")
            return
        if self.path == "/echo":
            body = json.dumps(
                {
                    "accept_encoding": self.headers.get("Accept-Encoding"),
                    "authorization": self.headers.get("Authorization"),
                    "cookie": self.headers.get("Cookie"),
                    "host": self.headers.get("Host"),
                    "user_agent": self.headers.get("User-Agent"),
                },
                sort_keys=True,
            ).encode()
            self._send(200, "text/html", body)
            return
        if self.path == "/private-redirect":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/oversized":
            self._send(200, "text/html", b"x" * 2049, include_length=False)
            return
        if self.path == "/compressed":
            body = b"not-actually-compressed"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._send(404, "text/html", b"not found")

    def _send(
        self,
        status: int,
        media_type: str,
        body: bytes,
        *,
        include_length: bool = True,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", media_type)
        if include_length:
            self.send_header("Content-Length", str(len(body)))
        else:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 80), Handler).serve_forever()
