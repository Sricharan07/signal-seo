"""Separate platform attestation for a closed set of A2 technical recipes."""

from uuid import UUID

from psycopg import Connection

from signal_core.database import _clean_transaction
from signal_core.recipe_releases import RecipeReleaseUnavailable, get_reviewed_recipe_release
from signal_core.technical_seo_recipes import technical_recipe_release_manifest

AUTONOMY_RECIPES = frozenset(
    {
        "technical_title",
        "technical_description",
        "technical_alt",
        "technical_structured_data",
        "technical_broken_link",
    }
)


def attest_recipe_autonomy(
    connection: Connection,
    *,
    release_id: UUID,
    recipe_key: str,
    actor_user_id: UUID,
    attestation_id: UUID,
) -> None:
    if recipe_key not in AUTONOMY_RECIPES:
        raise RecipeReleaseUnavailable("Recipe cannot be autonomy eligible.")
    release = get_reviewed_recipe_release(connection, recipe_key=recipe_key, release_id=release_id)
    if release.manifest != technical_recipe_release_manifest(
        recipe_key, release_id, version=str(release.version)
    ):
        raise RecipeReleaseUnavailable("Exact technical recipe contract is required.")
    with _clean_transaction(connection):
        result = connection.execute(
            "SELECT control.attest_recipe_autonomy(%s,%s,%s,%s)",
            (release_id, bytes.fromhex(release.content_hash), actor_user_id, attestation_id),
        ).fetchone()
    if result is None or result[0] != "attested":
        raise RecipeReleaseUnavailable("Platform autonomy attestation is unavailable.")


def read_recipe_autonomy(connection: Connection, *, release_id: UUID) -> bool:
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT control.recipe_autonomy_eligible(%s)", (release_id,)
        ).fetchone()
    return row is not None and row[0] is True
