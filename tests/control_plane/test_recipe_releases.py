"""Real PostgreSQL privilege, lifecycle, resolution, and revocation checks."""

import os
from uuid import uuid4

import psycopg
import pytest
import rfc8785
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from psycopg.errors import InsufficientPrivilege
from signal_core.recipe_releases import (
    VERIFIED_HOMEPAGE_METADATA_RELEASE_ID,
    RecipeReleaseConflict,
    RecipeReleaseUnavailable,
    RecipeVersion,
    recipe_release_dispatch_eligible,
    register_recipe_release,
    register_recipe_signing_key,
    resolve_reviewed_recipe_range,
    transition_recipe_release,
)


@pytest.fixture
def release_manager():
    with psycopg.connect(
        os.environ["SIGNAL_TEST_RELEASE_MANAGER_DSN"], autocommit=True
    ) as connection:
        yield connection


def _actor(admin):
    actor_id = uuid4()
    admin.execute(
        "INSERT INTO control.users (id,oidc_issuer,oidc_subject,display_name) "
        "VALUES (%s,'https://identity.example.invalid',%s,'Synthetic release reviewer')",
        (actor_id, str(actor_id)),
    )
    return actor_id


def _manifest(release_id, version, *, key="technical_metadata", mode="pull_request"):
    return {
        "schema_version": 1,
        "kind": "recipe",
        "release_id": str(release_id),
        "recipe_key": key,
        "version": version,
        "contract_version": 1,
        "delivery_mode": mode,
        "purpose": "Bounded synthetic registry qualification, not executable delivery.",
        "allowed_fields": ["meta_description"],
        "allowed_resource_types": ["verified_homepage"],
        "required_evidence": ["verified_page"],
        "steps": ["validate_evidence", "prepare_patch"],
        "max_resources_per_revision": 1,
        "approval_class": "A2",
        "recovery_mode": "manual_review",
        "verification_assertions": ["no_external_write_in_lab"],
    }


def _register(
    admin, release_manager, key, actor, release_id, version, *, family="technical_metadata"
):
    body = rfc8785.dumps(_manifest(release_id, version, key=family))
    register_recipe_release(
        release_manager,
        release_id=release_id,
        recipe_key=family,
        version=version,
        canonical_body=body,
        signing_key_id=key[0],
        signature=key[1].sign(body),
        actor_user_id=actor,
    )


def _key(release_manager):
    signing_key = Ed25519PrivateKey.generate()
    key_id = "test_registry_" + uuid4().hex
    register_recipe_signing_key(
        release_manager,
        key_id=key_id,
        public_key=signing_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        ),
    )
    return key_id, signing_key


def _review(release_manager, release_id, actor):
    assert (
        transition_recipe_release(
            release_manager,
            release_id=release_id,
            expected_status="DRAFT",
            new_status="TESTED",
            actor_user_id=actor,
            reason="Synthetic checks passed",
        )
        == 2
    )
    assert (
        transition_recipe_release(
            release_manager,
            release_id=release_id,
            expected_status="TESTED",
            new_status="REVIEWED",
            actor_user_id=actor,
            reason="Platform review accepted",
        )
        == 3
    )


def test_seeded_homepage_release_is_reviewed_signed_and_proposal_only(api, admin):
    result = resolve_reviewed_recipe_range(
        api,
        recipe_key="title_description_improvement",
        minimum_inclusive="1.0.0",
        maximum_exclusive="2.0.0",
    )
    assert result == (VERIFIED_HOMEPAGE_METADATA_RELEASE_ID,)
    assert admin.execute(
        "SELECT status FROM control.recipe_release_events WHERE release_id=%s "
        "ORDER BY sequence_number",
        (VERIFIED_HOMEPAGE_METADATA_RELEASE_ID,),
    ).fetchall() == [("DRAFT",), ("TESTED",), ("REVIEWED",)]
    assert admin.execute(
        "SELECT encode(content_hash,'hex') FROM control.recipe_releases WHERE id=%s",
        (VERIFIED_HOMEPAGE_METADATA_RELEASE_ID,),
    ).fetchone() == ("2f5bf797d5cbf5ab005a865996a785d2502792be01641cb5396753acacb7c793",)
    assert api.execute(
        "SELECT control.recipe_release_dispatch_eligible(%s)",
        (VERIFIED_HOMEPAGE_METADATA_RELEASE_ID,),
    ).fetchone() == (False,)
    assert not recipe_release_dispatch_eligible(
        api,
        recipe_key="title_description_improvement",
        release_id=VERIFIED_HOMEPAGE_METADATA_RELEASE_ID,
    )


def test_tenant_and_workflow_cannot_create_or_change_releases(admin, api, workflow):
    release_id = VERIFIED_HOMEPAGE_METADATA_RELEASE_ID
    for connection in (api, workflow):
        with pytest.raises(InsufficientPrivilege):
            connection.execute(
                "SELECT control.register_recipe_signing_key(%s,%s)",
                ("tenant_key", bytes(32)),
            )
        with pytest.raises(InsufficientPrivilege):
            connection.execute(
                "UPDATE control.recipe_releases SET version_patch=9 WHERE id=%s", (release_id,)
            )
        with pytest.raises(InsufficientPrivilege):
            connection.execute(
                "DELETE FROM control.recipe_release_events WHERE release_id=%s", (release_id,)
            )
        with pytest.raises(InsufficientPrivilege):
            connection.execute(
                "SELECT control.transition_recipe_release(%s,'REVIEWED','REVOKED',%s,%s,'x')",
                (release_id, uuid4(), uuid4()),
            )
    assert admin.execute(
        "SELECT count(*) FROM control.recipe_release_events WHERE release_id=%s",
        (release_id,),
    ).fetchone() == (3,)


def test_platform_manager_cannot_rewrite_release_or_history(release_manager):
    release_id = VERIFIED_HOMEPAGE_METADATA_RELEASE_ID
    with pytest.raises(InsufficientPrivilege):
        release_manager.execute(
            "UPDATE control.recipe_releases SET version_patch=9 WHERE id=%s", (release_id,)
        )
    with pytest.raises(InsufficientPrivilege):
        release_manager.execute(
            "DELETE FROM control.recipe_release_events WHERE release_id=%s", (release_id,)
        )
    with pytest.raises(InsufficientPrivilege):
        release_manager.execute(
            "UPDATE control.recipe_signing_keys SET public_key=%s "
            "WHERE key_id='verified_homepage_seed_v1'",
            (bytes(32),),
        )


def test_reviewed_range_freezes_exact_ids_and_rejects_future_or_cross_family(
    admin, api, release_manager
):
    actor = _actor(admin)
    key = _key(release_manager)
    first, second, other_family = uuid4(), uuid4(), uuid4()
    _register(admin, release_manager, key, actor, first, "1.0.0")
    with pytest.raises(RecipeReleaseUnavailable):
        resolve_reviewed_recipe_range(
            api,
            recipe_key="technical_metadata",
            minimum_inclusive="1.0.0",
            maximum_exclusive="2.0.0",
        )
    _review(release_manager, first, actor)
    frozen = resolve_reviewed_recipe_range(
        api,
        recipe_key="technical_metadata",
        minimum_inclusive="1.0.0",
        maximum_exclusive="2.0.0",
    )
    assert frozen == (first,)
    assert recipe_release_dispatch_eligible(api, recipe_key="technical_metadata", release_id=first)
    _register(admin, release_manager, key, actor, second, "1.1.0")
    _review(release_manager, second, actor)
    _register(admin, release_manager, key, actor, other_family, "1.0.0", family="other_metadata")
    _review(release_manager, other_family, actor)
    assert frozen == (first,)
    assert resolve_reviewed_recipe_range(
        api,
        recipe_key="technical_metadata",
        minimum_inclusive="1.0.0",
        maximum_exclusive="2.0.0",
    ) == (first, second)
    assert RecipeVersion.parse("1.2.3") < RecipeVersion.parse("2.0.0")
    with pytest.raises(ValueError):
        resolve_reviewed_recipe_range(
            api,
            recipe_key="technical_metadata",
            minimum_inclusive="1.0.0",
            maximum_exclusive="3.0.0",
        )


def test_bad_signature_stale_transition_and_duplicate_version_fail_closed(
    admin, api, release_manager
):
    actor = _actor(admin)
    key = _key(release_manager)
    family = "bad_signature_" + uuid4().hex
    release_id = uuid4()
    body = rfc8785.dumps(_manifest(release_id, "1.0.0", key=family))
    with pytest.raises(RecipeReleaseUnavailable):
        register_recipe_release(
            release_manager,
            release_id=release_id,
            recipe_key=family,
            version="1.0.0",
            canonical_body=body,
            signing_key_id=key[0],
            signature=bytes(64),
            actor_user_id=actor,
        )
    assert admin.execute(
        "SELECT count(*) FROM control.recipe_releases WHERE id=%s", (release_id,)
    ).fetchone() == (0,)
    _register(admin, release_manager, key, actor, release_id, "1.0.0", family=family)
    with pytest.raises(RecipeReleaseConflict):
        _register(admin, release_manager, key, actor, uuid4(), "1.0.0", family=family)
    with pytest.raises(RecipeReleaseConflict):
        transition_recipe_release(
            release_manager,
            release_id=release_id,
            expected_status="DRAFT",
            new_status="ACTIVE",
            actor_user_id=actor,
            reason="Skipping review",
        )
    with pytest.raises(RecipeReleaseUnavailable):
        resolve_reviewed_recipe_range(
            api,
            recipe_key=family,
            minimum_inclusive="1.0.0",
            maximum_exclusive="2.0.0",
        )


def test_revocation_is_local_immediate_and_journal_pending(admin, api, release_manager):
    actor = _actor(admin)
    key = _key(release_manager)
    family = "revocation_" + uuid4().hex
    release_id = uuid4()
    _register(admin, release_manager, key, actor, release_id, "1.0.0", family=family)
    _review(release_manager, release_id, actor)
    assert recipe_release_dispatch_eligible(api, recipe_key=family, release_id=release_id)
    event_id = uuid4()
    assert (
        transition_recipe_release(
            release_manager,
            release_id=release_id,
            expected_status="REVIEWED",
            new_status="REVOKED",
            event_id=event_id,
            actor_user_id=actor,
            reason="Synthetic operator revocation",
        )
        == 4
    )
    assert not recipe_release_dispatch_eligible(api, recipe_key=family, release_id=release_id)
    with pytest.raises(RecipeReleaseUnavailable):
        resolve_reviewed_recipe_range(
            api,
            recipe_key=family,
            minimum_inclusive="1.0.0",
            maximum_exclusive="2.0.0",
        )
    assert admin.execute(
        "SELECT target_kind,restriction_kind,effective_epoch FROM "
        "control.authority_restriction_outbox WHERE event_id=%s",
        (event_id,),
    ).fetchone() == ("recipe_release", "recipe_release_revoked", 4)
    assert admin.execute(
        "SELECT event_type FROM control.platform_events WHERE id=%s",
        (event_id,),
    ).fetchone() == ("recipe.release.revoked",)
    with pytest.raises(RecipeReleaseConflict):
        transition_recipe_release(
            release_manager,
            release_id=release_id,
            expected_status="REVOKED",
            new_status="REVIEWED",
            actor_user_id=actor,
            reason="Forbidden reactivation",
        )
