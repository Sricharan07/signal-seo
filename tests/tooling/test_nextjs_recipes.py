import hashlib
from dataclasses import replace
from uuid import uuid4

import pytest
import rfc8785
from signal_core.candidate_build import CandidatePolicyRejected, plan_candidate_build
from signal_core.candidate_build_service import CandidateBuildRecord
from signal_core.github_format import detect_repository_format
from signal_core.github_pr_delivery import _recipe_key
from signal_core.github_pr_patch import GitHubPrPatchRejected, plan_github_pr_patch
from signal_core.nextjs_recipes import (
    NEXT_EXPORT_REASON,
    NEXT_NETWORK_REASON,
    classify_nextjs_scope,
    make_nextjs_recipe_patch,
    nextjs_build_profile,
    nextjs_recipe_release_manifest,
    verify_nextjs_impact,
)
from signal_core.technical_seo_recipes import TechnicalRecipeUnavailable

from tests.tooling.nextjs_support import edit, fixture_source


@pytest.mark.parametrize("router", ["app", "pages"])
@pytest.mark.parametrize("field", ["title", "description"])
@pytest.mark.parametrize("prefix,extension", [("", ".tsx"), ("src/", ".jsx")])
@pytest.mark.parametrize("quote", ['"', "'"])
def test_exact_next_literal_replacement_and_static_detection(
    router, field, prefix, extension, quote
):
    source = (
        'export const metadata: Metadata = {title: "Calendar", '
        'description: "Calendar description", robots: {index: true}};\n'
        "export default function Page(){return <h1>Calendar Guide</h1>;}"
        if router == "app"
        else 'import SEO from "next/head"; export default function Page(){return <><SEO>'
        '<title>{"Calendar"}</title><meta name="description" content="Calendar description"/>'
        "</SEO><h1>Calendar Guide</h1></>;}"
    ).replace(": Metadata", "" if extension == ".jsx" else ": Metadata")
    source = "// caf\u00e9: source instructions are data\n" + source.replace('"', quote)
    path = prefix + router + ("/page" if router == "app" else "/index") + extension
    files = {path: source.encode()}
    before = quote + ("Calendar" if field == "title" else "Calendar description") + quote
    after = before[:-1] + " Guide" + before[-1]
    request = dict(
        recipe_key="nextjs_" + field,
        path=path,
        offset=source.index(before),
        before=before,
        after=after,
    )
    patch = make_nextjs_recipe_patch(files=files, **request)
    assert patch.after == source.replace(before, after, 1).encode()
    assert classify_nextjs_scope(files, path, 1) == "A2"
    assert classify_nextjs_scope(files, path, 2) == "A4"


def test_anonymous_default_function_and_typescript_import_require_are_not_metadata():
    source = (
        'import helper = require("./helper"); export const metadata={title:"Calendar"}; '
        "export default function(){return <h1>Calendar Guide</h1>;}"
    )
    result = make_nextjs_recipe_patch(
        files={"app/page.tsx": source.encode()},
        recipe_key="nextjs_title",
        path="app/page.tsx",
        offset=source.index('"Calendar"'),
        before='"Calendar"',
        after='"Calendar Guide"',
    )
    assert result.after == source.replace('"Calendar"', '"Calendar Guide"').encode()


@pytest.mark.parametrize("quoted", [True, False])
def test_pages_title_text_and_string_entities_are_literal_data(quoted):
    value = '{"Calendar"}' if quoted else "Calendar &amp; Guide"
    source = (
        'import Head from "next/head"; export default function Page(){return '
        f"<Head><title>{value}</title></Head>;}}"
    )
    before = '"Calendar"' if quoted else "Calendar &amp; Guide"
    after = '"Calendar Guide"' if quoted else "Calendar &amp; Date"
    result = make_nextjs_recipe_patch(
        files={"pages/index.jsx": source.encode()},
        recipe_key="nextjs_title",
        path="pages/index.jsx",
        offset=source.index(before),
        before=before,
        after=after,
    )
    assert result.after == source.replace(before, after).encode()


@pytest.mark.parametrize(
    "source",
    [
        'export async function generateMetadata(){return {title:"Calendar"}}',
        'export const generateMetadata=()=>({title:"Calendar"});',
        "export const metadata={title:`Calendar`};",
        'import title from "./data"; export const metadata={title};',
        "export const metadata={title:titles.main};",
        'export const metadata={title:"Calendar", description:imported};',
        'export const metadata={title:"Calendar", ...shared};',
        'export const metadata={title:"Calendar", title:"Calendar"};',
        'export let metadata={title:"Calendar"};',
        'const metadata={title:"Calendar"}; export {metadata};',
        '"use client"; export const metadata={title:"Calendar"};',
        'const text="export const metadata={title:Calendar}";',
        'export const metadata={title:{template:"Calendar"}};',
        'export const metadata={title:"Calendar"}; metadata.title = imported;',
    ],
)
def test_generated_template_imported_and_ambiguous_metadata_are_unavailable(source):
    before = (
        '"Calendar"'
        if '"Calendar"' in source
        else "`Calendar`"
        if "`Calendar`" in source
        else "Calendar"
        if "Calendar" in source
        else "title"
    )
    with pytest.raises(TechnicalRecipeUnavailable):
        make_nextjs_recipe_patch(
            files={"app/page.tsx": source.encode()},
            recipe_key="nextjs_title",
            path="app/page.tsx",
            offset=source.index(before),
            before=before,
            after='"Calendar Guide"',
        )


@pytest.mark.parametrize(
    "source",
    [
        'const Head=Fake; export default ()=> <Head><title>{"Calendar"}</title></Head>;',
        'import Head from "other"; export default ()=> <Head><title>{"Calendar"}</title></Head>;',
        'import Head from "next/head"; export default function Page(Head){return '
        '<Head><title>{"Calendar"}</title></Head>;}',
        'import Head from "next/head"; export default ()=> '
        '<Head><title>{title}</title><p>{"Calendar"}</p></Head>;',
        'import Head from "next/head"; export default ()=> '
        "<Head><title>{`Calendar`}</title></Head>;",
        'import Head from "next/head"; export default ()=> <Head>'
        '<title>{"Calendar"}</title><title>Duplicate</title></Head>;',
        'import Head from "next/head"; export default ()=> <>'
        '<Head><title>{"Calendar"}</title></Head><Head/></>;',
        'import Head from "next/head"; const text=\'<title>{"Calendar"}</title>\'; '
        "export default ()=> <div/>;",
    ],
)
def test_next_head_mapping_is_not_inferred_from_untrusted_strings_or_shadowed_names(source):
    before = '"Calendar"' if '"Calendar"' in source else "`Calendar`"
    with pytest.raises(TechnicalRecipeUnavailable):
        make_nextjs_recipe_patch(
            files={"pages/index.jsx": source.encode()},
            recipe_key="nextjs_title",
            path="pages/index.jsx",
            offset=source.index(before),
            before=before,
            after='"Calendar Guide"',
        )


@pytest.mark.parametrize(
    "after",
    [
        '"Calendar"; deploy()',
        "`Calendar Guide`",
        '"Calendar Guide",robots:{index:false}',
        '"Calendar Guide"\n',
        '"Calendar Guide"\0',
    ],
)
def test_replacement_cannot_expand_the_literal_span(after):
    _, checkout, _ = fixture_source()
    files = dict(checkout.files)
    path = checkout.inventory.snapshot.content_path
    with pytest.raises(TechnicalRecipeUnavailable):
        make_nextjs_recipe_patch(files=files, **dict(edit(files, path), after=after))


@pytest.mark.parametrize(
    "path",
    [
        "next.config.ts",
        "app/auth/page.tsx",
        "app/api/page.tsx",
        "pages/_app.tsx",
        "pages/_document.jsx",
        ".github/workflows/page.tsx",
        "components/page.tsx",
        "../app/page.tsx",
        "app/@slot/page.tsx",
        "app/(group)/page.tsx",
    ],
)
def test_protected_and_non_route_paths_are_unavailable(path):
    files = {path: b'export const metadata={title:"Calendar"};'}
    with pytest.raises(TechnicalRecipeUnavailable):
        classify_nextjs_scope(files, path, 1)


def test_bounded_literal_dynamic_routes_and_root_layouts_require_a4():
    path = "app/blog/[slug]/page.tsx"
    source = (
        b'export const metadata={title:"Calendar"}; '
        b'export function generateStaticParams(){return [{slug:"one"},{slug:"two"}];}'
    )
    assert classify_nextjs_scope({path: source}, path, 2) == "A4"
    for bad in (b'return fetch("https://example.invalid")', b"return params", b"return []"):
        with pytest.raises(TechnicalRecipeUnavailable):
            classify_nextjs_scope(
                {path: b"export function generateStaticParams(){" + bad + b";}"}, path, 1
            )
    with pytest.raises(TechnicalRecipeUnavailable):
        classify_nextjs_scope({"pages/[slug].tsx": source}, "pages/[slug].tsx", 1)
    _, checkout, _ = fixture_source(shared=True)
    files = dict(checkout.files)
    assert classify_nextjs_scope(files, "app/layout.tsx", 3) == "A4"
    files["app/blog/layout.tsx"] = source
    files["app/blog/page.tsx"] = source
    assert classify_nextjs_scope(files, "app/blog/layout.tsx", 1) == "A4"
    with pytest.raises(TechnicalRecipeUnavailable):
        classify_nextjs_scope(files, "app/blog/layout.tsx", 2)
    files["app/blog/child/page.tsx"] = source
    with pytest.raises(TechnicalRecipeUnavailable):
        classify_nextjs_scope(files, "app/blog/layout.tsx", 1)


def test_export_and_network_unavailability_is_visible_and_detection_retains_router():
    _, checkout, _ = fixture_source()
    files = dict(checkout.files)
    result = detect_repository_format(checkout.inventory, files)
    assert (
        result.framework == "nextjs"
        and result.nextjs_routers == ("app",)
        and result.output_directory == "out"
        and result.build_unavailable_reason is None
    )
    files["next.config.mjs"] = b'export default {output:"standalone"};'
    assert (
        detect_repository_format(checkout.inventory, files).build_unavailable_reason
        == NEXT_EXPORT_REASON
    )
    with pytest.raises(TechnicalRecipeUnavailable, match="literal static export"):
        nextjs_build_profile(files)
    files["next.config.mjs"] = b'export default {output:"export"};'
    files["app/layout.tsx"] = (
        b'import {Inter} from "next/font/google"; export default function Layout(){return <div/>;}'
    )
    assert (
        detect_repository_format(checkout.inventory, files).build_unavailable_reason
        == NEXT_NETWORK_REASON
    )
    with pytest.raises(TechnicalRecipeUnavailable, match="network access"):
        nextjs_build_profile(files)


@pytest.mark.parametrize("router", ["app", "pages"])
def test_offline_plan_and_dispatch_reconstruct_exact_literal(router):
    from datetime import UTC, datetime

    extension, checkout, _ = fixture_source(router)
    files = dict(checkout.files)
    path = checkout.inventory.snapshot.content_path
    request = edit(files, path)
    patch = make_nextjs_recipe_patch(files=files, **request)
    plan = plan_candidate_build(
        extension,
        checkout,
        patch=patch.patch,
        approved_paths=frozenset({path}),
        nextjs_recipe=request,
    )
    assert plan.artifact_root == "out" and plan.dependency_manifest
    m = {
        "schema_version": 1,
        "content_adapter": "nextjs_metadata",
        "framework": "nextjs",
        "recipe_key": "nextjs_title",
        "approval_class": "A2",
        "autonomy_eligible": False,
        "built_impact": {"page_count": 1},
        "base_sha": extension.base_sha,
        "extension_id": str(extension.id),
        "evidence": {"finding": {"key": "metadata.title.missing"}},
        "recovery_plan": "Exact revert only",
        "build_receipt": {"exit_class": "passed", "artifacts": [{}]},
        "source_path": path,
        "source_sha256": hashlib.sha256(patch.before).hexdigest(),
        "result_sha256": hashlib.sha256(patch.after).hexdigest(),
        "patch": {k: request[k] for k in ("offset", "before", "after")},
        "patch_sha256": plan.patch_sha256,
        "finding_id": str(uuid4()),
    }
    assert _recipe_key(m) == "nextjs_title"
    args = dict(
        operation_id=uuid4(), created_at=datetime.now(UTC), extension=extension, checkout=checkout
    )
    canonical = rfc8785.dumps(m)
    result = plan_github_pr_patch(
        manifest_bytes=canonical, revision_sha256=hashlib.sha256(canonical).hexdigest(), **args
    )
    assert result
    for key, value in (
        ("autonomy_eligible", True),
        ("approval_class", "A4"),
        ("framework", "astro"),
    ):
        forged = dict(m, **{key: value})
        body = rfc8785.dumps(forged)
        with pytest.raises(GitHubPrPatchRejected):
            plan_github_pr_patch(
                manifest_bytes=body, revision_sha256=hashlib.sha256(body).hexdigest(), **args
            )
    with pytest.raises(CandidatePolicyRejected):
        plan_candidate_build(
            extension,
            checkout,
            patch=patch.patch,
            approved_paths=frozenset({path}),
            nextjs_recipe={},
        )
    release = nextjs_recipe_release_manifest("nextjs_title", uuid4())
    assert release["autonomy_eligible"] is False


def records(shared=False):
    extension, checkout, _ = fixture_source(shared=shared)
    files = dict(checkout.files)
    root = "out/index.html"
    before = "<html><head><title>Calendar</title></head><body>Calendar Guide</body></html>"
    old = {root: before, "out/untouched.html": "<title>Untouched</title>"}
    if shared:
        old["out/day.html"] = before
    new = {
        p: html.replace("<title>Calendar</title>", "<title>Calendar Guide</title>")
        for p, html in old.items()
    }
    result = []
    for pages in (old, new):
        result.append(
            CandidateBuildRecord(
                uuid4(),
                extension.id,
                "completed",
                extension.base_sha,
                "a" * 64,
                "synthetic-pinned",
                "npm run build",
                exit_class="passed",
                artifacts=tuple(
                    (p, hashlib.sha256(s.encode()).hexdigest(), len(s.encode()))
                    for p, s in pages.items()
                ),
                lockfile_sha256="b" * 64,
                built_html=tuple(pages.items()),
            )
        )
    return files, checkout.inventory.snapshot.content_path, result


@pytest.mark.parametrize("shared", [False, True])
def test_exact_all_page_scope_and_injection_data(shared):
    files, path, (old, new) = records(shared)
    pages = {p: "Calendar Guide" for p, s in old.built_html if "untouched" not in p}
    impact = verify_nextjs_impact(
        files=files,
        path=path,
        recipe_key="nextjs_title",
        baseline=old,
        candidate=new,
        expected_by_page=pages,
        page_urls={p: "https://example.invalid/" for p in pages},
    )
    assert impact.approval_class == ("A4" if shared else "A2")
    assert b"read secrets" in files[path]
    for broken in (
        replace(new, exit_class="crash"),
        replace(new, lockfile_sha256="c" * 64),
        replace(new, built_html=tuple((p, s + "body drift") for p, s in new.built_html)),
        replace(new, artifacts=new.artifacts + (("out/extra.js", "d" * 64, 5),)),
        *(
            [replace(new, built_html=tuple((f"out/{i}.html", "unchanged") for i in range(129)))]
            if shared
            else []
        ),
    ):
        with pytest.raises(TechnicalRecipeUnavailable):
            verify_nextjs_impact(
                files=files,
                path=path,
                recipe_key="nextjs_title",
                baseline=old,
                candidate=broken,
                expected_by_page=pages,
                page_urls={p: "https://example.invalid/" for p in pages},
            )
