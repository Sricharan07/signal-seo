"""Private, expiring signed-assertion capture. This port cannot provision anything."""

import json
import os
import time
from collections.abc import Callable
from pathlib import Path

from signal_core.integration_scope import load_integration_scope
from signal_core.oidc_login import ConsumedOidcLoginAttempt
from signal_core.oidc_protocol import OidcTokenResponse, VerifiedOidcIdentity

CLIENT = "signal-test-dashboard"
PROOF = Path("/tmp/signal-owner-proof.json")


class TestAssertionCapture:
    __test__ = False

    def __init__(
        self,
        approval: dict,
        destination: Path = PROOF,
        *,
        approval_reader: Callable[[], dict] | None = None,
        integration_scope=None,
    ):
        self._validate_approval(approval)
        if approval_reader is not None and not callable(approval_reader):
            raise ValueError("Test proof approval reader rejected.")
        self.approval, self.destination = dict(approval), destination
        self.approval_reader = approval_reader
        self.integration_scope = integration_scope or load_integration_scope()

    @staticmethod
    def _validate_approval(approval: dict) -> None:
        if (
            not isinstance(approval, dict)
            or set(approval) != {"approved_at", "expires_at"}
            or any(type(value) is not int for value in approval.values())
            or not 0 < approval["expires_at"] - approval["approved_at"] <= 3600
        ):
            raise ValueError("Test proof approval window rejected.")

    def _current_approval(self) -> dict:
        approval = self.approval_reader() if self.approval_reader else self.approval
        self._validate_approval(approval)
        return approval

    def __call__(
        self,
        attempt: ConsumedOidcLoginAttempt,
        response: OidcTokenResponse,
        identity: VerifiedOidcIdentity,
    ) -> None:
        current = int(time.time())
        if self.destination.with_suffix(".done").exists():
            return
        approval = self._current_approval()
        if not approval["approved_at"] <= current < approval["expires_at"]:
            return
        if (
            attempt.purpose != "login"
            or identity.issuer != self.integration_scope.issuer
            or identity.client_id != CLIENT
            or identity.subject != self.integration_scope.owner_subject
            or identity.verified_email != self.integration_scope.owner_email
            or identity.issued_at < approval["approved_at"]
        ):
            raise ValueError("Test proof identity rejected.")
        content = json.dumps(
            {
                "schema_version": 1,
                "attempt_id": str(attempt.id),
                "id_token": response.id_token,
                "access_token": response.access_token,
                "expires_in": response.expires_in,
            }
        ).encode()
        # Create-only, one bounded artifact in container tmpfs, never an HTTP response.
        if len(content) > 65536:
            raise ValueError("Test proof size rejected.")
        try:
            descriptor = os.open(
                self.destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
        except FileExistsError:
            return
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
        except BaseException:
            self.destination.unlink(missing_ok=True)
            raise

    def expire(self) -> None:
        try:
            info = self.destination.lstat()
        except FileNotFoundError:
            return
        try:
            approval = self._current_approval()
        except Exception:
            self.destination.unlink(missing_ok=True)
            return
        if time.time() >= min(info.st_mtime + 300, approval["expires_at"]):
            self.destination.unlink()
