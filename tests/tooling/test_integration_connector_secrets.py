import json
import sys

import httpx2
import integration_connector_secrets as connectors
import integration_secrets as operator
import pytest
import qualify_integration_connector_secrets as qualification
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

ORIGIN = "https://signal-test.example.invalid"
GOOGLE_REDIRECTS = {
    "google-login": ORIGIN + "/identity/realms/signal/broker/google/endpoint",
    "gsc": ORIGIN + "/auth/gsc/callback",
}


def google_document(provider="gsc"):
    return {
        "web": {
            "client_id": (
                "000000000000-placeholder.apps.googleusercontent.com"
                if provider == "google-login"
                else "000000000000-gsc-placeholder.apps.googleusercontent.com"
            ),
            "client_secret": "synthetic-client-secret-not-for-output",
            "project_id": "integration-test",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": [GOOGLE_REDIRECTS[provider]],
        }
    }


def test_private_download_accepts_only_private_immediate_files(tmp_path):
    directory = operator.private_directory(tmp_path / "private")
    path = directory / "download.json"
    operator.write_private(path, b"synthetic-download")
    assert connectors.private_download(path, directory) == b"synthetic-download"
    for candidate in (path.relative_to(tmp_path), tmp_path / "outside.json"):
        with pytest.raises(operator.OperatorError, match="private operator directory"):
            connectors.private_download(candidate, directory)
    link = directory / "link.json"
    link.symlink_to(path)
    with pytest.raises(operator.OperatorError):
        connectors.private_download(link, directory)
    path.chmod(0o644)
    with pytest.raises(operator.OperatorError, match="owner-only"):
        connectors.private_download(path, directory)


def test_private_download_is_size_bounded(tmp_path):
    directory = operator.private_directory(tmp_path / "private")
    path = directory / "download.json"
    operator.write_private(path, b"x" * 16385)
    with pytest.raises(operator.OperatorError, match="small"):
        connectors.private_download(path, directory)


def pem(key, encryption=None):
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        encryption or serialization.NoEncryption(),
    )


def test_github_configuration_uses_exact_app_and_rsa_key():
    content = pem(rsa.generate_private_key(public_exponent=65537, key_size=2048))
    assert connectors.github_configuration(content) == {
        "app_id": 1234567,
        "private_key_pem": content.decode("ascii"),
    }


@pytest.mark.parametrize("kind", ["weak", "ec", "encrypted", "malformed"])
def test_github_configuration_rejects_unsafe_keys(kind):
    if kind == "weak":
        content = pem(rsa.generate_private_key(public_exponent=65537, key_size=1024))
    elif kind == "ec":
        content = pem(ec.generate_private_key(ec.SECP256R1()))
    elif kind == "encrypted":
        content = pem(
            rsa.generate_private_key(public_exponent=65537, key_size=2048),
            serialization.BestAvailableEncryption(b"synthetic-test-password"),
        )
    else:
        content = b"synthetic-malformed-key"
    with pytest.raises((operator.OperatorError, ValueError, TypeError)):
        connectors.github_configuration(content)


@pytest.mark.parametrize("provider", ["google-login", "gsc"])
@pytest.mark.parametrize("origins", [None, [], [ORIGIN]])
def test_google_configuration_uses_exact_separate_callback(provider, origins):
    document = google_document(provider)
    if origins is not None:
        document["web"]["javascript_origins"] = origins
    assert connectors.google_configuration(json.dumps(document).encode(), provider) == {
        "client_id": document["web"]["client_id"],
        "client_secret": document["web"]["client_secret"],
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("project_id", "unrelated-project"),
        ("redirect_uris", [GOOGLE_REDIRECTS["google-login"]]),
        ("redirect_uris", [GOOGLE_REDIRECTS["gsc"], "https://other.invalid"]),
        ("redirect_uris", [GOOGLE_REDIRECTS["gsc"] + "/"]),
        ("auth_uri", "https://other.invalid/auth"),
        ("token_uri", "https://other.invalid/token"),
        ("auth_provider_x509_cert_url", "https://other.invalid/certs"),
        ("javascript_origins", [ORIGIN, "https://other.invalid"]),
        ("unexpected", "synthetic-extra"),
        ("client_id", "not-google"),
        ("client_secret", "space in secret"),
    ],
)
def test_google_configuration_rejects_wrong_or_broader_configuration(field, value):
    document = google_document()
    document["web"][field] = value
    with pytest.raises((operator.OperatorError, ValueError)):
        connectors.google_configuration(json.dumps(document).encode(), "gsc")


@pytest.mark.parametrize("document", [[], {}, {"installed": {}}, {"web": []}])
def test_google_configuration_rejects_wrong_document_shape(document):
    with pytest.raises(operator.OperatorError):
        connectors.google_configuration(json.dumps(document).encode(), "gsc")


def test_google_configuration_rejects_duplicate_fields():
    content = json.dumps(google_document()).replace(
        '"project_id":', '"project_id": "unrelated-project", "project_id":'
    )
    with pytest.raises(operator.OperatorError, match="Duplicate"):
        connectors.google_configuration(content.encode(), "gsc")


def test_slack_configuration_has_fixed_client_and_no_bot_token():
    result = connectors.slack_configuration("synthetic-client-secret", "synthetic-signing-secret")
    assert result == {
        "client_id": "00000000000.00000000000000",
        "client_secret": "synthetic-client-secret",
        "signing_secret": "synthetic-signing-secret",
    }


@pytest.mark.parametrize("value", ["short", "x" * 129, "synthetic secret", "x" * 16 + "\n"])
def test_slack_configuration_rejects_invalid_secret_formats(value):
    for secrets in ((value, "synthetic-signing-secret"), ("synthetic-client-secret", value)):
        with pytest.raises(operator.OperatorError):
            connectors.slack_configuration(*secrets)


class RecordingStore:
    def __init__(self, *, mount=None, config=None, policies=None, failure=None):
        self.calls = []
        self.mount = mount
        self.config = {"cas_required": True} if config is None else config
        self.policies = [] if policies is None else policies
        self.failure = failure
        self.data = None
        self.revoked = False

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if self.failure == (method, path):
            raise httpx2.ReadTimeout("synthetic-secret-not-for-output")
        if path == "/sys/policies/acl":
            return {"data": {"keys": self.policies}}
        if path == "/sys/mounts":
            return {"data": {} if self.mount is None else {"signal-gsc/": self.mount}}
        if path == "/signal-gsc/config":
            return {"data": self.config} if method == "GET" else {}
        if path == "/auth/token/create" and kwargs["token"] == "synthetic-root-token":
            assert kwargs["payload"] == {
                "policies": ["signal-gsc-client-reader"],
                "no_default_policy": True,
                "ttl": "5m",
            }
            return {"auth": {"client_token": "synthetic-reader-token"}}
        if path == "/auth/token/revoke":
            self.revoked = True
            return {}
        if kwargs["token"] == "synthetic-reader-token":
            if method == "GET" and path == "/signal-gsc/data/oauth-client" and not self.revoked:
                return {"data": {"data": self.data, "metadata": {"version": 1}}}
            assert kwargs["expected"] == (403,)
            return {}
        if method == "POST" and path == "/signal-gsc/data/oauth-client":
            assert kwargs["payload"]["options"] == {"cas": 0}
            self.data = kwargs["payload"]["data"]
        return {}


@pytest.fixture
def prepared_operator(tmp_path, monkeypatch):
    directory = operator.private_directory(tmp_path / "private")
    operator.write_private(
        directory / "recovery.json",
        json.dumps({"root_token": "synthetic-root-token", "keys_base64": ["a", "b", "c"]}).encode(),
    )
    monkeypatch.setattr(connectors, "check_audit", lambda *_: None)
    return directory


@pytest.mark.parametrize("existing", [False, True])
def test_store_is_cas_create_only_with_exact_reader_and_revocation(prepared_operator, existing):
    store = RecordingStore(mount={"type": "kv", "options": {"version": "2"}} if existing else None)
    data = connectors.google_configuration(json.dumps(google_document()).encode(), "gsc")
    connectors.store_configuration(store, prepared_operator, "gsc", data)
    assert store.data == data
    assert store.revoked
    config_writes = [c for c in store.calls if c[:2] == ("POST", "/signal-gsc/config")]
    assert len(config_writes) == (0 if existing else 1)
    policies = [
        c for c in store.calls if c[:2] == ("PUT", "/sys/policies/acl/signal-gsc-client-reader")
    ]
    assert policies[0][2]["payload"] == {
        "policy": 'path "signal-gsc/data/oauth-client" { capabilities = ["read"] }',
        "cas": 0,
        "cas_required": True,
    }
    assert store.calls[-1][2]["expected"] == (403,)


@pytest.mark.parametrize(
    ("mount", "config"),
    [
        ({"type": "transit"}, {"cas_required": True}),
        ({"type": "kv", "options": {"version": "1"}}, {"cas_required": True}),
        ({"type": "kv", "options": {"version": "2"}}, {"cas_required": False}),
    ],
)
def test_store_rejects_existing_unsafe_mount_without_mutation(prepared_operator, mount, config):
    store = RecordingStore(mount=mount, config=config)
    with pytest.raises(operator.OperatorError):
        connectors.store_configuration(store, prepared_operator, "gsc", {"synthetic": "data"})
    assert all(method in {"GET", "LIST"} for method, _, _ in store.calls)


def test_store_never_overwrites_existing_reader_policy(prepared_operator):
    store = RecordingStore(policies=["signal-gsc-client-reader"])
    with pytest.raises(operator.OperatorError, match="Reader policy already exists"):
        connectors.store_configuration(store, prepared_operator, "gsc", {"synthetic": "data"})
    assert all(method in {"GET", "LIST"} for method, _, _ in store.calls)


def test_store_does_not_retry_unknown_write_outcome(prepared_operator):
    store = RecordingStore(failure=("POST", "/signal-gsc/data/oauth-client"))
    with pytest.raises(httpx2.ReadTimeout):
        connectors.store_configuration(store, prepared_operator, "gsc", {"synthetic": "data"})
    assert sum(c[:2] == store.failure for c in store.calls) == 1
    assert not any(method == "PUT" for method, _, _ in store.calls)


def test_reader_is_revoked_even_if_verification_fails():
    store = RecordingStore(failure=("GET", "/signal-gsc/data/oauth-client"))
    with pytest.raises(httpx2.ReadTimeout):
        connectors.verify_reader(
            store,
            "synthetic-root-token",
            "signal-gsc",
            "oauth-client",
            "signal-gsc-client-reader",
            {},
        )
    assert store.revoked


def test_cli_redacts_parser_failure_and_does_not_reach_store(
    prepared_operator, monkeypatch, capsys
):
    path = prepared_operator / "bad.json"
    operator.write_private(path, b'{"synthetic-secret-not-for-output"')
    monkeypatch.setattr(
        sys,
        "argv",
        ["import", "gsc", "--directory", str(prepared_operator), "--download", str(path)],
    )
    assert connectors.main() == 1
    output = capsys.readouterr()
    assert not output.out
    assert "JSONDecodeError" in output.err
    assert "synthetic-secret-not-for-output" not in output.err


def test_slack_cli_requires_hidden_interactive_input(prepared_operator, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["import", "slack", "--directory", str(prepared_operator)])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert connectors.main() == 1
    assert "non-echoed interactive prompts" in capsys.readouterr().err


def test_new_mount_waits_only_on_exact_upgrade_response(monkeypatch):
    replies = iter([{"errors": [connectors.UPGRADING]}, {"data": {"cas_required": False}}])
    calls = []

    class Store:
        def request(self, method, path, **kwargs):
            calls.append((method, path))
            return next(replies)

    monkeypatch.setattr(connectors.time, "sleep", lambda _: None)
    connectors.wait_for_new_mount(Store(), "synthetic-root-token", "signal-gsc")
    assert calls == [("GET", "/signal-gsc/config")] * 2


@pytest.mark.parametrize("reply", [{}, {"errors": ["synthetic-unknown-error"]}])
def test_new_mount_does_not_retry_unknown_responses(reply):
    class Store:
        def request(self, *args, **kwargs):
            return reply

    with pytest.raises(operator.OperatorError, match="did not become ready"):
        connectors.wait_for_new_mount(Store(), "synthetic-root-token", "signal-gsc")


def test_new_mount_readiness_has_a_deadline(monkeypatch):
    times = iter([0, 11])
    monkeypatch.setattr(connectors.time, "monotonic", lambda: next(times))

    class Store:
        def request(self, *args, **kwargs):
            return {"errors": [connectors.UPGRADING]}

    with pytest.raises(operator.OperatorError, match="did not become ready"):
        connectors.wait_for_new_mount(Store(), "synthetic-root-token", "signal-gsc")


def test_qualification_configuration_is_synthetic_and_schema_compatible():
    data = qualification.synthetic_configurations()
    assert set(data) == set(connectors.PATHS)
    assert "private_key_pem" in data["github"]
    for provider in ("google-login", "gsc", "slack"):
        assert "synthetic" in data[provider]["client_secret"]


def test_qualification_transport_errors_include_only_fixed_path(tmp_path):
    operator.prepare_tls(tmp_path)
    store = qualification.QualificationStore("https://localhost:18200", tmp_path / "ca.pem")
    store.client.close()
    store.client = httpx2.Client(
        base_url="https://localhost:18200",
        transport=httpx2.MockTransport(
            lambda _: httpx2.Response(500, json={"errors": ["synthetic-secret-not-for-output"]})
        ),
    )
    try:
        with pytest.raises(operator.OperatorError, match="GET /sys/init") as error:
            store.request("GET", "/sys/init")
        assert "synthetic-secret" not in str(error.value)
    finally:
        store.close()
