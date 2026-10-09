"""The generic private boundary exposes truth, never provider or production authority."""

import asyncio
import json
import os
import ssl
import time
from dataclasses import asdict, replace
from pathlib import Path
from uuid import uuid4

import httpx2
import pytest
from self_host_material import tls_material
from signal_api import self_host_runtime as runtime
from signal_api.integration_runtime import DIRECTORY, WorkloadTokens
from signal_core.oidc_login import ConsumedOidcLoginAttempt, OidcClientRegistration
from signal_core.oidc_protocol import OidcTokenResponse, VerifiedOidcIdentity
from signal_core.self_host_config import CLIENT, SelfHostConfig
from signal_core.session_issuance import SessionIssuanceDenied


def test_unqualified_configured_and_missing_providers_remain_disabled(tmp_path, monkeypatch):
    asyncio.run(check_unqualified(tmp_path, monkeypatch))


async def check_unqualified(tmp_path, monkeypatch):
    config = SelfHostConfig(
        "signal-self-host",
        "https://signal.example.invalid",
        "owner",
        "Synthetic workspace",
        "self-host",
        {
            name: f"registry.example.invalid/synthetic-{name}@sha256:" + "a" * 64
            for name in ("api", "dashboard", "identity", "ingress", "workflow")
        },
    )
    documents = {
        "config.json": {"schema_version": 1, **asdict(config)},
        "application.json": {
            "identity_db_password": "synthetic-private-password",
            "csrf_key": "a" * 64,
        },
        "providers.json": ["openai"],
    }
    monkeypatch.setattr(runtime, "DIRECTORY", tmp_path)
    monkeypatch.setattr(runtime, "document", lambda path: documents[path.name])
    (tmp_path / "tls").mkdir()
    (tmp_path / "tls/ca.pem").write_text(tls_material()["ca.pem"])
    app = runtime.create_self_host_app()
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="https://api.invalid"
    ) as client:
        response = await client.get("/v1/capabilities")
        assert response.status_code == 200
        assert response.json()["production_writes_enabled"] is False
        assert all(item["availability"] == "disabled" for item in response.json()["capabilities"])
        response = await client.get("/v1/self-host/providers")
        assert response.status_code == 200
        projection = response.json()["providers"]
        assert projection[0]["configuration"] == "configured"
        assert projection[1]["configuration"] == "not_configured"
        assert all(item["availability"] == "disabled" for item in projection)
        for path in ("/v1/commands", "/v1/connectors", "/docs", "/openapi.json"):
            denied = await client.post(path, json={"synthetic": "no-authority"})
            assert denied.status_code == 503
        assert "synthetic-private-password" not in json.dumps(projection)


def test_shared_workload_defaults_preserve_integration_behavior():
    original = WorkloadTokens(ssl.create_default_context())
    generic = WorkloadTokens(
        ssl.create_default_context(),
        directory=Path("/synthetic-runtime"),
        policy_prefix="signal-self-host-",
    )
    assert original.directory == DIRECTORY and original.policy_prefix == "signal-test-"
    assert generic.directory == Path("/synthetic-runtime")
    assert original.roles == generic.roles == ("pkce-writer", "pkce-consumer", "recovery-reader")


def test_owner_capture_is_signed_subject_bound_create_only_and_expires(tmp_path):
    epoch = int(time.time())
    config = SelfHostConfig(
        "signal-self-host",
        "https://signal.example.invalid",
        "owner",
        "Synthetic workspace",
        "self-host",
        {},
    )
    subject = str(uuid4())
    bootstrap = {
        "subject": subject,
        "status": "armed",
        "approved_at": epoch - 3,
        "expires_at": epoch + 3597,
    }
    registration = OidcClientRegistration(config.issuer, CLIENT, config.origin + "/auth/callback")
    attempt = ConsumedOidcLoginAttempt(uuid4(), registration, b"x" * 32, "synthetic-ref", "/")
    response = OidcTokenResponse("synthetic-id-token", "synthetic-access-token", 300)
    identity = VerifiedOidcIdentity(
        config.issuer,
        subject,
        CLIENT,
        epoch,
        epoch + 300,
        epoch - 1,
        None,
        "1",
        None,
        frozenset({"otp"}),
    )
    path = tmp_path / "proof"
    observer = runtime.OwnerProofCapture(config, bootstrap, path, clock=lambda: epoch)
    observer(attempt, response, replace(identity, subject="synthetic-other"))
    assert not path.exists()
    with pytest.raises(SessionIssuanceDenied):
        observer(attempt, response, replace(identity, authentication_methods=frozenset({"pwd"})))
    observer(attempt, response, identity)
    assert path.stat().st_mode & 0o777 == 0o600
    content = path.read_bytes()
    observer(replace(attempt, id=uuid4()), response, identity)
    assert path.read_bytes() == content
    os.utime(path, (epoch - 300, epoch - 300))
    observer.expire()
    assert not path.exists()
    target = tmp_path / "untouched"
    target.write_text("synthetic-untouched")
    path.symlink_to(target)
    observer(attempt, response, identity)
    assert target.read_text() == "synthetic-untouched"
