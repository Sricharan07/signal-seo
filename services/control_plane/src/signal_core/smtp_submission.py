"""Closed TLS-only SMTP transport used exclusively by shared egress."""

import hashlib
import json
import re
import smtplib
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field

from signal_core.crawl_http import BoundedSystemResolver, _same_address
from signal_core.crawl_urls import validate_public_addresses
from signal_core.egress_profiles import EgressProfile
from signal_core.email_messages import MAX_MESSAGE_BYTES, dashboard_origin
from signal_core.identity_conditions import normalize_ascii_mailbox
from signal_core.openbao_http import (
    json_document,
    request,
    valid_base_url,
    valid_mount,
    valid_token,
)


@dataclass(frozen=True)
class SmtpConfiguration:
    host: str
    port: int
    tls_mode: str
    sender: str
    dashboard_origin: str
    daily_cap: int = 20

    def __post_init__(self) -> None:
        if (
            not isinstance(self.host, str)
            or re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,252}", self.host) is None
            or type(self.port) is not int
            or not 1 <= self.port <= 65535
            or self.tls_mode not in {"starttls", "implicit"}
            or type(self.daily_cap) is not int
            or not 1 <= self.daily_cap <= 100
            or normalize_ascii_mailbox(self.sender) != self.sender
        ):
            raise ValueError("Invalid TLS SMTP submission configuration.")
        dashboard_origin(self.dashboard_origin)

    @property
    def sha256(self) -> bytes:
        return hashlib.sha256(json.dumps(self.__dict__, sort_keys=True).encode()).digest()


@dataclass(frozen=True, repr=False)
class SmtpCredential:
    username: str = field(repr=False)
    password: str = field(repr=False)

    def __post_init__(self) -> None:
        for value in (self.username, self.password):
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= 512
                or any(ord(c) < 33 or ord(c) > 126 for c in value)
            ):
                raise ValueError("Invalid SMTP credential.")


@dataclass(frozen=True, repr=False)
class OpenBaoSmtpCredential:
    base_url: str
    token: str = field(repr=False)
    mount: str = "signal-email"

    def __post_init__(self) -> None:
        if (
            not valid_base_url(self.base_url)
            or not valid_token(self.token)
            or not valid_mount(self.mount)
        ):
            raise ValueError("Invalid OpenBao email credential boundary.")

    async def read(self, **options) -> SmtpCredential:
        response = await request(
            base_url=self.base_url,
            token=self.token,
            method="GET",
            path=f"/{self.mount}/data/smtp/default",
            **options,
        )
        if response.status_code != 200:
            raise RuntimeError("EMAIL_CREDENTIAL_UNAVAILABLE")
        try:
            envelope = json_document(response)["data"]
            metadata, data = envelope["metadata"], envelope["data"]
            if (
                set(data) != {"username", "password"}
                or metadata["destroyed"] is not False
                or metadata.get("deletion_time") not in {None, ""}
                or type(metadata["version"]) is not int
                or metadata["version"] < 1
            ):
                raise ValueError
            return SmtpCredential(**data)
        except (KeyError, TypeError, ValueError):
            raise RuntimeError("EMAIL_CREDENTIAL_UNAVAILABLE") from None


class _PinnedSmtp(smtplib.SMTP):
    def __init__(self, config, address, context):
        self.config, self.address, self.context = config, address, context
        self.deadline = time.monotonic() + 15
        super().__init__(timeout=15, local_hostname="signal.invalid")

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        return remaining

    def secure(self, raw, host):
        # Publish the TLS socket before handshaking so the total-deadline abort
        # can close it even while the handshake is blocked.
        self.sock = self.context.wrap_socket(
            raw, server_hostname=host, do_handshake_on_connect=False
        )
        self.sock.settimeout(self.remaining())
        self.sock.do_handshake()
        return self.sock

    def _get_socket(self, host, port, timeout):
        if host != self.config.host or port != self.config.port:
            raise ValueError("SMTP destination differs from its profile.")
        raw = socket.create_connection((self.address, port), timeout=self.remaining())
        self.sock = raw
        try:
            if not _same_address(raw.getpeername()[0], self.address):
                raise ValueError("SMTP peer differs from its pinned destination.")
            if self.config.tls_mode == "implicit":
                return self.secure(raw, host)
            raw.settimeout(self.remaining())
            return raw
        except BaseException:
            raw.close()
            raise

    def starttls(self, *, context=None):
        self.ehlo_or_helo_if_needed()
        if not self.has_extn("starttls"):
            raise smtplib.SMTPNotSupportedError("SMTP requires STARTTLS.")
        code, response = self.docmd("STARTTLS")
        if code != 220:
            raise smtplib.SMTPResponseException(code, response)
        self.secure(self.sock, self.config.host)
        if self.file is not None:
            self.file.close()
        self.file = None
        self.helo_resp = self.ehlo_resp = None
        self.esmtp_features = {}
        self.does_esmtp = False
        return code, response

    def getreply(self):
        if self.file is None:
            self.file = self.sock.makefile("rb")
        chunks, size = [], 0
        for _ in range(32):
            line = self.file.readline(1025)
            size += len(line)
            if not line or len(line) > 1024 or size > 8192:
                raise smtplib.SMTPServerDisconnected("SMTP response rejected.")
            if len(line) < 4 or not line[:3].isdigit() or line[3:4] not in {b"-", b" "}:
                raise smtplib.SMTPServerDisconnected("SMTP response rejected.")
            code = int(line[:3])
            chunks.append(line[4:].strip())
            if line[3:4] == b" ":
                return code, b"\n".join(chunks)
        raise smtplib.SMTPServerDisconnected("SMTP response rejected.")


class PinnedSmtpSubmitter:
    """DNS screened as a whole; numeric connect, TLS hostname validation, no fallback."""

    profile = EgressProfile.SMTP_SUBMIT

    def __init__(self, *, resolver=None, tls_context=None):
        self.resolver = resolver if resolver is not None else BoundedSystemResolver()
        self.context = tls_context if tls_context is not None else ssl.create_default_context()
        if (
            not isinstance(self.context, ssl.SSLContext)
            or not self.context.check_hostname
            or self.context.verify_mode != ssl.CERT_REQUIRED
        ):
            raise ValueError("SMTP requires verified TLS.")

    def submit(
        self, config: SmtpConfiguration, credential: SmtpCredential, recipient: str, body: bytes
    ) -> str:
        recipient = normalize_ascii_mailbox(recipient)
        if not isinstance(body, bytes) or not 1 <= len(body) <= MAX_MESSAGE_BYTES:
            raise ValueError("SMTP message exceeds its profile.")
        data_started = False
        client = None
        timer = None
        try:
            addresses = validate_public_addresses(self.resolver(config.host, config.port, 3))
            client = _PinnedSmtp(config, addresses[0], self.context)

            def abort():
                if client.sock is not None:
                    try:
                        client.sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    client.sock.close()

            timer = threading.Timer(15, abort)
            timer.daemon = True
            timer.start()
            client._host = config.host
            code, _ = client.connect(config.host, config.port)
            if code != 220:
                return _response_class(code)
            client.ehlo()
            if config.tls_mode == "starttls":
                client.starttls(context=self.context)
                client.ehlo()
            if not isinstance(client.sock, ssl.SSLSocket):
                return "permanent"
            client.login(credential.username, credential.password)
            code, _ = client.mail(config.sender)
            if code != 250:
                return _response_class(code)
            code, _ = client.rcpt(recipient)
            if code not in {250, 251}:
                return "bounced" if 500 <= code < 600 else _response_class(code)
            data_started = True
            code, _ = client.data(body)
            return "accepted" if code == 250 else _response_class(code)
        except smtplib.SMTPResponseException as error:
            return _response_class(error.smtp_code)
        except (ssl.SSLError, smtplib.SMTPNotSupportedError, ValueError):
            return "unknown" if data_started else "permanent"
        except (OSError, smtplib.SMTPException, TimeoutError):
            return "unknown" if data_started else "transient"
        finally:
            if timer is not None:
                timer.cancel()
                timer.join()
            if client is not None:
                client.close()


def _response_class(code):
    return "transient" if 400 <= code < 500 else "permanent"
