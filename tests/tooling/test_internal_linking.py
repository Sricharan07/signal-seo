import hashlib
from collections import Counter
from uuid import uuid4

import pytest
from signal_core.internal_linking import (
    RECIPE,
    assert_internal_link_diff,
    build_link_graph,
    link_opportunities,
    make_internal_link_patch,
)
from signal_core.recipe_autonomy import AUTONOMY_RECIPES, attest_recipe_autonomy
from signal_core.recipe_releases import RecipeReleaseUnavailable
from signal_core.structured_data_recipe import assert_structured_build
from signal_core.technical_seo_recipes import (
    TechnicalRecipeUnavailable,
    technical_recipe_release_manifest,
)
from test_technical_seo_recipes import _case

ORIGIN = "https://example.test"
PARAGRAPH = (
    "Garden irrigation systems reduce water waste while helping plants thrive in dry weather."
)


def page(path, links=()):
    return {
        "id": str(uuid4()),
        "url": ORIGIN + path,
        "title": "Garden irrigation systems",
        "headings": [{"level": 1, "text": "Garden irrigation"}],
        "internal_links": list(links),
    }


def case(html=None, *, path="index.html"):
    html = (
        html
        or "<html><head><title>Garden irrigation</title></head><body><main><p>"
        + PARAGRAPH
        + "</p></main></body></html>"
    )
    extension, checkout, evidence = _case(html, "links.internal.add", path=path)
    evidence["page_id"] = str(uuid4())
    evidence["page_url"] = ORIGIN + ("/" if path == "index.html" else "/" + path)
    source, target = page("/"), page("/water.html")
    source.update(id=evidence["page_id"], url=evidence["page_url"])
    pages = [source, target]
    if path != "index.html":
        pages.append(page("/", [source["url"]]))
    opportunity = next(
        p for p in link_opportunities(pages, ORIGIN) if p["source_id"] == source["id"]
    )
    return dict(
        evidence=evidence,
        extension=extension,
        checkout=checkout,
        opportunity=opportunity,
        source_path=path,
    )


def test_graph_distinct_incoming_weak_orphan_offsite_truncated_and_deterministic():
    a, b, c, d = (
        page("/", ["/b", "/b#one", "https://other.invalid/b"]),
        page("/b"),
        page("/c"),
        page("/d"),
    )
    d["output_truncated"] = True
    graph = build_link_graph([a, b, c, d], ORIGIN)
    assert graph["incoming"] == {ORIGIN + "/": 0, ORIGIN + "/b": 1, ORIGIN + "/c": 0}
    assert graph["edges"] == ((ORIGIN + "/", ORIGIN + "/b"),)
    first = link_opportunities([a, b, c, d], ORIGIN)
    assert first == link_opportunities([d, c, b, a], ORIGIN)
    assert any(p["status"] == "no_incoming_observed" for p in first)
    extra = [page("/one", ["/c"]), page("/two", ["/c"])]
    for p in extra:
        p.update(title="Astronomy", headings=[])
    assert any(
        p["status"] == "weakly_linked" for p in link_opportunities([a, b, c, *extra], ORIGIN)
    )
    assert all(len(p["shared_terms"]) >= 2 and not p["autonomy_eligible"] for p in first)
    assert not any(
        p["source_url"] == ORIGIN + "/" and p["target_url"] == ORIGIN + "/b" for p in first
    )


@pytest.mark.parametrize("cap", [1, 2, 3])
def test_page_caps_and_unrelated_pages(cap):
    pages = [page("/"), *(page(f"/{n}.html") for n in range(8))]
    counts = Counter(p["source_id"] for p in link_opportunities(pages, ORIGIN, page_cap=cap))
    assert max(counts.values()) == cap
    pages[0]["title"], pages[0]["headings"] = "Unrelated astronomy", []
    assert not any(p["source_id"] == pages[0]["id"] for p in link_opportunities(pages, ORIGIN))


@pytest.mark.parametrize("cap", [0, 4, True])
def test_invalid_caps(cap):
    with pytest.raises(ValueError):
        link_opportunities([], ORIGIN, page_cap=cap)


@pytest.mark.parametrize("path", ["index.html", "guides/water.html"])
def test_exact_existing_anchor_in_relevant_paragraph(path):
    args = case(path=path)
    patch = make_internal_link_patch(**args)
    assert patch.path == path and patch.before_fragment in PARAGRAPH
    assert patch.after.count(b"<a href=") == 1
    assert_internal_link_diff(
        patch.before,
        patch.after,
        start=patch.offset,
        anchor=patch.before_fragment,
        target=args["opportunity"]["target_url"],
    )
    assert (
        patch.after.replace(patch.after_fragment.encode(), patch.before_fragment.encode(), 1)
        == patch.before
    )
    with pytest.raises(TechnicalRecipeUnavailable):
        assert_internal_link_diff(
            patch.before,
            patch.after + b"other",
            start=patch.offset,
            anchor=patch.before_fragment,
            target=args["opportunity"]["target_url"],
        )


@pytest.mark.parametrize(
    "container",
    [
        "nav",
        "footer",
        "header",
        "aside",
        "div class='boilerplate'",
        "div role='navigation'",
        "div hidden",
        "div style='display:none'",
    ],
)
def test_never_insert_navigation_footer_boilerplate_or_hidden(container):
    tag = container.split()[0]
    args = case(
        f"<html><head></head><body><main><{container}><p>{PARAGRAPH}</p></{tag}></main></body></html>"
    )
    with pytest.raises(TechnicalRecipeUnavailable, match="ANCHOR_UNAVAILABLE"):
        make_internal_link_patch(**args)


@pytest.mark.parametrize(
    "target",
    [
        "https://other.invalid/water",
        "https://example.test.evil.invalid/water",
        "http://example.test/water",
        "https://example.test/water?q=secret",
        "https://example.test/water#anchor",
    ],
)
def test_offsite_unverified_and_ambiguous_targets_refused(target):
    args = case()
    args["opportunity"]["target_url"] = target
    with pytest.raises(TechnicalRecipeUnavailable, match="TARGET_REFUSED"):
        make_internal_link_patch(**args)


def test_duplicate_link_and_anchor_reuse_and_stuffing_refused():
    args = case()
    args["opportunity"]["target_url"] = ORIGIN + "/water.html"
    html = (
        '<html><head></head><body><a href="/water.html#old">Old</a>'
        f"<main><p>{PARAGRAPH}</p></main></body></html>"
    )
    with pytest.raises(TechnicalRecipeUnavailable, match="DUPLICATE"):
        make_internal_link_patch(**case(html))
    patch = make_internal_link_patch(**args)
    with pytest.raises(TechnicalRecipeUnavailable, match="ANCHOR_UNAVAILABLE"):
        make_internal_link_patch(
            **args,
            used_anchors=[patch.before_fragment.upper()],
            selected_anchor=patch.before_fragment,
        )
    stuffing = "Garden garden garden garden garden garden garden garden."
    with pytest.raises(TechnicalRecipeUnavailable, match="ANCHOR_UNAVAILABLE"):
        make_internal_link_patch(
            **case(f"<html><head></head><body><main><p>{stuffing}</p></main></body></html>")
        )


@pytest.mark.parametrize(
    "failure", ["source", "malformed", "nested", "duplicate_attrs", "template"]
)
def test_failure_paths(failure):
    args = case()
    if failure == "source":
        args["evidence"]["page_body_sha256"] = "0" * 64
    else:
        html = {
            "malformed": f"<main><p>{PARAGRAPH}</main>",
            "nested": f"<main><p><em>{PARAGRAPH}</em></p></main>",
            "duplicate_attrs": f"<main><p id='x' id='y'>{PARAGRAPH}</p></main>",
            "template": f"<main><p>{{{{ data }}}} {PARAGRAPH}</p></main>",
        }[failure]
        args = case(html)
    with pytest.raises(TechnicalRecipeUnavailable):
        make_internal_link_patch(**args)


def test_reviewed_manifest_is_never_autonomy_eligible_and_five_keys_unchanged():
    manifest = technical_recipe_release_manifest(RECIPE, uuid4())
    assert manifest["approval_class"] == "owner_review" and manifest["autonomy_eligible"] is False
    assert manifest["max_new_links_per_page_per_cycle"] == 3
    assert len(AUTONOMY_RECIPES) == 5 and RECIPE not in AUTONOMY_RECIPES
    with pytest.raises(RecipeReleaseUnavailable):
        attest_recipe_autonomy(
            None,
            release_id=uuid4(),
            recipe_key=RECIPE,
            actor_user_id=uuid4(),
            attestation_id=uuid4(),
        )


@pytest.mark.parametrize("failure", [None, "extra", "missing", "wrong", "failed"])
def test_all_build_artifacts_exactly_one_wrapper_only(failure):
    from types import SimpleNamespace

    patch = make_internal_link_patch(**case())

    def artifact(body):
        return ("_site/index.html", hashlib.sha256(body).hexdigest(), len(body))

    baseline = SimpleNamespace(
        exit_class="passed", artifacts=(artifact(patch.before), ("_site/style.css", "a" * 64, 1))
    )
    candidate = SimpleNamespace(
        exit_class="passed", artifacts=(artifact(patch.after), baseline.artifacts[1])
    )
    if failure == "extra":
        candidate.artifacts += (("_site/extra.html", "b" * 64, 2),)
    if failure == "missing":
        candidate.artifacts = candidate.artifacts[:1]
    if failure == "wrong":
        candidate.artifacts = (artifact(patch.after + b"other"), baseline.artifacts[1])
    if failure == "failed":
        candidate.exit_class = "crash"
    if failure:
        with pytest.raises(TechnicalRecipeUnavailable):
            assert_structured_build(patch, baseline, candidate, output_path="_site/index.html")
    else:
        assert_structured_build(patch, baseline, candidate, output_path="_site/index.html")


def test_repeated_main_paragraph_is_boilerplate_and_existing_cross_page_anchor_is_refused():
    from dataclasses import replace

    args = case()
    other = ("other.html", dict(args["checkout"].files)["index.html"])
    args["checkout"] = replace(args["checkout"], files=(*args["checkout"].files, other))
    with pytest.raises(TechnicalRecipeUnavailable, match="ANCHOR_UNAVAILABLE"):
        make_internal_link_patch(**args)
    args = case()
    anchor = make_internal_link_patch(**args).before_fragment
    args["checkout"] = replace(
        args["checkout"],
        files=(*args["checkout"].files, ("other.html", f'<a href="/other">{anchor}</a>'.encode())),
    )
    with pytest.raises(TechnicalRecipeUnavailable, match="ANCHOR_UNAVAILABLE"):
        make_internal_link_patch(**args, selected_anchor=anchor)


@pytest.mark.parametrize("negative", [None, "autonomy", "offsite", "nav", "other_change"])
def test_dispatch_reconstructs_exact_static_patch_and_live_requires_exact_bytes(negative):
    from dataclasses import replace
    from datetime import UTC, datetime

    import rfc8785
    from signal_core.candidate_build import plan_candidate_build
    from signal_core.github_pr_patch import (
        GitHubPrPatchRejected,
        git_tree_sha,
        plan_github_pr_patch,
    )
    from signal_core.live_verification import sealed_live_postcondition, verify_live_html

    args = case()
    patch = make_internal_link_patch(**args)
    extension, checkout = args["extension"], args["checkout"]
    tree = git_tree_sha(dict(checkout.files))
    checkout = replace(checkout, inventory=replace(checkout.inventory, tree_sha=tree))
    extension = replace(extension, tree_sha=tree)
    plan = plan_candidate_build(
        extension, checkout, patch=patch.patch, approved_paths=frozenset({patch.path})
    )
    manifest = dict(
        schema_version=1,
        finding_id=str(uuid4()),
        site_id=str(extension.site_id),
        extension_id=str(extension.id),
        approval_class="owner_review",
        base_sha=extension.base_sha,
        source_path=patch.path,
        source_sha256=hashlib.sha256(patch.before).hexdigest(),
        result_sha256=hashlib.sha256(patch.after).hexdigest(),
        patch_sha256=plan.patch_sha256,
        patch=dict(offset=patch.offset, before=patch.before_fragment, after=patch.after_fragment),
        evidence=args["evidence"] | {"finding": {"key": "links.internal.add"}},
        internal_link=dict(
            recipe_key=RECIPE,
            autonomy_eligible=False,
            target_url=args["opportunity"]["target_url"],
            graph=args["opportunity"],
        ),
        build_receipt=dict(exit_class="passed", artifacts=[dict(path="_site/index.html")]),
        recovery_plan="Revert only this link wrapper.",
    )
    if negative == "autonomy":
        manifest["internal_link"]["autonomy_eligible"] = True
    elif negative == "offsite":
        manifest["internal_link"]["graph"]["target_url"] = "https://other.example.invalid/"
    elif negative == "nav":
        content = patch.before.replace(b"<main>", b"<nav>").replace(b"</main>", b"</nav>")
        checkout = replace(
            checkout,
            files=tuple((p, content if p == "index.html" else b) for p, b in checkout.files),
        )
    elif negative == "other_change":
        manifest["patch"]["after"] += "Other text"
    canonical = rfc8785.dumps(manifest)
    call = dict(
        manifest_bytes=canonical,
        revision_sha256=hashlib.sha256(canonical).hexdigest(),
        operation_id=uuid4(),
        created_at=datetime.now(UTC),
        extension=extension,
        checkout=checkout,
    )
    if negative:
        with pytest.raises(GitHubPrPatchRejected):
            plan_github_pr_patch(**call)
    else:
        assert plan_github_pr_patch(**call).content == patch.after
        from signal_core.github_pr_delivery import _recipe_key

        assert _recipe_key(manifest) == RECIPE
        live = sealed_live_postcondition(canonical, call["revision_sha256"])
        assert (
            verify_live_html(
                live, body=patch.after, http_status=200, final_url=ORIGIN + "/"
            ).outcome
            == "verified"
        )
        assert (
            verify_live_html(
                live, body=patch.after + b"other", http_status=200, final_url=ORIGIN + "/"
            ).outcome
            != "verified"
        )
