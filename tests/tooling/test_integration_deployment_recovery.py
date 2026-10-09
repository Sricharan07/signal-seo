import base64
import json

import pytest
from integration_recover_deployment_operator import recover_operator
from integration_retire_root import ROLE
from integration_secrets import OperatorError


class PrivateStore:
    def __init__(
        self,
        *,
        changed_role=False,
        fail_after_quorum=False,
        fail_operator_probe=False,
        fail_root_retirement=False,
    ):
        self.calls = []
        self.changed_role = changed_role
        self.fail_after_quorum = fail_after_quorum
        self.fail_operator_probe = fail_operator_probe
        self.fail_root_retirement = fail_root_retirement
        self.updates = 0

    def close(self):
        pass

    def request(self, method, path, *, token=None, payload=None, expected=(200, 204)):
        self.calls.append((method, path, token, expected))
        if self.fail_operator_probe and path == "/signal-identity/data/google/client":
            raise OperatorError("Private test probe failure")
        if self.fail_root_retirement and path == "/auth/token/revoke-self" and token == "fixture!":
            raise OperatorError("Private test retirement failure")
        if path == "/sys/generate-root/attempt":
            if method == "GET":
                return {"started": False}
            return {"otp": "abcdefgh", "nonce": "fixture", "required": 2, "progress": 0}
        if path == "/sys/generate-root/update":
            self.updates += 1
            encoded = base64.b64encode(
                bytes(left ^ right for left, right in zip(b"fixture!", b"abcdefgh", strict=True))
            ).decode()
            return {"complete": self.updates == 2, "nonce": "fixture", "encoded_token": encoded}
        if path == "/auth/approle/role/" + ROLE:
            if self.fail_after_quorum:
                raise OperatorError("Private test failure")
            return {
                "data": {
                    "token_policies": ["root"] if self.changed_role else [ROLE],
                    "token_no_default_policy": True,
                    "secret_id_num_uses": 1,
                    "token_ttl": 3600,
                    "token_max_ttl": 14400,
                }
            }
        if path.endswith("/role-id"):
            return {"data": {"role_id": "fixture"}}
        if path.endswith("/secret-id"):
            return {"data": {"secret_id": "fixture"}}
        if path == "/auth/approle/login":
            return {
                "auth": {
                    "client_token": "limited-fixture",
                    "policies": [ROLE],
                    "lease_duration": 3600,
                }
            }
        return {}


@pytest.mark.parametrize("changed,fail", [(False, False), (True, False), (False, True)])
def test_quorum_recovery_always_retires_root_and_never_changes_role(
    tmp_path,
    monkeypatch,
    changed,
    fail,
):
    store = PrivateStore(changed_role=changed, fail_after_quorum=fail)
    monkeypatch.setattr("integration_recover_deployment_operator.Store", lambda *args: store)
    monkeypatch.setattr("integration_recover_deployment_operator.check_audit", lambda *args: None)
    monkeypatch.setattr(
        "integration_recover_deployment_operator.recovery",
        lambda *args: {
            "keys_base64": ["fixture1", "fixture2", "fixture3"],
        },
    )
    if changed or fail:
        with pytest.raises(OperatorError):
            recover_operator(tmp_path, tmp_path, approved=True)
        assert not (tmp_path / "private-operator.json").exists()
    else:
        recover_operator(tmp_path, tmp_path, approved=True)
        assert json.loads((tmp_path / "private-operator.json").read_text())["role"] == ROLE
    assert ("POST", "/auth/token/revoke-self", "fixture!", (200, 204)) in store.calls
    assert ("GET", "/sys/mounts", "fixture!", (403,)) in store.calls
    assert (
        json.loads((tmp_path / "root-revocation.json").read_text())["temporary_root"]
        == "REVOKED_AND_DENIED"
    )
    assert not any("sys/policies" in call[1] for call in store.calls)


def test_recovery_requires_approval_and_empty_audit_destination(tmp_path):
    with pytest.raises(OperatorError):
        recover_operator(tmp_path, tmp_path, approved=False)
    (tmp_path / "existing").write_text("fixture")
    with pytest.raises(OperatorError):
        recover_operator(tmp_path, tmp_path, approved=True)


@pytest.mark.parametrize("failure", ["probe", "receipt", "root_retirement"])
def test_partial_operator_is_retired_when_recovery_does_not_finish(tmp_path, monkeypatch, failure):
    store = PrivateStore(
        fail_operator_probe=failure == "probe", fail_root_retirement=failure == "root_retirement"
    )
    monkeypatch.setattr("integration_recover_deployment_operator.Store", lambda *args: store)
    monkeypatch.setattr("integration_recover_deployment_operator.check_audit", lambda *args: None)
    monkeypatch.setattr(
        "integration_recover_deployment_operator.recovery",
        lambda *args: {
            "keys_base64": ["fixture1", "fixture2", "fixture3"],
        },
    )
    if failure == "receipt":
        from integration_recover_deployment_operator import write_private

        def failing_receipt(path, content):
            if path.name == "private-operator.json":
                raise OperatorError("Private test storage failure")
            write_private(path, content)

        monkeypatch.setattr(
            "integration_recover_deployment_operator.write_private", failing_receipt
        )
    with pytest.raises(OperatorError):
        recover_operator(tmp_path, tmp_path, approved=True)
    assert not (tmp_path / "private-operator.json").exists()
    assert ("POST", "/auth/token/revoke-self", "limited-fixture", (200, 204)) in store.calls
    assert ("GET", "/auth/token/lookup-self", "limited-fixture", (403,)) in store.calls
    assert ("POST", "/auth/token/revoke-self", "fixture!", (200, 204)) in store.calls
