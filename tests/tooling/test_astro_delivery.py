import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import rfc8785
from signal_core.astro_recipes import (
    astro_recipe_release_manifest,
    classify_astro_scope,
    make_astro_recipe_patch,
    verify_astro_impact,
)
from signal_core.candidate_build import CandidatePolicyRejected, plan_candidate_build
from signal_core.github_pr_patch import plan_github_pr_patch
from signal_core.technical_seo_recipes import TechnicalRecipeUnavailable

from tests.tooling.astro_delivery_support import fixture_source, record


@pytest.mark.parametrize(
    "path,count,expected",
    [
        ("src/pages/index.astro", 1, "A2"),
        ("src/pages/dates/[slug].astro", 1, "A4"),
        ("src/pages/dates/[slug].astro", 3, "A4"),
        ("src/layouts/Calendar.astro", 1, "A4"),
        ("src/components/Banner.astro", 1, "A4"),
        ("src/data/generator.ts", 3, "A4"),
        ("src/data/dates.json", 1, "A4"),
        ("src/content/blog/guide.json", 1, "A2"),
        ("src/content/blog/guide.json", 2, "A4"),
        ("src/content.config.ts", 1, "A4"),
    ],
)
def test_page_versus_dynamic_shared_and_collection_classification(path, count, expected):
    _, checkout, _ = fixture_source()
    assert classify_astro_scope(dict(checkout.files), path, count) == expected


def test_configured_source_directory_and_nested_component_pages_never_lower_risk():
    _, checkout, _ = fixture_source()
    files = dict(checkout.files)
    files["astro.config.mjs"] = b'export default {srcDir: "./website", outDir: "./public-build"};'
    files["website/pages/index.astro"] = b"<title>Calendar</title>"
    files["src/components/pages/index.astro"] = b"<title>Calendar</title>"
    assert classify_astro_scope(files, "website/pages/index.astro", 1) == "A2"
    assert classify_astro_scope(files, "src/pages/index.astro", 1) == "A4"
    assert classify_astro_scope(files, "src/components/pages/index.astro", 1) == "A4"


@pytest.mark.parametrize(
    "key,source,before,after",
    [
        (
            "astro_title",
            '<Layout title="Calendar">Guide</Layout>',
            '<Layout title="Calendar">',
            '<Layout title="Calendar Guide">',
        ),
        (
            "astro_description",
            '---\nconst description = "Calendar";\n---\n<Layout description={description}/> ',
            '"Calendar"',
            '"Calendar Guide"',
        ),
        (
            "astro_description",
            '<head><meta name="description" content="Calendar"></head>',
            '<meta name="description" content="Calendar">',
            '<meta name="description" content="Calendar Guide">',
        ),
        (
            "astro_title",
            "<head></head><h1>Calendar Guide</h1>",
            "",
            "<title>Calendar Guide</title>",
        ),
        (
            "astro_alt",
            '<img src="/calendar.png">',
            '<img src="/calendar.png">',
            '<img src="/calendar.png" alt="Calendar Guide">',
        ),
        (
            "astro_json_ld",
            '<script type="application/ld+json">{}</script>',
            "{}",
            '{"@context":"https://schema.org","@type":"WebPage","url":"https://example.invalid/"}',
        ),
    ],
)
def test_literal_props_frontmatter_own_markup_alt_and_json_ld(key, source, before, after):
    _, checkout, _ = fixture_source()
    path = "src/pages/index.astro"
    files = dict(checkout.files, **{path: source.encode()})
    offset = source.index(before) if before else source.index("</head>")
    patch = make_astro_recipe_patch(
        files=files, recipe_key=key, path=path, offset=offset, before=before, after=after
    )
    assert patch.after.decode() == source[:offset] + after + source[offset + len(before) :]


@pytest.mark.parametrize(
    "source",
    [
        '---\nconst x = "<title>Calendar</title>";\n---\n<p>Calendar</p>',
        '{"<title>Calendar</title>"}',
        '<script>const x = "<title>Calendar</title>";</script>',
        "---\nconst x = `\n---malformed\n<title>Calendar</title>`;\n---\n<p>Calendar</p>",
    ],
)
def test_markup_looking_server_code_and_scripts_are_not_editable(source):
    _, checkout, _ = fixture_source()
    path = "src/pages/index.astro"
    with pytest.raises(TechnicalRecipeUnavailable):
        make_astro_recipe_patch(
            files=dict(checkout.files, **{path: source.encode()}),
            recipe_key="astro_title",
            path=path,
            offset=source.index("<title>"),
            before="<title>Calendar</title>",
            after="<title>Calendar Guide</title>",
        )


@pytest.mark.parametrize(
    "before,after",
    [
        ('<Layout title="Calendar">', '<layout title="Calendar Guide">'),
        (
            '<Layout title="Calendar" onClick="handler">',
            '<Layout title="Calendar Guide" onclick="handler">',
        ),
        (
            '<img src="calendar.png" alt="Calendar">',
            '<img SRC="calendar.png" alt="Calendar Guide">',
        ),
        ('<img src="calendar.png">', '<img src="calendar.png" alt="Calendar Guide" alt="hidden">'),
    ],
)
def test_literal_metadata_edits_preserve_component_and_every_other_attribute_byte(before, after):
    _, checkout, _ = fixture_source()
    path = "src/pages/index.astro"
    source = before + ("Guide</Layout>" if before.startswith("<Layout") else "")
    with pytest.raises(TechnicalRecipeUnavailable):
        make_astro_recipe_patch(
            files=dict(checkout.files, **{path: source.encode()}),
            path=path,
            offset=0,
            recipe_key="astro_title" if before.startswith("<Layout") else "astro_alt",
            before=before,
            after=after,
        )


@pytest.mark.parametrize(
    "suffix,source,before,after",
    [
        ("json", '{"title":"Calendar","body":"Guide"}', '"Calendar"', '"Calendar Guide"'),
        (
            "md",
            '---\ntitle: "Calendar"\n---\nGuide',
            'title: "Calendar"',
            'title: "Calendar Guide"',
        ),
        (
            "yaml",
            'title: "Calendar"\nbody: "Guide"',
            'title: "Calendar"',
            'title: "Calendar Guide"',
        ),
    ],
)
def test_single_collection_entry_exact_field(suffix, source, before, after):
    _, checkout, _ = fixture_source()
    path = "src/content/blog/entry." + suffix
    files = dict(checkout.files, **{path: source.encode()})
    assert classify_astro_scope(files, path, 1) == "A2"
    make_astro_recipe_patch(
        files=files,
        recipe_key="astro_title",
        path=path,
        offset=source.index(before),
        before=before,
        after=after,
    )


@pytest.mark.parametrize(
    "path", ["src/pages/index.astro", "src/pages/dates/[slug].astro", "src/data/generator.ts"]
)
def test_metadata_expression_mapping_uses_parser_and_closed_expression(path):
    extension, checkout, _ = fixture_source()
    files = dict(checkout.files)
    source = files[path].decode()
    offset = source.index('"Calendar"')
    edit = dict(
        recipe_key="astro_title",
        path=path,
        offset=offset,
        before='"Calendar"',
        after="`Calendar ${date}`",
    )
    patch = make_astro_recipe_patch(files=files, **edit)
    plan = plan_candidate_build(
        extension, checkout, patch=patch.patch, approved_paths=frozenset({path}), astro_recipe=edit
    )
    assert plan.dependency_manifest and plan.patch_sha256 != hashlib.sha256(b"").hexdigest()
    for after in (
        'fetch("https://example.invalid")',
        '"Calendar"; steal()',
        "`Calendar ${run()}`",
        "process.env.KEY",
    ):
        with pytest.raises(TechnicalRecipeUnavailable):
            make_astro_recipe_patch(files=files, **dict(edit, after=after))


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/build.yml",
        "auth/access.ts",
        "src/payments/checkout.astro",
        "package.json",
        "astro.config.mjs",
    ],
)
def test_workflow_and_protected_paths_cannot_be_recipe_edits(path):
    _, checkout, _ = fixture_source()
    files = dict(checkout.files, **{path: b'const title = "Calendar";'})
    with pytest.raises(TechnicalRecipeUnavailable):
        make_astro_recipe_patch(
            files=files,
            recipe_key="astro_title",
            path=path,
            offset=14,
            before='"Calendar"',
            after='"Calendar Guide"',
        )


def test_exact_impact_count_bounded_samples_and_out_of_scope_rejection():
    _, checkout, _ = fixture_source()
    pages = {
        f"public-build/dates/2026-01-0{i}/index.html": (
            "<html><head><title>Calendar</title></head><body>Calendar Guide</body></html>"
        )
        for i in range(1, 4)
    }
    pages["public-build/index.html"] = "<html><title>Unchanged</title></html>"
    new = dict(pages)
    targets = {p: "Calendar Guide" for p in pages if "/dates/" in p}
    for p in targets:
        new[p] = new[p].replace("<title>Calendar</title>", "<title>Calendar Guide</title>")
    args = dict(
        files=dict(checkout.files),
        path="src/pages/dates/[slug].astro",
        recipe_key="astro_title",
        baseline=record(pages),
        candidate=record(new),
        expected_by_page=targets,
        page_urls={p: "https://example.invalid/" for p in targets},
    )
    impact = verify_astro_impact(**args)
    assert impact.approval_class == "A4" and len(impact.pages) == 3 and len(impact.samples) == 3
    assert len(impact.assertions) == 3 and len(impact.canonical_scope) < 8192
    for change in (
        "outside",
        "within",
        "removed",
        "asset",
        "wrong_field",
        "omitted_target",
        "forged_digest",
    ):
        candidate = record(new)
        expected = targets
        urls = args["page_urls"]
        if change == "outside":
            candidate = record(dict(new, **{"public-build/index.html": "changed"}))
        elif change == "within":
            candidate = record(
                {p: s.replace("Guide</body>", "Wrong</body>") for p, s in new.items()}
            )
        elif change == "removed":
            candidate = record({p: s for p, s in new.items() if p != "public-build/index.html"})
        elif change == "asset":
            candidate = replace(
                candidate, artifacts=candidate.artifacts + (("public-build/app.js", "a" * 64, 1),)
            )
        elif change == "wrong_field":
            candidate = record(
                {
                    p: s.replace("Calendar Guide</title>", "Guide Calendar</title>")
                    for p, s in new.items()
                }
            )
        elif change == "omitted_target":
            expected = dict(list(targets.items())[1:])
            urls = {p: urls[p] for p in expected}
        else:
            candidate = replace(
                candidate, artifacts=tuple((p, "0" * 64, n) for p, _, n in candidate.artifacts)
            )
        with pytest.raises(TechnicalRecipeUnavailable):
            verify_astro_impact(
                **dict(args, candidate=candidate, expected_by_page=expected, page_urls=urls)
            )


def test_sealed_astro_git_patch_uses_same_mapper_and_exact_digest():
    extension, checkout, _ = fixture_source()
    path = "src/pages/dates/[slug].astro"
    source = dict(checkout.files)[path].decode()
    edit = dict(
        recipe_key="astro_title",
        path=path,
        offset=source.index('"Calendar"'),
        before='"Calendar"',
        after="`Calendar ${date}`",
    )
    patch = make_astro_recipe_patch(files=dict(checkout.files), **edit)
    plan = plan_candidate_build(
        extension, checkout, patch=patch.patch, approved_paths=frozenset({path}), astro_recipe=edit
    )
    manifest = {
        "schema_version": 1,
        "framework": "astro",
        "recipe_key": "astro_title",
        "approval_class": "A4",
        "autonomy_eligible": False,
        "base_sha": extension.base_sha,
        "extension_id": str(extension.id),
        "source_path": path,
        "source_sha256": hashlib.sha256(patch.before).hexdigest(),
        "result_sha256": hashlib.sha256(patch.after).hexdigest(),
        "patch": {k: edit[k] for k in ("offset", "before", "after")},
        "patch_sha256": plan.patch_sha256,
        "built_impact": {"page_count": 3},
        "finding_id": str(uuid4()),
        "evidence": {"finding": {"key": "metadata.title.duplicate"}},
        "recovery_plan": patch.recovery_plan,
        "build_receipt": {"exit_class": "passed", "artifacts": [{}]},
    }
    canonical = rfc8785.dumps(manifest)
    result = plan_github_pr_patch(
        manifest_bytes=canonical,
        revision_sha256=hashlib.sha256(canonical).hexdigest(),
        operation_id=uuid4(),
        created_at=datetime.now(UTC),
        extension=extension,
        checkout=checkout,
    )
    assert result.path == path and result.content == patch.after
    assert astro_recipe_release_manifest("astro_title", uuid4())["autonomy_eligible"] is False
    with pytest.raises(CandidatePolicyRejected):
        plan_candidate_build(
            extension, checkout, patch=patch.patch, approved_paths=frozenset({path})
        )
