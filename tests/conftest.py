"""Disposable loopback SMTP protocol double; never a public mail service."""

import json
import socketserver
import ssl
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

INTEGRATION_FIXTURE = {
    "origin": "https://signal-test.example.invalid",
    "owner_email": "owner@example.invalid",
    "owner_subject": "00000000-0000-4000-8000-000000000017",
    "site_id": "00000000-0000-4000-8000-000000000002",
    "slack_workspace_id": "T0000000000",
    "slack_channel_id": "C0000000000",
    "slack_client_id": "00000000000.00000000000000",
    "google_project_id": "integration-test",
    "google_login_client_id": "000000000000-placeholder.apps.googleusercontent.com",
    "google_gsc_client_id": "000000000000-gsc-placeholder.apps.googleusercontent.com",
    "github_app_id": 1234567,
    "github_installation_id": 123456789,
    "github_owner": "example-owner",
    "github_repository": "integration-test",
    "github_base_branch": "main",
    "github_content_path": "README.md",
    "public_ipv4": "93.184.216.34",
    "public_ipv6": "2001:db8::2",
    "ssh_source_ipv6": "2001:db8::1",
    "ssh_key_path": "/private-fixture/ssh-key",
    "ssh_known_hosts_path": "/private-fixture/known-hosts",
    "realm_id": "00000000-0000-4000-8000-000000000001",
    "incomplete_user_id": "00000000-0000-4000-8000-000000000018",
    "incomplete_created_timestamp": 1700000000000,
    "tenant_name": "Signal Test",
    "home_region": "us-east-1",
}


@pytest.fixture(autouse=True)
def dedicated_integration_configuration(tmp_path_factory, monkeypatch):
    directory = tmp_path_factory.mktemp("private-integration-configuration")
    path = directory / "environment.json"
    path.write_text(json.dumps(INTEGRATION_FIXTURE))
    path.chmod(0o600)
    monkeypatch.setenv("SIGNAL_INTEGRATION_CONFIG_FILE", str(path))
    return path


@pytest.fixture
def smtp_server(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "smtp.example.invalid")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("smtp.example.invalid")]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "smtp.pem", tmp_path / "smtp-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert_path, key_path)
    client_context = ssl.create_default_context(cafile=str(cert_path))
    state = SimpleNamespace(
        mode="starttls", messages=[], commands=[], port=None, tls_context=client_context
    )

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            connection = self.request
            connection.settimeout(5)
            secured = state.mode == "implicit"
            try:
                if state.mode == "stall_implicit":
                    connection.recv(4096)
                    connection.recv(4096)
                    return
                if secured:
                    connection = server_context.wrap_socket(connection, server_side=True)
                reader = connection.makefile("rb")

                def reply(value):
                    connection.sendall(value.encode("ascii") + b"\r\n")

                reply("220 synthetic SMTP")
                while True:
                    line = reader.readline(4096)
                    if not line:
                        break
                    verb = line.split(b" ", 1)[0].strip().upper()
                    state.commands.append((verb, secured))
                    if verb == b"EHLO":
                        reply("250-synthetic SMTP")
                        if not secured and state.mode != "no_starttls":
                            reply("250-STARTTLS")
                        reply("250 AUTH PLAIN")
                    elif verb == b"STARTTLS":
                        reply("220 Upgrade")
                        reader.close()
                        if state.mode == "stall_tls":
                            connection.recv(4096)
                            connection.recv(4096)
                            return
                        connection = server_context.wrap_socket(connection, server_side=True)
                        reader = connection.makefile("rb")
                        secured = True
                    elif verb == b"AUTH":
                        reply("235 Authenticated" if secured else "530 TLS required")
                    elif verb == b"MAIL":
                        reply("250 Sender")
                    elif verb == b"RCPT":
                        reply("550 Bounced" if state.mode == "bounce" else "250 Recipient")
                    elif verb == b"DATA":
                        reply("354 Message")
                        message = bytearray()
                        while True:
                            part = reader.readline(4096)
                            if part == b".\r\n":
                                break
                            if not part:
                                return
                            message.extend(part[1:] if part.startswith(b"..") else part)
                        if state.mode == "transient":
                            reply("451 Not accepted")
                        else:
                            state.messages.append(bytes(message))
                            if state.mode == "drop":
                                return
                            reply("250 Accepted synthetic-secret-provider-text")
                    else:
                        reply("250 Done")
            except (OSError, ssl.SSLError):
                pass
            finally:
                connection.close()

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True

    with Server(("127.0.0.1", 0), Handler) as server:
        state.port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        # The only public-address exception is test-local, in-process and ephemeral.
        # The production submitter has no loopback bypass or configurable screening.
        monkeypatch.setattr(
            "signal_core.smtp_submission.validate_public_addresses", lambda answers: tuple(answers)
        )
        yield state
        server.shutdown()
        thread.join(timeout=5)
