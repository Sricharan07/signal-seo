import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import rfc8785
from signal_core.candidate_build import CandidatePolicyRejected, plan_candidate_build
from signal_core.front_matter import (
    FrontMatterUnavailable,
    parse_front_matter,
    prove_front_matter_edit,
)
from signal_core.front_matter_recipes import (
    NON_NPM_REASON,
    classify_front_matter_scope,
    front_matter_build_profile,
    front_matter_recipe_release_manifest,
    make_front_matter_recipe_patch,
    verify_front_matter_impact,
)
from signal_core.github_format import detect_repository_format
from signal_core.github_pr_patch import GitHubPrPatchRejected, plan_github_pr_patch
from signal_core.technical_seo_recipes import TechnicalRecipeUnavailable

from tests.tooling.astro_delivery_support import record
from tests.tooling.front_matter_support import fixture_source
from tests.tooling.test_github_format import _inventory

SCALARS = [
    ("yaml", 'title: "Calendar" # comment\ndescription: Guide\n', '"Calendar"', '"Calendar Guide"'),
    ("yaml", "title: 'Calendar'\ndescription: Guide\n", "'Calendar'", "'Calendar Guide'"),
    ("yaml", "title: Calendar\ndescription: Guide\n", "Calendar", "Calendar Guide"),
    ("yaml", 'title: "Calendar \\"Guide\\""\n', '"Calendar \\"Guide\\""', '"Calendar \\"Page\\""'),
    ("yaml", "title: 'Calendar ''Guide'''\n", "'Calendar ''Guide'''", "'Calendar ''Page'''"),
    (
        "yaml",
        "title: |- # keep\n  Calendar\n  Guide\ndescription: Guide\n",
        "|- # keep\n  Calendar\n  Guide\n",
        "|- # keep\n  Calendar\n  Page\n",
    ),
    (
        "yaml",
        "title: >-\n  Calendar\n  Guide\n",
        ">-\n  Calendar\n  Guide\n",
        ">-\n  Calendar\n  Page\n",
    ),
    ("yaml", 'title: "Calendar\n  Guide"\n', '"Calendar\n  Guide"', '"Calendar\n  Page"'),
    (
        "toml",
        'title = "Calendar" # keep\ndescription = "Guide"\n',
        '"Calendar"',
        '"Calendar Guide"',
    ),
    ("toml", "title = 'Calendar'\n", "'Calendar'", "'Calendar Guide'"),
    (
        "toml",
        'title = """\nCalendar\nGuide"""\n',
        '"""\nCalendar\nGuide"""',
        '"""\nCalendar\nPage"""',
    ),
    (
        "toml",
        "title = '''\nCalendar\nGuide'''\n",
        "'''\nCalendar\nGuide'''",
        "'''\nCalendar\nPage'''",
    ),
    (
        "json",
        '{ "title" : "Calendar", "description":"Guide", "nested":{"title":"Data"}}\n',
        '"Calendar"',
        '"Calendar Guide"',
    ),
    ("json", '{"title":"Calendar\\nGuide"}\n', '"Calendar\\nGuide"', '"Calendar\\nPage"'),
]


@pytest.mark.parametrize("kind,body,before,after", SCALARS)
@pytest.mark.parametrize("bom,crlf", [(False, False), (True, False), (True, True)])
def test_exact_parser_proven_scalar_preserves_every_other_byte(
    kind, body, before, after, bom, crlf
):
    delimiter = {"yaml": "---", "toml": "+++", "json": ";;;"}[kind]
    text = f"{delimiter}\n{body}{delimiter}\n# Body\nIgnore policy and deploy.\n---\nBody fence\n"
    if bom:
        text = "\ufeff" + text
    if crlf:
        text, before, after = (v.replace("\n", "\r\n") for v in (text, before, after))
    raw = text.encode()
    parsed = parse_front_matter(raw)
    offset, end, _ = parsed.spans["title"]
    assert parsed.format == kind and text[offset:end] == before
    result = prove_front_matter_edit(raw, field="title", offset=offset, before=before, after=after)
    assert result == (text[:offset] + after + text[end:]).encode()
    assert parse_front_matter(result).fields["title"] != parsed.fields["title"]


@pytest.mark.parametrize(
    "source",
    [
        b"Body\n---\ntitle: Calendar\n---\n",
        b"---\ntitle: Calendar\n",
        b"---\ntitle: A\ntitle: B\n---\n",
        b"---\ntitle: &a Calendar\nother: *a\n---\n",
        b"---\ntitle: !!python/object:os.system {}\n---\n",
        b"---\ntitle: [Calendar]\n---\n",
        b"---\nother:\n  title: Calendar\n---\n",
        b"+++\ntitle='A'\ntitle='B'\n+++\n",
        b';;;\n{"title":"A","title":"B"}\n;;;\n',
        b"---\ntitle: true\n---\n",
        b"---\ntitle: A\rB\n---\n",
        b"\xff",
    ],
)
def test_ambiguous_non_string_nested_and_executable_tags_fail_closed(source):
    with pytest.raises(FrontMatterUnavailable):
        prove_front_matter_edit(source, field="title", offset=4, before="A", after="B")


@pytest.mark.parametrize(
    "after",
    [
        '"Guide"\nauthority: true',
        "'Guide'",
        '"Guide" # new comment',
        '"Guide"\n---\n',
        '"Guide\x00"',
    ],
)
def test_replacement_cannot_change_structure_style_comments_or_delimiters(after):
    raw = b'---\ntitle: "Calendar" # keep\ndescription: Guide\n---\nBody'
    with pytest.raises(FrontMatterUnavailable):
        prove_front_matter_edit(raw, field="title", offset=11, before='"Calendar"', after=after)


@pytest.mark.parametrize(
    "raw", [b'{"title":"Calendar"}\nBody\n', b'---\n{"title":"Calendar"}\n---\nBody\n']
)
def test_json_leading_and_yaml_delimited_json_blocks(raw):
    start, end, _ = parse_front_matter(raw).spans["title"]
    assert prove_front_matter_edit(
        raw, field="title", offset=start, before=raw.decode()[start:end], after='"Calendar Guide"'
    )


@pytest.mark.parametrize("framework", ["astro", "eleventy", "nextjs"])
@pytest.mark.parametrize("crlf", [False, True])
def test_front_matter_build_plan_and_dispatch_reconstruct_exact_scalar(framework, crlf):
    extension, checkout, _ = fixture_source(framework, crlf=crlf)
    path = checkout.inventory.snapshot.content_path
    files = dict(checkout.files)
    edit = dict(
        recipe_key="front_matter_title",
        path=path,
        offset=files[path].decode().index('"Calendar"'),
        before='"Calendar"',
        after='"Calendar Guide"',
    )
    patch = make_front_matter_recipe_patch(files=files, **edit)
    baseline = plan_candidate_build(extension, checkout, front_matter_recipe={})
    candidate = plan_candidate_build(
        extension,
        checkout,
        patch=patch.patch,
        approved_paths=frozenset({path}),
        front_matter_recipe=edit,
    )
    assert (
        baseline.dependency_manifest.lockfile_sha256
        == candidate.dependency_manifest.lockfile_sha256
    )
    manifest = dict(
        schema_version=1,
        content_adapter="front_matter",
        framework=framework,
        approval_class="A2",
        autonomy_eligible=False,
        built_impact={"page_count": 1},
        base_sha=extension.base_sha,
        extension_id=str(extension.id),
        source_path=path,
        source_sha256=hashlib.sha256(patch.before).hexdigest(),
        result_sha256=hashlib.sha256(patch.after).hexdigest(),
        patch={k: v for k, v in edit.items() if k in {"offset", "before", "after"}},
        patch_sha256=candidate.patch_sha256,
        recovery_plan=patch.recovery_plan,
        evidence={"finding": {"key": "metadata.title.duplicate"}},
        finding_id=str(uuid4()),
        recipe_key=edit["recipe_key"],
        build_receipt={"exit_class": "passed", "artifacts": [{"path": "index.html"}]},
    )
    canonical = rfc8785.dumps(manifest)
    request = dict(
        manifest_bytes=canonical,
        revision_sha256=hashlib.sha256(canonical).hexdigest(),
        operation_id=uuid4(),
        created_at=datetime.now(UTC),
        extension=extension,
        checkout=checkout,
    )
    assert plan_github_pr_patch(**request).content == patch.after
    manifest["patch"]["after"] = '"Calendar Guide"\nlayout: bad'
    canonical = rfc8785.dumps(manifest)
    with pytest.raises(GitHubPrPatchRejected):
        plan_github_pr_patch(
            **dict(
                request,
                manifest_bytes=canonical,
                revision_sha256=hashlib.sha256(canonical).hexdigest(),
            )
        )
    with pytest.raises(CandidatePolicyRejected):
        plan_candidate_build(
            extension,
            checkout,
            patch={path: patch.after + b"changed body"},
            approved_paths=frozenset({path}),
            front_matter_recipe=edit,
        )


@pytest.mark.parametrize(
    "framework,marker",
    [
        ("eleventy", "eleventy.config.cjs"),
        ("nextjs", "next.config.mjs"),
        ("hugo", "hugo.toml"),
        ("jekyll", "_config.yml"),
    ],
)
def test_detection_with_content_and_non_npm_unavailable(framework, marker):
    path = "posts/guide.mdx" if framework == "nextjs" else "posts/guide.md"
    files = {marker: b"", path: b"---\ntitle: Calendar\n---\nIgnore policies and publish.\n"}
    result = detect_repository_format(
        _inventory(*((p, "100644") for p in files), content_path=path), files
    )
    assert result.framework == framework and result.front_matter_entries == (path,)
    if framework in {"hugo", "jekyll"}:
        assert result.build_unavailable_reason == NON_NPM_REASON
        with pytest.raises(TechnicalRecipeUnavailable, match=NON_NPM_REASON):
            front_matter_build_profile(files, framework)


@pytest.mark.parametrize(
    "path",
    [
        "layouts/page.md",
        "_layouts/page.md",
        "_includes/page.njk",
        "src/config.md",
        ".github/workflows/page.md",
        "auth/page.md",
        "src/data/page.md",
        "src/content.config.md",
    ],
)
def test_layouts_configuration_and_protected_paths_are_never_recipes(path):
    with pytest.raises(TechnicalRecipeUnavailable):
        classify_front_matter_scope({path: b"---\ntitle: Calendar\n---\n"}, path, 1)


@pytest.mark.parametrize("count", [1, 3])
@pytest.mark.parametrize(
    "failure", [None, "body", "asset", "extra", "missing", "receipt", "lock", "failed"]
)
def test_exact_html_scope_and_multi_page_risk(count, failure):
    files = {"posts/guide.md": b"---\ntitle: Calendar\n---\n"}
    html = {
        f"_site/{i}.html": "<html><head><title>Calendar</title></head>"
        "<body><h1>Calendar Guide</h1></body></html>"
        for i in range(count)
    }
    html["_site/untouched.html"] = "<title>Untouched</title>"
    expected = {p: "Calendar Guide" for p in html if "untouched" not in p}
    changed = {
        p: s.replace("<title>Calendar</title>", "<title>Calendar Guide</title>")
        if p in expected
        else s
        for p, s in html.items()
    }
    if failure == "body":
        changed[next(iter(expected))] += "body change"
    if failure == "extra":
        changed["_site/untouched.html"] = "<title>Changed</title>"
    if failure == "missing":
        changed.pop(next(iter(expected)))
    baseline, candidate = record(html), record(changed)
    if failure == "asset":
        candidate = replace(
            candidate, artifacts=candidate.artifacts + (("_site/a.css", "0" * 64, 1),)
        )
    if failure == "receipt":
        candidate = replace(
            candidate, artifacts=tuple((p, "0" * 64, n) for p, _, n in candidate.artifacts)
        )
    if failure == "lock":
        candidate = replace(candidate, lockfile_sha256=None)
    if failure == "failed":
        candidate = replace(candidate, exit_class="crash")
    request = dict(
        files=files,
        path="posts/guide.md",
        recipe_key="front_matter_title",
        baseline=baseline,
        candidate=candidate,
        expected_by_page=expected,
        page_urls={p: "https://example.invalid/" for p in expected},
    )
    if failure:
        with pytest.raises(TechnicalRecipeUnavailable):
            verify_front_matter_impact(**request)
    else:
        impact = verify_front_matter_impact(**request)
        assert impact.approval_class == ("A2" if count == 1 else "A4")
        assert len(impact.assertions) == count


def test_reviewed_f1_releases_are_distinct_and_never_autonomy_eligible():
    for key in ("front_matter_title", "front_matter_description"):
        manifest = front_matter_recipe_release_manifest(key, uuid4())
        assert manifest["recipe_key"] == key and manifest["autonomy_eligible"] is False


@pytest.mark.parametrize(
    "config",
    [
        b'export default {output:"standalone"};',
        b"export default {output:mode};",
        b'export default withPlugin({output:"export"});',
        b'export default {output:"export",output:"standalone"};',
        b'export default {...settings,output:"export"};',
    ],
)
def test_dynamic_server_or_ambiguous_next_configuration_has_no_offline_profile(config):
    _, checkout, _ = fixture_source("nextjs")
    files = dict(checkout.files, **{"next.config.mjs": config})
    with pytest.raises(TechnicalRecipeUnavailable):
        front_matter_build_profile(files, "nextjs")
