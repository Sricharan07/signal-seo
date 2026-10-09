import re
import stat

import integration_identity as identity
import integration_secrets as operator
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import ExtendedKeyUsageOID


def platform():
    return {field: str(index) * 43 for index, field in enumerate(sorted(identity.FIELDS))}


@pytest.mark.parametrize(
    "invalid", [None, {}, {"extra": "x"}, {field: "x" for field in identity.FIELDS}]
)
def test_platform_rejects_invalid_values(invalid):
    with pytest.raises(operator.OperatorError):
        identity.validate_platform(invalid)


@pytest.mark.parametrize("value", [None, True, 2, "1", "x" * 42, "x" * 44, "x" * 42 + "\n"])
def test_platform_rejects_invalid_password(value):
    data = platform()
    data["keycloak_db_password"] = value
    with pytest.raises(operator.OperatorError):
        identity.validate_platform(data)


def test_distinct_platform_secrets():
    assert identity.validate_platform(platform()) == platform()
    with pytest.raises(operator.OperatorError):
        identity.validate_platform(dict.fromkeys(identity.FIELDS, "x" * 43))


class ConfigurationStore:
    def __init__(self, generation=1):
        self.generation = generation

    def request(self, method, path, **kwargs):
        assert method == "GET"
        assert path == f"/{identity.MOUNT}/data/{identity.PATH}"
        return {"data": {"metadata": {"version": self.generation}, "data": platform()}}


@pytest.mark.parametrize("generation", [True, 0, 2, "1", None])
def test_unknown_generation_denied(generation):
    with pytest.raises(operator.OperatorError):
        identity.read_configuration(ConfigurationStore(generation), "synthetic-root", identity.PATH)


def test_generation_one_read():
    assert (
        identity.read_configuration(ConfigurationStore(), "synthetic-root", identity.PATH)
        == platform()
    )


def test_leaves_are_private_distinct_and_hostname_bound(tmp_path):
    authority = operator.private_directory(tmp_path / "authority")
    directory = operator.private_directory(tmp_path / "identity")
    operator.prepare_tls(authority)
    identity.leaf_certificates(directory, authority)
    public_keys = []
    for name in ("database", "identity"):
        key_file = directory / f"{name}-key.pem"
        certificate = x509.load_pem_x509_certificate(
            operator.read_private(directory / f"{name}.pem")
        )
        key = serialization.load_pem_private_key(operator.read_private(key_file), password=None)
        public_keys.append(
            key.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        )
        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
        assert name in certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value.get_values_for_type(x509.DNSName)
        assert (
            ExtendedKeyUsageOID.SERVER_AUTH
            in certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        )
        assert (
            certificate.not_valid_after_utc
            <= x509.load_pem_x509_certificate(
                operator.read_private(authority / "ca.pem")
            ).not_valid_after_utc
        )
    assert public_keys[0] != public_keys[1]
    assert not (directory / "ca-key.pem").exists()
    with pytest.raises(FileExistsError):
        identity.leaf_certificates(directory, authority)


def test_wrong_ca_key_fails_before_export(tmp_path):
    authority = operator.private_directory(tmp_path / "authority")
    other = operator.private_directory(tmp_path / "other")
    destination = operator.private_directory(tmp_path / "destination")
    operator.prepare_tls(authority)
    operator.prepare_tls(other)
    (authority / "ca-key.pem").unlink()
    operator.write_private(authority / "ca-key.pem", operator.read_private(other / "ca-key.pem"))
    with pytest.raises(operator.OperatorError):
        identity.leaf_certificates(destination, authority)
    assert list(destination.iterdir()) == []


def test_existing_material_and_nested_directory_reject_before_store(tmp_path):
    authority = operator.private_directory(tmp_path / "authority")
    destination = operator.private_directory(tmp_path / "destination")
    operator.write_private(destination / "signal_google", b"synthetic")
    with pytest.raises(operator.OperatorError):
        identity.prepare(None, authority, destination, create=True)
    nested = operator.private_directory(authority / "nested")
    with pytest.raises(operator.OperatorError):
        identity.prepare(None, authority, nested, create=True)


def test_realms_have_no_synthetic_credentials_or_broad_clients():
    from integration_environment import render_realm

    realm = render_realm(identity.load_integration_scope())
    assert realm["sslRequired"] == "all"
    assert realm["registrationAllowed"] is False
    assert not realm.get("users")
    (client,) = realm["clients"]
    assert client["redirectUris"] == ["https://signal-test.example.invalid/auth/callback"]
    assert client["webOrigins"] == []
    assert client["attributes"]["pkce.code.challenge.method"] == "S256"
    assert client["directAccessGrantsEnabled"] is False
    assert client["implicitFlowEnabled"] is False
    (provider,) = realm["identityProviders"]
    assert provider["providerId"] == "google"
    assert provider["storeToken"] is False
    assert provider["config"]["clientSecret"] == "${vault.google}"
    assert (
        provider["config"]["clientId"] == identity.load_integration_scope().google_login_client_id
    )
    assert provider["config"]["filteredByClaim"] == "true"
    assert provider["config"]["claimFilterValue"] == r"^owner@example\.invalid$"
    assert provider["config"]["disableNonce"] == "false"
    pattern = provider["config"]["claimFilterValue"]
    assert re.fullmatch(pattern, "owner@example.invalid")
    for denied in (
        "owner@exampleXinvalid",
        "other-owner@example.invalid",
        "owner@example.invalid.evil",
    ):
        assert re.fullmatch(pattern, denied) is None


@pytest.mark.parametrize("cas,policies", [(False, []), (True, None), (True, [identity.POLICY])])
def test_create_guard_performs_no_mutation(monkeypatch, cas, policies):
    monkeypatch.setattr(identity, "check_audit", lambda *args: None)

    class Store:
        def request(self, method, path, **kwargs):
            assert method in {"GET", "LIST"}
            return {"data": {"cas_required": cas, "keys": policies}}

    with pytest.raises(operator.OperatorError):
        identity.create_platform(Store(), "synthetic-root")
