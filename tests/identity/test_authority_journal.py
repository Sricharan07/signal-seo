"""The journal envelope is a restrictive, canonical, typed wire contract."""

import json
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

import pytest
import rfc8785
from signal_core.authority_journal import AuthorityJournalUnavailable, RestrictionRecord


def record() -> RestrictionRecord:
    return RestrictionRecord(
        uuid4(), uuid4(), uuid4(), 1, datetime.now(UTC), sha256(b"facts").hexdigest()
    )


def test_canonical_record_round_trips_without_grant_authority():
    original = record()
    assert RestrictionRecord.from_body(original.canonical_body()) == original
    document = json.loads(original.canonical_body())
    assert document["restriction_kind"] == "session_revoked"
    assert document["scope"] == {"kind": "platform"}
    assert document["target"]["kind"] == "identity_session"


@pytest.mark.parametrize(
    "kind",
    ["slack_binding", "slack_link", "wordpress_binding", "github_pr_extension", "invitation"],
)
def test_slack_journal_records_are_deny_only(kind):
    original = record()
    restriction = RestrictionRecord(
        original.event_id,
        original.actor_user_id,
        original.target_id,
        1,
        original.event_time,
        original.payload_hash,
        kind,
        kind + "_revoked",
    )
    assert RestrictionRecord.from_body(restriction.canonical_body()) == restriction
    with pytest.raises(ValueError):
        RestrictionRecord(
            original.event_id,
            original.actor_user_id,
            original.target_id,
            1,
            original.event_time,
            original.payload_hash,
            kind,
            kind + "_linked",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("restriction_kind", "session_granted"),
        ("effective_epoch", 0),
        ("effective_epoch", True),
        ("scope", {"kind": "tenant"}),
        ("target", {"kind": "membership", "id": str(uuid4())}),
        ("actor", {"kind": "workload", "id": str(uuid4())}),
    ],
)
def test_unrecognized_or_authority_enlarging_record_is_denied(field, value):
    document = json.loads(record().canonical_body())
    document[field] = value
    with pytest.raises(AuthorityJournalUnavailable):
        RestrictionRecord.from_body(rfc8785.dumps(document))


def test_noncanonical_and_extra_fields_are_denied():
    document = json.loads(record().canonical_body())
    with pytest.raises(AuthorityJournalUnavailable):
        RestrictionRecord.from_body(json.dumps(document).encode())
    document["approval"] = True
    with pytest.raises(AuthorityJournalUnavailable):
        RestrictionRecord.from_body(rfc8785.dumps(document))


def test_naive_timestamp_and_bad_digest_are_denied():
    original = record()
    with pytest.raises(ValueError):
        RestrictionRecord(
            original.event_id,
            original.actor_user_id,
            original.target_id,
            1,
            datetime.now(),
            original.payload_hash,
        )
    with pytest.raises(ValueError):
        RestrictionRecord(
            original.event_id,
            original.actor_user_id,
            original.target_id,
            1,
            original.event_time,
            "not-a-hash",
        )
    with pytest.raises(ValueError):
        RestrictionRecord(
            original.event_id,
            original.actor_user_id,
            original.target_id,
            True,
            original.event_time,
            original.payload_hash,
        )
    with pytest.raises(ValueError):
        RestrictionRecord(
            original.event_id,
            original.actor_user_id,
            original.target_id,
            1,
            original.event_time,
            original.payload_hash.upper(),
        )
