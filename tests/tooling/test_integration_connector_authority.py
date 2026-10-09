import base64
import json
from pathlib import Path

import pytest
from integration_application import POLICIES as LOGIN_POLICIES
from integration_connector_authority import (
    OPERATOR_ROLE,
    POLICIES,
    issue_existing_operator,
    operator_policy,
    provision,
    recover_and_provision,
    render,
)
from integration_secrets import OperatorError


class Store:
    def __init__(self, changed_policy=False, unsafe_mount=False, provision_failure=False):
        self.calls = []
        self.changed_policy, self.unsafe_mount = changed_policy, unsafe_mount
        self.provision_failure = provision_failure
        self.shares = 0
        self.policy = "changed" if changed_policy else operator_policy(LOGIN_POLICIES)

    def close(self):
        self.calls.append(("close",))

    def request(self, method, path, token=None, payload=None, expected=(200, 204)):
        self.calls.append((method, path, token, payload, expected))
        if path == "/sys/policies/acl/" + OPERATOR_ROLE and method == "GET":
            return {"data": {"policy": self.policy}}
        if path == "/sys/policies/acl/" + OPERATOR_ROLE and method == "PUT":
            self.policy = payload["policy"]
        if path == "/auth/approle/role/" + OPERATOR_ROLE and method == "GET":
            return {
                "data": {
                    "token_policies": [OPERATOR_ROLE],
                    "token_no_default_policy": True,
                    "secret_id_num_uses": 1,
                    "token_ttl": 3600,
                    "token_max_ttl": 14400,
                }
            }
        if path == "/auth/approle/login":
            return {
                "auth": {
                    "client_token": "limited-fixture",
                    "policies": [OPERATOR_ROLE],
                    "lease_duration": 3600,
                    "renewable": True,
                }
            }
        if path.endswith("/config"):
            return {"data": {"cas_required": not self.unsafe_mount}}
        if (
            self.provision_failure
            and path == "/signal-identity/data/platform/owner-artifacts"
            and method == "POST"
        ):
            raise OperatorError("fixture write failure")
        if path == "/sys/generate-root/attempt":
            if method == "GET":
                return {"started": False}
            return {"otp": "abcdefgh", "nonce": "fixture", "required": 2, "progress": 0}
        if path == "/sys/generate-root/update":
            self.shares += 1
            encoded = base64.urlsafe_b64encode(
                bytes(left ^ right for left, right in zip(b"fixture!", b"abcdefgh", strict=True))
            ).decode()
            return {"complete": self.shares == 2, "nonce": "fixture", "encoded_token": encoded}
        if path.endswith("/role-id"):
            return {"data": {"role_id": "fixture"}}
        if path.endswith("/secret-id"):
            return {"data": {"secret_id": "fixture"}}
        return {}


@pytest.mark.parametrize("changed,unsafe", [(False, False), (True, False), (False, True)])
def test_existing_authority_and_cas_are_preflighted_before_any_mutation(
    monkeypatch, changed, unsafe
):
    store = Store(changed_policy=changed, unsafe_mount=unsafe)
    monkeypatch.setattr("integration_connector_authority.check_audit", lambda *args: None)
    if changed or unsafe:
        with pytest.raises(OperatorError):
            provision(store, "fixture-root")
        assert not any(call[0] in {"POST", "PUT", "DELETE"} for call in store.calls)
    else:
        provision(store, "fixture-root")
        writes = [call for call in store.calls if call[0] == "POST"]
        key = next(call[3] for call in writes if call[1].endswith("/owner-artifacts"))
        assert key["options"] == {"cas": 0} and len(key["data"]["material_hex"]) == 64
        for role in POLICIES:
            record = next(
                call[3] for call in writes if call[1] == "/auth/approle/role/signal-test-" + role
            )
            assert record["secret_id_num_uses"] == 1 and record["token_period"] == "300s"
            assert record["token_no_default_policy"] is True
        updated = next(
            call[3]["policy"]
            for call in store.calls
            if call[0] == "PUT" and call[1].endswith(OPERATOR_ROLE)
        )
        assert "signal-slack/data" not in updated and "signal-github/data" not in updated
        assert "signal-gsc/data" not in updated and "auth/token/create" not in updated


@pytest.mark.parametrize("failure", [False, True])
def test_quorum_always_retires_temporary_root_without_runtime_root_file(
    tmp_path, monkeypatch, failure
):
    store = Store(provision_failure=failure)
    monkeypatch.setattr("integration_connector_authority.Store", lambda *args: store)
    monkeypatch.setattr("integration_connector_authority.check_audit", lambda *args: None)
    monkeypatch.setattr("integration_connector_authority.snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "integration_connector_authority.recovery", lambda *args: {"keys_base64": ["a", "b", "c"]}
    )
    if failure:
        with pytest.raises(OperatorError):
            recover_and_provision(tmp_path, tmp_path, approved=True)
    else:
        recover_and_provision(tmp_path, tmp_path, approved=True)
    assert any(call[:3] == ("POST", "/auth/token/revoke-self", "fixture!") for call in store.calls)
    assert any(
        call[:3] == ("GET", "/sys/mounts", "fixture!") and call[4] == (403,) for call in store.calls
    )
    receipt = json.loads((tmp_path / "root-revocation.json").read_text())
    assert receipt["provisioning_succeeded"] is not failure
    assert receipt["secure_listener_restore_required"] is True
    assert all("fixture!" not in path.read_text() for path in tmp_path.iterdir() if path.is_file())
    if not failure:
        assert json.loads((tmp_path / "private-operator.json").read_text())["role"] == OPERATOR_ROLE
        assert (tmp_path / "after-root-retirement").is_dir()


def test_quorum_requires_explicit_approval_and_empty_private_destination(tmp_path):
    with pytest.raises(OperatorError):
        recover_and_provision(tmp_path, tmp_path, approved=False)
    (tmp_path / "existing").write_text("fixture")
    with pytest.raises(OperatorError):
        recover_and_provision(tmp_path, tmp_path, approved=True)


@pytest.mark.parametrize("failure", ["root_retirement", "post_retirement_backup", "operator_probe"])
def test_incomplete_provisioning_retires_any_partial_operator(tmp_path, monkeypatch, failure):
    class FailingStore(Store):
        def request(self, method, path, token=None, payload=None, expected=(200, 204)):
            if (
                failure == "root_retirement"
                and path == "/auth/token/revoke-self"
                and token == "fixture!"
            ):
                raise OperatorError("fixture root retirement failure")
            if (
                failure == "operator_probe"
                and path == "/signal-github/data/github/app"
                and token == "limited-fixture"
            ):
                raise OperatorError("fixture denial probe failure")
            return super().request(method, path, token, payload, expected)

    store = FailingStore()
    monkeypatch.setattr("integration_connector_authority.Store", lambda *args: store)
    monkeypatch.setattr("integration_connector_authority.check_audit", lambda *args: None)
    monkeypatch.setattr(
        "integration_connector_authority.recovery", lambda *args: {"keys_base64": ["a", "b", "c"]}
    )

    def backup(store, directory, **kwargs):
        if failure == "post_retirement_backup" and directory.name == "after-root-retirement":
            raise OperatorError("fixture backup failure")

    monkeypatch.setattr("integration_connector_authority.snapshot", backup)
    with pytest.raises(OperatorError):
        recover_and_provision(tmp_path, tmp_path, approved=True)
    assert not (tmp_path / "private-operator.json").exists()
    assert any(
        call[:3] == ("POST", "/auth/token/revoke-self", "limited-fixture") for call in store.calls
    )
    assert any(
        call[:3] == ("GET", "/auth/token/lookup-self", "limited-fixture") and call[4] == (403,)
        for call in store.calls
    )


def test_workload_render_issues_only_exact_one_use_roles(tmp_path):
    store = Store()
    render(store, "fixture-operator", tmp_path)
    assert json.loads((tmp_path / "connector-workloads.json").read_text()) == {
        "roles": list(POLICIES)
    }
    assert len(store.calls) == 8
    assert all("/auth/approle/role/signal-test-" in call[1] for call in store.calls)
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in tmp_path.iterdir())
    with pytest.raises(OperatorError):
        render(store, "fixture-operator", tmp_path)


@pytest.mark.parametrize("unlink_failure", [False, True])
def test_operator_write_failure_retires_token_even_if_cleanup_fails(
    tmp_path, monkeypatch, unlink_failure
):
    store = Store()
    store.policy = operator_policy((*LOGIN_POLICIES, *POLICIES))

    def partial_write(path, data):
        path.write_bytes(data)
        raise OSError("fixture partial write failure")

    monkeypatch.setattr("integration_connector_authority.write_private", partial_write)
    if unlink_failure:

        def failed_unlink(path, **kwargs):
            raise OSError("fixture unlink failure")

        monkeypatch.setattr(Path, "unlink", failed_unlink)
    with pytest.raises(OSError):
        issue_existing_operator(store, "fixture-root", tmp_path)
    assert (tmp_path / "private-operator.json").exists() is unlink_failure
    assert any(
        call[:3] == ("POST", "/auth/token/revoke-self", "limited-fixture") for call in store.calls
    )
    assert any(
        call[:3] == ("GET", "/auth/token/lookup-self", "limited-fixture") and call[4] == (403,)
        for call in store.calls
    )
