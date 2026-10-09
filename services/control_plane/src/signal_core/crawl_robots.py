"""RFC-aware robots parsing, immutable snapshots, and fail-closed cached decisions."""

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from uuid import UUID, uuid5

from protego import Protego
from psycopg import Connection
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from signal_core.crawl_artifacts import (
    ArtifactEncryptionKey,
    ArtifactObject,
    ArtifactRecord,
    EncryptedLocalArtifactStore,
)
from signal_core.crawl_frontier import CrawlRunOpened
from signal_core.crawl_http import RobotsFetchResult
from signal_core.crawl_urls import CrawlScopePolicy, CrawlUrl, validate_public_addresses
from signal_core.database import Scope, _clean_transaction


class RobotsSnapshotUnavailable(RuntimeError):
    """No exact current snapshot can support a crawl decision."""


class RobotsSnapshotConflict(RuntimeError):
    """A stable snapshot identity is already bound to different evidence."""


@dataclass(frozen=True, repr=False)
class RobotsRules:
    origin: str
    product_token: str
    parser_release: str
    source_sha256: str
    rules_sha256: str
    parse_error_count: int
    rule_count: int
    crawl_delay_ms: int | None
    _parser: Any

    def can_fetch(self, value: object) -> bool:
        url = value if isinstance(value, CrawlUrl) else None
        if url is None or url.origin != self.origin:
            raise ValueError("Robots decisions require an admitted URL from the exact origin.")
        if url.request_target.partition("?")[0] == "/robots.txt":
            return True
        return bool(self._parser.can_fetch(url.fetch_url, self.product_token))


@dataclass(frozen=True, repr=False)
class RobotsSnapshotRecorded:
    snapshot_id: UUID
    tenant_id: UUID
    site_id: UUID
    crawl_run_id: UUID
    origin: str
    robots_url: str
    final_url: str
    fetch_profile_sha256: str
    product_token: str
    parser_release: str
    fetched_at: datetime
    expires_at: datetime
    retrieval_outcome: str
    decision_status: str
    http_status: int | None
    response_headers: tuple[tuple[str, str], ...]
    redirect_chain: tuple[str, ...]
    resolved_address: str | None
    media_type: str | None
    source_sha256: str | None
    rules_sha256: str | None
    parse_error_count: int
    rule_count: int
    crawl_delay_ms: int | None
    network_profile_sha256: str
    raw_artifact: ArtifactRecord | None
    duplicate: bool


@dataclass(frozen=True)
class RobotsAccessDecision:
    allowed: bool
    reason: str
    snapshot_id: UUID
    expires_at: datetime
    crawl_delay_ms: int | None


def parse_robots(body: object, *, origin: object, user_agent: object) -> RobotsRules:
    """Parse bounded UTF-8 records with a pinned RFC 9309 parser profile."""
    if not isinstance(body, bytes) or len(body) > ROBOTS_MAX_BODY_BYTES:
        raise ValueError("Robots content must be bounded bytes.")
    if not isinstance(origin, str) or not _ORIGIN.fullmatch(origin):
        raise ValueError("A canonical robots origin is required.")
    product_token = _product_token(user_agent)
    raw_lines = body.split(b"\n")
    if len(raw_lines) > ROBOTS_MAX_LINES:
        raise ValueError("Robots content exceeds the line limit.")

    accepted: list[str] = []
    parse_errors = 0
    rule_count = 0
    for index, raw_line in enumerate(raw_lines):
        if raw_line.endswith(b"\r"):
            raw_line = raw_line[:-1]
        if len(raw_line) > ROBOTS_MAX_LINE_BYTES:
            parse_errors += 1
            continue
        try:
            line = raw_line.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            parse_errors += 1
            continue
        if index == 0:
            line = line.removeprefix("\ufeff")
        if any(ord(character) < 32 and character != "\t" for character in line):
            parse_errors += 1
            continue
        stripped = line.lstrip(" \t")
        if not stripped or stripped.startswith("#"):
            continue
        name, separator, value = stripped.partition(":")
        if not separator:
            parse_errors += 1
            continue
        directive = name.strip().lower()
        if directive not in _SUPPORTED_DIRECTIVES:
            continue
        if value.count("*") > ROBOTS_MAX_WILDCARDS_PER_LINE:
            parse_errors += 1
            continue
        accepted.append(f"{directive}: {value.strip()}")
        if directive in {"allow", "disallow"}:
            rule_count += 1

    canonical = ("\n".join(accepted) + ("\n" if accepted else "")).encode("utf-8")
    try:
        parser = Protego.parse(canonical.decode("utf-8"))
        delay = parser.crawl_delay(product_token)
    except Exception:
        raise ValueError("Robots content could not be parsed safely.") from None
    crawl_delay_ms = None
    if isinstance(delay, (int, float)) and not isinstance(delay, bool) and math.isfinite(delay):
        if delay > 0:
            crawl_delay_ms = min(math.ceil(delay * 1000), ROBOTS_MAX_CRAWL_DELAY_MS)
    return RobotsRules(
        origin=origin,
        product_token=product_token,
        parser_release=ROBOTS_PARSER_RELEASE,
        source_sha256=hashlib.sha256(body).hexdigest(),
        rules_sha256=hashlib.sha256(canonical).hexdigest(),
        parse_error_count=parse_errors,
        rule_count=rule_count,
        crawl_delay_ms=crawl_delay_ms,
        _parser=parser,
    )


def persist_robots_snapshot(
    connection: Connection,
    store: EncryptedLocalArtifactStore,
    run: CrawlRunOpened,
    policy: CrawlScopePolicy,
    result: RobotsFetchResult,
    *,
    snapshot_id: UUID,
    fetched_at: datetime,
    network_profile_sha256: str,
    artifact_key: ArtifactEncryptionKey | None,
    retain_until: datetime | None,
    recorded_at: datetime | None = None,
) -> RobotsSnapshotRecorded:
    """Register one bounded retrieval and body artifact in a single SQL transaction."""
    _validate_persist_inputs(connection, store, run, policy, result, snapshot_id)
    fetched = _utc(fetched_at, "robots fetch time")
    recorded = _utc(recorded_at or datetime.now(UTC), "robots recording time")
    now = datetime.now(UTC)
    if (
        recorded < fetched
        or fetched > now + timedelta(minutes=5)
        or recorded > now + timedelta(minutes=5)
    ):
        raise ValueError("Robots snapshot times are invalid.")
    if (
        not isinstance(network_profile_sha256, str)
        or _SHA256.fullmatch(network_profile_sha256) is None
    ):
        raise ValueError("A canonical network profile SHA-256 is required.")
    headers = _headers(result.response_headers)
    rules = (
        parse_robots(result.body, origin=result.origin, user_agent=policy.user_agent)
        if result.outcome == "fetched"
        else None
    )
    decision_status = _DECISION_BY_OUTCOME[result.outcome]
    expires_at = fetched + _cache_ttl(result, fetched)
    profile_hash = _profile_hash(policy)
    product_token = _product_token(policy.user_agent)
    artifact: ArtifactObject | None = None
    artifact_id: UUID | None = None
    attestation_id: UUID | None = None
    retention: datetime | None = None

    if result.outcome == "fetched":
        if not isinstance(artifact_key, ArtifactEncryptionKey) or retain_until is None:
            raise ValueError("Fetched robots content requires encryption and retention.")
        retention = _utc(retain_until, "artifact retention deadline")
        artifact_id = uuid5(
            _ROBOTS_ID_NAMESPACE,
            f"artifact:{run.tenant_id}:{snapshot_id}:{result.body_sha256}",
        )
        attestation_id = uuid5(_ROBOTS_ID_NAMESPACE, f"attestation:{artifact_id}:upload-readback")
        with store.stage_verified(
            Scope(run.tenant_id, run.site_id),
            artifact_id,
            result.body,
            media_type="text/plain",
            key=artifact_key,
            created_at=recorded,
        ) as artifact:
            if retention <= artifact.created_at:
                raise ValueError("Artifact retention must extend beyond creation.")
            row = _commit_snapshot(
                connection,
                run,
                result,
                snapshot_id,
                fetched,
                expires_at,
                recorded,
                profile_hash,
                product_token,
                rules,
                decision_status,
                network_profile_sha256,
                headers,
                artifact,
                retention,
                attestation_id,
            )
    else:
        if artifact_key is not None or retain_until is not None:
            raise ValueError("Body-free robots outcomes cannot register artifact material.")
        row = _commit_snapshot(
            connection,
            run,
            result,
            snapshot_id,
            fetched,
            expires_at,
            recorded,
            profile_hash,
            product_token,
            rules,
            decision_status,
            network_profile_sha256,
            headers,
            None,
            None,
            None,
        )
    return _snapshot_from_row(row, run=run, expected_id=snapshot_id)


def authorize_from_current_snapshot(
    connection: Connection,
    store: EncryptedLocalArtifactStore,
    run: CrawlRunOpened,
    policy: CrawlScopePolicy,
    value: object,
    *,
    at_time: datetime,
    artifact_key: ArtifactEncryptionKey | None,
    request_target: str | None = None,
) -> RobotsAccessDecision:
    """Apply the newest unexpired exact-profile snapshot; absence fails closed."""
    _require_clean_connection(connection)
    if not isinstance(store, EncryptedLocalArtifactStore) or not isinstance(run, CrawlRunOpened):
        raise ValueError("Validated robots decision dependencies are required.")
    if not isinstance(policy, CrawlScopePolicy):
        raise ValueError("A validated crawl policy is required.")
    url = policy.admit(value)
    observed_at = _utc(at_time, "robots decision time")
    profile_hash = bytes.fromhex(_profile_hash(policy))
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT snapshot_id, crawl_run_id, origin, robots_url, final_url, "
            "fetch_profile_hash, product_token, parser_release, fetched_at, expires_at, "
            "retrieval_outcome, decision_status, http_status, response_headers, redirect_chain, "
            "resolved_address, media_type, source_hash, rules_hash, parse_error_count, rule_count, "
            "crawl_delay_ms, network_profile_hash, raw_artifact_id, object_key, object_version, "
            "artifact_hash, artifact_byte_length, artifact_media_type, encryption_key_ref, "
            "artifact_created_at, retain_until, legal_hold, durability_state, outcome "
            "FROM control.get_current_robots_snapshot(%s, %s, %s, %s, %s, %s)",
            (run.tenant_id, run.site_id, run.run_id, url.origin, profile_hash, observed_at),
        ).fetchone()
    if row is None or row[34] != "found":
        raise RobotsSnapshotUnavailable()
    snapshot = _snapshot_from_lookup(row, run=run)
    if snapshot.decision_status == "allow_missing":
        return RobotsAccessDecision(
            True, "robots_not_found", snapshot.snapshot_id, snapshot.expires_at, None
        )
    if snapshot.decision_status != "rules":
        return RobotsAccessDecision(
            False,
            snapshot.decision_status,
            snapshot.snapshot_id,
            snapshot.expires_at,
            None,
        )
    if snapshot.raw_artifact is None or not isinstance(artifact_key, ArtifactEncryptionKey):
        raise RobotsSnapshotUnavailable()
    body = store.read(snapshot.raw_artifact, key=artifact_key)
    rules = parse_robots(body, origin=snapshot.origin, user_agent=policy.user_agent)
    if (
        rules.parser_release != snapshot.parser_release
        or rules.product_token != snapshot.product_token
        or rules.source_sha256 != snapshot.source_sha256
        or rules.rules_sha256 != snapshot.rules_sha256
        or rules.parse_error_count != snapshot.parse_error_count
        or rules.rule_count != snapshot.rule_count
        or rules.crawl_delay_ms != snapshot.crawl_delay_ms
    ):
        raise RobotsSnapshotUnavailable()
    if request_target is None:
        allowed = rules.can_fetch(url)
    else:
        # Secret-bearing targets are evaluated only in memory, never in snapshot evidence.
        if (
            url.origin != "https://api.telegram.org"
            or not request_target.startswith("/bot")
            or re.fullmatch(r"/bot[A-Za-z0-9_:-]{16,256}/[A-Za-z]+", request_target) is None
        ):
            raise ValueError("Invalid private robots target.")
        try:
            allowed = bool(
                rules._parser.can_fetch(url.origin + request_target, rules.product_token)
            )
        except Exception:
            raise RobotsSnapshotUnavailable() from None
    return RobotsAccessDecision(
        allowed,
        "allowed_by_rules" if allowed else "disallowed_by_rules",
        snapshot.snapshot_id,
        snapshot.expires_at,
        snapshot.crawl_delay_ms,
    )


def _commit_snapshot(
    connection: Connection,
    run: CrawlRunOpened,
    result: RobotsFetchResult,
    snapshot_id: UUID,
    fetched_at: datetime,
    expires_at: datetime,
    recorded_at: datetime,
    profile_hash: str,
    product_token: str,
    rules: RobotsRules | None,
    decision_status: str,
    network_profile_sha256: str,
    headers: dict[str, str],
    artifact: ArtifactObject | None,
    retain_until: datetime | None,
    attestation_id: UUID | None,
) -> tuple:
    artifact_values = (
        (
            artifact.artifact_id,
            artifact.object_key,
            artifact.object_version,
            bytes.fromhex(artifact.sha256),
            artifact.byte_length,
            artifact.media_type,
            artifact.encryption_key_ref,
            artifact.created_at,
            retain_until,
            attestation_id,
        )
        if artifact is not None
        else (None,) * 10
    )
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT snapshot_id, crawl_run_id, origin, robots_url, final_url, "
            "fetch_profile_hash, product_token, parser_release, fetched_at, expires_at, "
            "retrieval_outcome, decision_status, http_status, response_headers, redirect_chain, "
            "resolved_address, media_type, source_hash, rules_hash, parse_error_count, rule_count, "
            "crawl_delay_ms, network_profile_hash, raw_artifact_id, object_key, object_version, "
            "artifact_hash, artifact_byte_length, artifact_media_type, encryption_key_ref, "
            "artifact_created_at, retain_until, legal_hold, durability_state, duplicate, outcome "
            "FROM control.commit_robots_snapshot(" + ", ".join(["%s"] * 36) + ")",
            (
                run.tenant_id,
                run.site_id,
                run.run_id,
                snapshot_id,
                result.origin,
                result.robots_url,
                result.final_url,
                bytes.fromhex(profile_hash),
                rules.product_token if rules else product_token,
                rules.parser_release if rules else ROBOTS_PARSER_RELEASE,
                fetched_at,
                expires_at,
                result.outcome,
                decision_status,
                result.http_status,
                Jsonb(headers),
                Jsonb(list(result.redirect_chain)),
                result.resolved_address,
                result.media_type,
                bytes.fromhex(rules.source_sha256) if rules else None,
                bytes.fromhex(rules.rules_sha256) if rules else None,
                rules.parse_error_count if rules else 0,
                rules.rule_count if rules else 0,
                rules.crawl_delay_ms if rules else None,
                bytes.fromhex(network_profile_sha256),
                recorded_at,
                *artifact_values,
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("Robots snapshot commit returned no outcome.")
    if row[35] == "snapshot_unavailable":
        raise RobotsSnapshotUnavailable()
    if row[35] == "snapshot_conflict":
        raise RobotsSnapshotConflict()
    if row[35] != "recorded":
        raise RuntimeError("Robots snapshot commit returned an invalid outcome.")
    return row


def _snapshot_from_row(
    row: tuple, *, run: CrawlRunOpened, expected_id: UUID
) -> RobotsSnapshotRecorded:
    snapshot = _snapshot_from_values(row, run=run, duplicate=row[34])
    if snapshot.snapshot_id != expected_id:
        raise RuntimeError("Robots snapshot commit returned mismatched evidence.")
    return snapshot


def _snapshot_from_lookup(row: tuple, *, run: CrawlRunOpened) -> RobotsSnapshotRecorded:
    return _snapshot_from_values(row, run=run, duplicate=False)


def _snapshot_from_values(
    row: tuple, *, run: CrawlRunOpened, duplicate: bool
) -> RobotsSnapshotRecorded:
    artifact = None
    if row[23] is not None:
        artifact = ArtifactRecord(
            tenant_id=run.tenant_id,
            site_id=run.site_id,
            artifact_id=row[23],
            object_key=row[24],
            object_version=row[25],
            sha256=row[26].hex(),
            byte_length=row[27],
            media_type=row[28],
            encryption_key_ref=row[29],
            created_at=row[30],
            retain_until=row[31],
            legal_hold=row[32],
            durability_state=row[33],
        )
    snapshot = RobotsSnapshotRecorded(
        snapshot_id=row[0],
        tenant_id=run.tenant_id,
        site_id=run.site_id,
        crawl_run_id=row[1],
        origin=row[2],
        robots_url=row[3],
        final_url=row[4],
        fetch_profile_sha256=row[5].hex(),
        product_token=row[6],
        parser_release=row[7],
        fetched_at=row[8],
        expires_at=row[9],
        retrieval_outcome=row[10],
        decision_status=row[11],
        http_status=row[12],
        response_headers=tuple(sorted(row[13].items())),
        redirect_chain=tuple(row[14]),
        resolved_address=row[15],
        media_type=row[16],
        source_sha256=row[17].hex() if row[17] is not None else None,
        rules_sha256=row[18].hex() if row[18] is not None else None,
        parse_error_count=row[19],
        rule_count=row[20],
        crawl_delay_ms=row[21],
        network_profile_sha256=row[22].hex(),
        raw_artifact=artifact,
        duplicate=duplicate,
    )
    if snapshot.crawl_run_id != run.run_id or not isinstance(snapshot.duplicate, bool):
        raise RuntimeError("Robots snapshot returned invalid evidence.")
    return snapshot


def _validate_persist_inputs(connection, store, run, policy, result, snapshot_id) -> None:
    _require_clean_connection(connection)
    if not isinstance(store, EncryptedLocalArtifactStore) or not isinstance(run, CrawlRunOpened):
        raise ValueError("Validated robots persistence dependencies are required.")
    if not isinstance(policy, CrawlScopePolicy) or not isinstance(result, RobotsFetchResult):
        raise ValueError("Validated robots policy and retrieval evidence are required.")
    if not isinstance(snapshot_id, UUID):
        raise ValueError("A typed robots snapshot identity is required.")
    if result.schema_version != 1 or result.outcome not in _DECISION_BY_OUTCOME:
        raise ValueError("Unsupported robots retrieval outcome.")
    if (
        result.origin not in policy.allowed_origins
        or result.robots_url != f"{result.origin}/robots.txt"
    ):
        raise ValueError("Robots retrieval does not match the admitted origin.")
    final = policy.admit(result.final_url)
    if final.fetch_url != result.final_url or len(result.redirect_chain) > min(
        policy.max_redirects, 5
    ):
        raise ValueError("Robots redirect evidence is invalid.")
    seen = {result.robots_url}
    canonical_redirects = []
    current = policy.admit(result.robots_url)
    for value in result.redirect_chain:
        if not isinstance(value, str):
            raise ValueError("Robots redirect evidence is invalid.")
        redirected = policy.admit_redirect(current, value)
        if redirected.fetch_url != value or redirected.normalized_key in seen:
            raise ValueError("Robots redirect evidence is invalid.")
        seen.add(redirected.normalized_key)
        canonical_redirects.append(redirected.fetch_url)
        current = redirected
    if (canonical_redirects and canonical_redirects[-1] != result.final_url) or (
        not canonical_redirects and result.final_url != result.robots_url
    ):
        raise ValueError("Robots final URL does not match its redirect evidence.")
    if not isinstance(result.elapsed_ms, int) or not 0 <= result.elapsed_ms <= int(
        policy.total_timeout_seconds * 1000
    ):
        raise ValueError("Robots elapsed time is invalid.")
    if result.resolved_address is not None:
        validate_public_addresses((result.resolved_address,))
    if result.media_type is not None and _MEDIA_TYPE.fullmatch(result.media_type) is None:
        raise ValueError("Robots media type is invalid.")
    _validate_outcome_status(result)
    if result.outcome == "fetched":
        if (
            result.http_status is None
            or not 200 <= result.http_status <= 299
            or result.media_type != "text/plain"
            or result.decoded_bytes != len(result.body)
            or result.decoded_bytes > ROBOTS_MAX_BODY_BYTES
            or result.body_sha256 != hashlib.sha256(result.body).hexdigest()
        ):
            raise ValueError("Fetched robots body evidence is invalid.")
    elif result.body != b"" or result.body_sha256 is not None or result.decoded_bytes != 0:
        raise ValueError("Body-free robots outcome contains body evidence.")


def _validate_outcome_status(result: RobotsFetchResult) -> None:
    status = result.http_status
    valid = {
        "fetched": status is not None and 200 <= status <= 299,
        "not_found": status == 404,
        "forbidden": status in {401, 403},
        "backoff": status == 429,
        "server_error": status is not None and 500 <= status <= 599,
        "client_error": (
            status is not None and 400 <= status <= 499 and status not in {401, 403, 404, 429}
        ),
        "transport_error": status is None or 100 <= status <= 599,
        "policy_rejected": status is None or 100 <= status <= 599,
        "unsupported_encoding": status is not None and 200 <= status <= 299,
        "unsupported_media_type": status is not None and 200 <= status <= 299,
        "body_limit": status is not None and 200 <= status <= 299,
    }[result.outcome]
    if not valid:
        raise ValueError("Robots retrieval status does not match its outcome.")
    if (
        result.outcome not in {"transport_error", "policy_rejected"}
        and result.resolved_address is None
    ):
        raise ValueError("Robots response outcome requires a public address.")


def _cache_ttl(result: RobotsFetchResult, fetched_at: datetime) -> timedelta:
    if result.outcome in {"fetched", "not_found", "forbidden"}:
        return timedelta(hours=24)
    if result.outcome == "backoff":
        headers = dict(result.response_headers)
        return _retry_after(headers.get("retry-after"), fetched_at)
    return timedelta(minutes=5)


def _retry_after(value: str | None, now: datetime) -> timedelta:
    seconds = 300
    if value is not None:
        if value.isascii() and value.isdigit():
            seconds = int(value)
        else:
            try:
                target = parsedate_to_datetime(value).astimezone(UTC)
                seconds = math.ceil((target - now).total_seconds())
            except (TypeError, ValueError, OverflowError):
                seconds = 300
    return timedelta(seconds=min(max(seconds, 1), 24 * 60 * 60))


def _headers(values: object) -> dict[str, str]:
    if not isinstance(values, tuple) or len(values) > 9:
        raise ValueError("Robots response headers are invalid.")
    result = {}
    total = 0
    for item in values:
        if not isinstance(item, tuple) or len(item) != 2:
            raise ValueError("Robots response headers are invalid.")
        name, value = item
        if (
            name not in _RETAINED_HEADERS
            or name in result
            or not isinstance(value, str)
            or not 1 <= len(value) <= 2048
            or _VISIBLE_ASCII.fullmatch(value) is None
        ):
            raise ValueError("Robots response headers are invalid.")
        total += len(name) + len(value)
        if total > 16384:
            raise ValueError("Robots response headers exceed their storage limit.")
        result[name] = value
    if tuple(result) != tuple(sorted(result)):
        raise ValueError("Robots response headers must use canonical ordering.")
    return result


def _product_token(user_agent: object) -> str:
    if not isinstance(user_agent, str):
        raise ValueError("A robots user agent is required.")
    token = user_agent.split("/", 1)[0]
    if _PRODUCT_TOKEN.fullmatch(token) is None:
        raise ValueError("Robots user-agent product token is invalid.")
    return token


def _profile_hash(policy: CrawlScopePolicy) -> str:
    profile = {
        "max_body_bytes": policy.max_body_bytes,
        "max_redirects": policy.max_redirects,
        "request_timeout_ms": round(policy.request_timeout_seconds * 1000),
        "schema_version": 1,
        "total_timeout_ms": round(policy.total_timeout_seconds * 1000),
        "user_agent": policy.user_agent,
    }
    canonical = json.dumps(profile, sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def _require_clean_connection(connection: object) -> None:
    if (
        not isinstance(connection, Connection)
        or not connection.autocommit
        or connection.info.transaction_status != TransactionStatus.IDLE
    ):
        raise ValueError("Use an idle autocommit connection for robots persistence.")


def _utc(value: object, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"A timezone-aware {name} is required.")
    return value.astimezone(UTC)


ROBOTS_PARSER_RELEASE = "protego/0.6.2+signal-rfc9309-v1"
ROBOTS_MAX_BODY_BYTES = 500 * 1024
ROBOTS_MAX_LINES = 10_000
ROBOTS_MAX_LINE_BYTES = 8192
ROBOTS_MAX_WILDCARDS_PER_LINE = 64
ROBOTS_MAX_CRAWL_DELAY_MS = 60_000
_ROBOTS_ID_NAMESPACE = UUID("459746d1-e54c-4504-8c6d-967b9d2241f6")
_SUPPORTED_DIRECTIVES = {"user-agent", "allow", "disallow", "crawl-delay"}
_DECISION_BY_OUTCOME = {
    "fetched": "rules",
    "not_found": "allow_missing",
    "forbidden": "deny",
    "backoff": "backoff",
    "server_error": "suspended",
    "client_error": "suspended",
    "transport_error": "suspended",
    "policy_rejected": "suspended",
    "unsupported_encoding": "suspended",
    "unsupported_media_type": "suspended",
    "body_limit": "suspended",
}
_RETAINED_HEADERS = {
    "cache-control",
    "content-encoding",
    "content-length",
    "content-type",
    "etag",
    "last-modified",
    "location",
    "retry-after",
    "transfer-encoding",
}
_PRODUCT_TOKEN = re.compile(r"[A-Za-z_-]{1,64}")
_ORIGIN = re.compile(r"https?://[^/?#@\s]{1,500}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_MEDIA_TYPE = re.compile(r"[a-z0-9!#$&^_.+-]{1,64}/[a-z0-9!#$&^_.+-]{1,64}")
_VISIBLE_ASCII = re.compile(r"[\x20-\x7e]+")
