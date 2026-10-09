"""Real PostgreSQL checks for separately reviewed, closed-family eligibility."""

from uuid import uuid4

import pytest
from psycopg.errors import InsufficientPrivilege
from signal_core.recipe_autonomy import attest_recipe_autonomy, read_recipe_autonomy
from signal_core.recipe_releases import RecipeReleaseUnavailable, transition_recipe_release

from tests.control_plane.test_recipe_releases import _actor, _key, _register, _review
from tests.control_plane.test_technical_recipes import _register_release
from tests.control_plane.test_technical_recipes import release_manager as release_manager


@pytest.mark.parametrize(
    "key",
    [
        "technical_title",
        "technical_description",
        "technical_alt",
        "technical_structured_data",
        "technical_broken_link",
    ],
)
def test_autonomy_attestation_is_separate_immutable_and_platform_only(
    admin, api, workflow, release_manager, key
):
    release_id, actor_id = _register_release(
        admin, release_manager, key, reviewed=True, version="1.0.900"
    )
    assert not read_recipe_autonomy(api, release_id=release_id)
    with pytest.raises(InsufficientPrivilege):
        attest_recipe_autonomy(
            api,
            release_id=release_id,
            recipe_key=key,
            actor_user_id=actor_id,
            attestation_id=uuid4(),
        )
    attest_recipe_autonomy(
        release_manager,
        release_id=release_id,
        recipe_key=key,
        actor_user_id=actor_id,
        attestation_id=uuid4(),
    )
    assert read_recipe_autonomy(workflow, release_id=release_id)
    with pytest.raises(InsufficientPrivilege):
        workflow.execute("SELECT * FROM control.recipe_autonomy_attestations")
    transition_recipe_release(
        release_manager,
        release_id=release_id,
        expected_status="REVIEWED",
        new_status="REVOKED",
        actor_user_id=actor_id,
        reason="Synthetic revocation",
    )
    assert not read_recipe_autonomy(workflow, release_id=release_id)


@pytest.mark.parametrize(
    "key",
    [
        "technical_canonical",
        "technical_robots",
        "technical_redirect",
        "technical_sitemap",
        "shared_template",
        "product_claims",
    ],
)
def test_forbidden_families_never_become_autonomy_eligible(admin, api, release_manager, key):
    with pytest.raises(RecipeReleaseUnavailable):
        attest_recipe_autonomy(
            api, release_id=uuid4(), recipe_key=key, actor_user_id=uuid4(), attestation_id=uuid4()
        )
    actor = _actor(admin)
    signing = _key(release_manager)
    release_id = uuid4()
    # A reviewed release explicitly claiming A2 is still not enough.
    _register(admin, release_manager, signing, actor, release_id, "1.0.901", family=key)
    _review(release_manager, release_id, actor)
    digest = admin.execute(
        "SELECT content_hash FROM control.recipe_releases WHERE id=%s", (release_id,)
    ).fetchone()[0]
    assert release_manager.execute(
        "SELECT control.attest_recipe_autonomy(%s,%s,%s,%s)", (release_id, digest, actor, uuid4())
    ).fetchone() == ("attestation_denied",)
    # Even forged registry storage cannot widen the closed family policy.
    admin.execute(
        "INSERT INTO control.recipe_autonomy_attestations "
        "VALUES(%s,%s,%s,'technical-a2-1','metadata_pr',%s,transaction_timestamp())",
        (uuid4(), release_id, digest, actor),
    )
    assert not read_recipe_autonomy(api, release_id=release_id)
