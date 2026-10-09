"""Slack installation and linking orchestration behind current dashboard authority."""

import hashlib
import secrets
import time
from dataclasses import dataclass, field
from functools import partial
from urllib.parse import urlencode, urlsplit
from uuid import UUID, uuid4

from anyio import to_thread
from psycopg import Connection
from psycopg.types.json import Jsonb

from signal_core.connector_framework import connector_row, external_revocation, require_restriction
from signal_core.database import _clean_transaction
from signal_core.session_tokens import hash_session_token
from signal_core.shared_egress import ProviderEgressUnavailable, SharedEgressProvider
from signal_core.slack_protocol import (
    SlackRejected,
    approval_message,
    parse_slack_action,
    slack_id,
    slack_json,
    verify_slack_signature,
)
from signal_core.slack_secrets import OpenBaoSlackSecrets


@dataclass(frozen=True, repr=False)
class SlackService:
    connection: Connection = field(repr=False)
    secrets_store: OpenBaoSlackSecrets = field(repr=False)
    dashboard_origin: str
    recovery_generation: str
    openbao_options: dict = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        endpoint = urlsplit(self.dashboard_origin)
        if (
            endpoint.scheme != "https"
            or not endpoint.hostname
            or endpoint.username
            or endpoint.password
            or endpoint.path
            or endpoint.query
            or endpoint.fragment
        ):
            raise ValueError("Slack requires an exact HTTPS dashboard origin.")

    def _one(self, function, *args):
        return connector_row(
            self.connection, function, *args, scalar=False, transaction=_clean_transaction
        )

    async def begin_install(
        self,
        *,
        session_token: str,
        site_id: UUID,
        workspace_id: str,
        channel_id: str,
        max_risk: int = 2,
    ) -> dict:
        slack_id(workspace_id, "T")
        slack_id(channel_id, "CG")
        if type(max_risk) is not int or not 0 <= max_risk <= 2:
            raise SlackRejected("SLACK_RISK_REJECTED")
        client = await self.secrets_store.client(**self.openbao_options)
        attempt, state = uuid4(), secrets.token_urlsafe(32)
        redirect = self.dashboard_origin + "/auth/slack/callback"
        row = self._one(
            "begin_slack_oauth",
            hash_session_token(session_token),
            site_id,
            self.recovery_generation,
            attempt,
            hashlib.sha256(state.encode()).digest(),
            workspace_id,
            channel_id,
            redirect,
            max_risk,
        )
        if row != ("created",):
            raise SlackRejected("SLACK_INSTALL_DENIED")
        return {
            "attempt_id": attempt,
            "authorization_url": "https://slack.com/oauth/v2/authorize?"
            + urlencode(
                {
                    "client_id": client["client_id"],
                    "scope": "chat:write",
                    "state": state,
                    "redirect_uri": redirect,
                    "team": workspace_id,
                }
            ),
            "expires_in_seconds": 600,
        }

    async def complete_install(
        self,
        *,
        session_token: str,
        site_id: UUID,
        attempt_id: UUID,
        state: str,
        code: str,
        egress: SharedEgressProvider,
    ) -> UUID:
        if (
            not isinstance(state, str)
            or len(state) != 43
            or not isinstance(code, str)
            or not 1 <= len(code) <= 2048
        ):
            raise SlackRejected("SLACK_OAUTH_REJECTED")
        redirect = self.dashboard_origin + "/auth/slack/callback"
        token_hash = hash_session_token(session_token)
        row = self._one(
            "consume_slack_oauth",
            token_hash,
            site_id,
            self.recovery_generation,
            attempt_id,
            hashlib.sha256(state.encode()).digest(),
            redirect,
        )
        if row is None:
            raise SlackRejected("SLACK_OAUTH_REJECTED")
        client = await self.secrets_store.client(**self.openbao_options)
        response = await _provider_json(
            egress,
            method="oauth.v2.access",
            document={
                "client_id": client["client_id"],
                "client_secret": client["client_secret"],
                "code": code,
                "redirect_uri": redirect,
            },
            operation_id=uuid4(),
        )
        try:
            if (
                response["token_type"] != "bot"
                or response["scope"] != "chat:write"
                or response["team"]["id"] != row[0]
                or response.get("is_enterprise_install") is not False
                or response.get("enterprise") is not None
                or any(key in response for key in ("refresh_token", "expires_in"))
                or any(key in response.get("authed_user", {}) for key in ("access_token", "scope"))
            ):
                raise ValueError
            bot_token = response["access_token"]
        except (KeyError, TypeError, ValueError):
            raise SlackRejected("SLACK_OAUTH_SCOPE_REJECTED") from None
        binding = uuid4()
        reference = await self.secrets_store.store_bot(binding, bot_token, **self.openbao_options)
        try:
            outcome = self._one(
                "confirm_slack_oauth",
                token_hash,
                site_id,
                self.recovery_generation,
                attempt_id,
                binding,
                row[0],
            )
            if outcome != ("bound",):
                raise SlackRejected("SLACK_INSTALL_DENIED")
        except Exception:
            await self.secrets_store.destroy_bot(reference, **self.openbao_options)
            raise
        return binding

    def begin_link(
        self, *, session_token: str, site_id: UUID, binding_id: UUID, slack_user_id: str
    ) -> UUID:
        slack_id(slack_user_id, "UW")
        identifier, code = uuid4(), secrets.token_urlsafe(32)
        payload = {
            "channel": slack_user_id,
            "text": "Confirm your Signal account link.",
            "mrkdwn": False,
            "parse": "none",
            "unfurl_links": False,
            "unfurl_media": False,
            "blocks": [
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Link account"},
                            "action_id": "signal_link",
                            "value": code,
                        }
                    ],
                }
            ],
        }
        row = self._one(
            "begin_slack_link",
            hash_session_token(session_token),
            site_id,
            self.recovery_generation,
            binding_id,
            identifier,
            slack_user_id,
            hashlib.sha256(code.encode()).digest(),
            Jsonb(payload),
        )
        if row != ("queued",):
            raise SlackRejected("SLACK_LINK_DENIED")
        return identifier

    def queue_approval(
        self,
        *,
        session_token: str,
        site_id: UUID,
        binding_id: UUID,
        revision_id: UUID,
        revision_sha256: str,
        channel_id: str,
    ) -> UUID:
        approve, reject = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        payload = approval_message(
            channel=channel_id,
            revision_id=revision_id,
            revision_sha256=revision_sha256,
            summary="An exact revision needs your decision.",
            evidence_url=self.evidence_link(revision_id),
            approve_code=approve,
            reject_code=reject,
        )
        row = self._one(
            "enqueue_slack_approval",
            hash_session_token(session_token),
            site_id,
            self.recovery_generation,
            binding_id,
            uuid4(),
            revision_id,
            bytes.fromhex(revision_sha256),
            hashlib.sha256(approve.encode()).digest(),
            hashlib.sha256(reject.encode()).digest(),
            Jsonb(payload),
        )
        if row is None or row[1] != "queued":
            raise SlackRejected("SLACK_REQUEST_DENIED")
        return row[0]

    def evidence_link(self, revision_id: UUID) -> str:
        return self.dashboard_origin + "/approvals?revision=" + str(revision_id)

    async def deliver(
        self, *, binding_id: UUID, outbox_id: UUID, egress: SharedEgressProvider
    ) -> str:
        item = self._one(
            "slack_outbox_item", binding_id, outbox_id, self.recovery_generation, False
        )
        if item is None:
            raise SlackRejected("SLACK_DELIVERY_UNAVAILABLE")
        if item[2] != "queued":
            return item[2]
        bot = await self.secrets_store.bot(item[1], **self.openbao_options)
        claimed = self._one(
            "slack_outbox_item", binding_id, outbox_id, self.recovery_generation, True
        )
        if claimed is None:
            raise SlackRejected("SLACK_DELIVERY_UNAVAILABLE")
        if claimed[2] != "claimed":
            return claimed[2]
        try:
            response = await _provider_json(
                egress,
                method="chat.postMessage",
                document=claimed[0],
                operation_id=outbox_id,
                bot_token=bot,
            )
            channel = slack_id(response.get("channel"), "CDG")
            timestamp = response.get("ts")
            if not isinstance(timestamp, str):
                raise SlackRejected("SLACK_RESPONSE_REJECTED")
            return self._one("finish_slack_outbox", binding_id, outbox_id, channel, timestamp)[0]
        except ProviderEgressUnavailable as error:
            if error.code == "EGRESS_DEFERRED":
                return self._one("defer_slack_outbox", binding_id, outbox_id)[0]
            return self._one("finish_slack_outbox", binding_id, outbox_id, None, None)[0]
        except Exception:
            return self._one("finish_slack_outbox", binding_id, outbox_id, None, None)[0]

    async def interact(
        self,
        *,
        binding_id: UUID,
        body: bytes,
        timestamp: str,
        signature: str,
        now: int | None = None,
    ) -> dict:
        client = await self.secrets_store.client(**self.openbao_options)
        body_hash = hashlib.sha256(body).digest()
        try:
            signature_hash = verify_slack_signature(
                secret=client["signing_secret"],
                timestamp=timestamp,
                signature=signature,
                body=body,
                now=int(time.time()) if now is None else now,
            )
        except SlackRejected:
            self._one("audit_slack_rejection", binding_id, body_hash, "signature_rejected")
            raise
        try:
            action = parse_slack_action(body)
        except SlackRejected:
            self._one("audit_slack_rejection", binding_id, body_hash, "payload_rejected")
            raise
        row = self._one(
            "handle_slack_action",
            binding_id,
            self.recovery_generation,
            signature_hash,
            body_hash,
            action.workspace_id,
            action.channel_id,
            action.user_id,
            hashlib.sha256(action.callback.encode()).digest(),
            action.action,
            action.message_ts,
            uuid4(),
        )
        if row is None:
            raise SlackRejected("SLACK_ACTION_REJECTED")
        # Never follow response_url or interpret message text. A synchronous ephemeral
        # response is a reply to this verified action, not a new outbound HTTP path.
        return {
            "outcome": row[0],
            "decision_id": row[1],
            "response_type": "ephemeral",
            "text": (
                "Continue in the dashboard for step-up authentication: "
                + self.dashboard_origin
                + "/approvals"
                if row[0] == "step_up"
                else "Signal: " + row[0]
            ),
        }

    async def revoke(
        self,
        *,
        session_token: str,
        site_id: UUID,
        binding_id: UUID,
        egress: SharedEgressProvider | None = None,
        link_id: UUID | None = None,
    ) -> dict:
        row = self._one(
            "revoke_slack_authority",
            hash_session_token(session_token),
            site_id,
            self.recovery_generation,
            binding_id,
            link_id,
            uuid4(),
        )
        require_restriction(
            row, {"revoked", "AUTHORITY_DURABILITY_PENDING"}, SlackRejected("SLACK_REVOKE_DENIED")
        )
        upstream = "not_required" if link_id is not None else "not_executed"
        if link_id is None:

            async def revoke():
                if egress is not None:
                    bot = await self.secrets_store.bot(row[1], **self.openbao_options)
                    await _provider_json(
                        egress,
                        method="auth.revoke",
                        document={},
                        operation_id=uuid4(),
                        bot_token=bot,
                    )
                    return "accepted"
                return "not_executed"

            upstream = await external_revocation(
                revoke,
                lambda: self.secrets_store.destroy_bot(row[1], **self.openbao_options),
                caught=(Exception,),
                failure="unknown",
            )
        return {"outcome": row[0], "restriction_event_id": row[2], "upstream": upstream}


async def _provider_json(egress, **kwargs):
    return await to_thread.run_sync(partial(slack_json, egress, **kwargs))
