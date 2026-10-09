import pytest
from signal_core.candidate_build import CandidatePolicyRejected, plan_candidate_build
from signal_core.indexnow_placement import indexnow_key_placement

from tests.tooling.astro_build_support import astro_source

KEY = "synthetic-indexnow-key-0144"


@pytest.mark.parametrize("public", ["public", "static/assets"])
def test_astro_key_is_added_only_to_the_served_public_directory(public):
    extension, checkout = astro_source(
        config=f"export default {{publicDir: './{public}',outDir: './public-build'}};".encode()
    )
    path, output = indexnow_key_placement("astro", dict(checkout.files), KEY)
    assert path == public + "/" + KEY + ".txt"
    assert output == "public-build/" + KEY + ".txt"
    plan = plan_candidate_build(
        extension,
        checkout,
        patch={path: KEY.encode()},
        approved_paths=frozenset({path}),
        indexnow_key=KEY,
    )
    assert dict(plan.files)[path] == KEY.encode()
    with pytest.raises(CandidatePolicyRejected, match="INDEXNOW_KEY_PLACEMENT_REJECTED"):
        plan_candidate_build(
            extension,
            checkout,
            patch={KEY + ".txt": KEY.encode()},
            approved_paths=frozenset({KEY + ".txt"}),
            indexnow_key=KEY,
        )


def test_default_public_and_other_framework_rules_are_unchanged():
    extension, checkout = astro_source()
    assert indexnow_key_placement("astro", dict(checkout.files), KEY) == (
        "public/" + KEY + ".txt",
        "public-build/" + KEY + ".txt",
    )
    assert indexnow_key_placement("eleventy", {}, KEY) == (KEY + ".txt", "_site/" + KEY + ".txt")
    assert indexnow_key_placement("nextjs", {}, KEY) == (KEY + ".txt", ".next/" + KEY + ".txt")
    with pytest.raises(ValueError):
        indexnow_key_placement("unknown", {}, KEY)


@pytest.mark.parametrize(
    "value",
    [
        "false",
        "getDirectory()",
        "'../public'",
        "'/tmp/public'",
        "'public-build'",
        "'public-build/assets'",
    ],
)
def test_astro_public_directory_must_be_literal_safe_and_separate_from_output(value):
    _, checkout = astro_source(
        config=f"export default {{publicDir: {value},outDir: './public-build'}};".encode()
    )
    with pytest.raises(ValueError):
        indexnow_key_placement("astro", dict(checkout.files), KEY)
