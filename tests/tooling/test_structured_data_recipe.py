import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from signal_core.business_brain import BusinessFact, FactProvenance
from signal_core.structured_data_recipe import (
    RECIPE_KEY,
    assert_structured_build,
    build_grounded_block,
    make_structured_data_patch,
    site_url,
    static_page_url,
    structured_data_release_manifest,
)
from signal_core.technical_seo_recipes import TechnicalRecipeUnavailable

from tests.tooling.test_technical_seo_recipes import _case

ORIGIN = "https://example.test"


@pytest.mark.parametrize("drift", [None, "key", "hash", "contract", "authority"])
def test_dispatch_binds_grounded_recipe_not_provenance_finding(monkeypatch, drift):
    from signal_core import github_pr_delivery as delivery

    release_id = uuid4()
    manifest = {
        "approval_class": "owner_review",
        "evidence": {"finding": {"key": "metadata.title.missing"}},
        "structured_data": {"recipe_key": RECIPE_KEY, "autonomy_eligible": False},
    }
    release = SimpleNamespace(
        version="1.0.0",
        content_hash="a" * 64,
        manifest=structured_data_release_manifest(release_id),
    )
    if drift == "key":
        manifest["structured_data"]["recipe_key"] = "technical_title"
    elif drift == "hash":
        release.content_hash = "b" * 64
    elif drift == "contract":
        release.manifest["approval_class"] = "A2"
    elif drift == "authority":
        manifest["structured_data"]["autonomy_eligible"] = True

    def read(connection, *, recipe_key, release_id):
        assert recipe_key == RECIPE_KEY
        return release

    monkeypatch.setattr(delivery, "get_reviewed_recipe_release", read)
    item = SimpleNamespace(recipe_release_id=release_id, release_content_hash="a" * 64)
    if drift:
        with pytest.raises(delivery.GitHubPrDeliveryUnavailable):
            delivery._validate_technical_release(None, item, manifest)
    else:
        delivery._validate_technical_release(None, item, manifest)


HTML = (
    "<html><head><title>Signal Guide</title></head><body><h1>A guide</h1>"
    "<p>What is Signal?</p><p>A useful guide.</p><time>2026-10-01</time>"
    '<a href="/">Home</a><a href="/guide.html">Guide</a></body></html>'
)


def build(proposal, *, source=HTML, facts=()):
    return build_grounded_block(
        proposal, source=source, page_url=ORIGIN + "/", origin=ORIGIN, facts=facts
    )


def fact(statement, status="approved"):
    return BusinessFact(
        uuid4(),
        "product_claim",
        statement,
        status,
        FactProvenance("owner_statement"),
        True,
        None,
        datetime.now(UTC),
    )


def test_faq_exact_visible_question_and_answer_and_unsupported_omitted():
    value = build(
        {
            "@type": "FAQPage",
            "questions": [{"question": "What is Signal?", "answer": "A useful guide."}],
            "invented": "not used",
        }
    )
    assert value.document["mainEntity"][0]["acceptedAnswer"]["text"] == "A useful guide."
    assert "invented" not in value.document


@pytest.mark.parametrize("answer", ["Useful guide.", "A useful", " A useful guide.", "Made up."])
def test_faq_refuses_paraphrases_substrings_or_whitespace_changes(answer):
    with pytest.raises(TechnicalRecipeUnavailable):
        build(
            {"@type": "FAQPage", "questions": [{"question": "What is Signal?", "answer": answer}]}
        )


@pytest.mark.parametrize(
    "hidden",
    [
        "<p hidden>A useful guide.</p>",
        '<p aria-hidden="true">A useful guide.</p>',
        "<template>A useful guide.</template>",
    ],
)
def test_faq_does_not_use_hidden_text(hidden):
    with pytest.raises(TechnicalRecipeUnavailable):
        build(
            {
                "@type": "FAQPage",
                "questions": [{"question": "What is Signal?", "answer": "A useful guide."}],
            },
            source=HTML.replace("<p>A useful guide.</p>", hidden),
        )


@pytest.mark.parametrize(
    "uncertain",
    [
        '<link rel="StyleSheet" href="/site.css">',
        '<svg><text display="none">A useful guide.</text></svg>',
        "<canvas>A useful guide.</canvas>",
        "<select><option>A useful guide.</option></select>",
        "<div popover>A useful guide.</div>",
    ],
)
def test_visibility_requires_render_evidence_for_nonplain_or_conditional_html(uncertain):
    with pytest.raises(TechnicalRecipeUnavailable, match="RENDER_EVIDENCE_REQUIRED"):
        build({"@type": "Article"}, source=HTML.replace("</body>", uncertain + "</body>"))


@pytest.mark.parametrize("kind", ["Article", "BlogPosting"])
def test_article_headline_and_dates_from_page_only(kind):
    value = build({"@type": kind, "datePublished": "2026-10-01", "author": "invented"})
    assert value.document["headline"] == "Signal Guide"
    assert value.document["datePublished"] == "2026-10-01"
    assert "author" not in value.document and "dateModified" not in value.document
    assert build({"@type": kind, "headline": "A guide"}).document["headline"] == "A guide"
    for proposal in (
        {"headline": "Not the title"},
        {"dateModified": "2025-10-01"},
        {"datePublished": "2026-99-01"},
    ):
        with pytest.raises(TechnicalRecipeUnavailable):
            build({"@type": kind, **proposal})


@pytest.mark.parametrize("kind", ["Organization", "Product"])
def test_business_values_require_approved_refs_and_owner_review(kind):
    name, description, url = fact("Signal"), fact("Approved product claim."), fact(ORIGIN + "/")
    result = build(
        {
            "@type": kind,
            "fact_refs": {
                "name": str(name.fact_id),
                "description": str(description.fact_id),
                "url": str(url.fact_id),
            },
            "offers": {"price": 1},
            "aggregateRating": {"ratingValue": 5},
        },
        facts=(name, description, url),
    )
    assert result.owner_required is True
    assert result.document == {
        "@context": "https://schema.org",
        "@type": kind,
        "name": name.statement,
        "description": description.statement,
        "url": url.statement,
    }
    with pytest.raises(TechnicalRecipeUnavailable):
        build({"@type": kind, "fact_refs": {"name": str(name.fact_id)}})
    unapproved = fact("Signal", "proposed")
    with pytest.raises(TechnicalRecipeUnavailable):
        build({"@type": kind, "fact_refs": {"name": str(unapproved.fact_id)}}, facts=(unapproved,))
    assert (
        "url"
        not in build(
            {"@type": kind, "fact_refs": {"name": str(name.fact_id)}}, facts=(name,)
        ).document
    )


def test_breadcrumb_requires_exact_visible_label_and_link():
    result = build(
        {
            "@type": "BreadcrumbList",
            "items": [
                {"name": "Home", "item": ORIGIN + "/"},
                {"name": "Guide", "item": ORIGIN + "/guide.html"},
            ],
        }
    )
    assert [item["position"] for item in result.document["itemListElement"]] == [1, 2]
    with pytest.raises(TechnicalRecipeUnavailable):
        build({"@type": "BreadcrumbList", "items": [{"name": "Missing", "item": ORIGIN + "/"}]})


@pytest.mark.parametrize(
    "url",
    [
        "https://offsite.test/",
        "http://example.test/",
        "https://user@example.test/",
        "https://example.test/?next=foo",
        "https://example.test/#foo",
        "javascript:alert(1)",
    ],
)
def test_off_site_or_ambiguous_urls_refused(url):
    with pytest.raises(TechnicalRecipeUnavailable):
        site_url(url, ORIGIN)


@pytest.mark.parametrize(
    "extra",
    [
        "<style>p{display:none}</style>",
        "<script>document.body.remove()</script>",
        '<p style="display:none">text</p>',
        '<a onclick="alert(1)">text</a>',
        "<details>text</details>",
    ],
)
def test_visibility_uncertainty_requires_rendered_evidence(extra):
    with pytest.raises(TechnicalRecipeUnavailable):
        build({"@type": "Article"}, source=HTML.replace("</body>", extra + "</body>"))


@pytest.mark.parametrize("existing", ["", '<script type="application/ld+json">{broken}</script>'])
def test_one_exact_block_patch_and_built_output_assertion(existing):
    source = HTML.replace("</head>", existing + "</head>")
    extension, checkout, evidence = _case(source, "metadata.title.missing")
    patch, block = make_structured_data_patch(
        evidence=evidence, extension=extension, checkout=checkout, proposal={"@type": "Article"}
    )
    assert patch.after.count(b"application/ld+json") == 1
    assert (
        patch.before[: patch.offset]
        + patch.after_fragment.encode()
        + patch.before[patch.offset + len(patch.before_fragment.encode()) :]
        == patch.after
    )
    assert json.loads(patch.after_fragment.split(">", 1)[1].split("</script>")[0]) == block.document

    def receipt(body, extra="b" * 64):
        return SimpleNamespace(
            exit_class="passed",
            artifacts=(
                ("_site/index.html", hashlib.sha256(body).hexdigest(), len(body)),
                ("_site/asset.css", extra, 10),
            ),
        )

    assert_structured_build(
        patch, receipt(patch.before), receipt(patch.after), output_path="_site/index.html"
    )
    for bad in (receipt(patch.before), receipt(patch.after, "c" * 64)):
        with pytest.raises(TechnicalRecipeUnavailable):
            assert_structured_build(
                patch, receipt(patch.before), bad, output_path="_site/index.html"
            )


def test_release_is_owner_only_and_not_in_closed_a2_shape():
    from signal_core.recipe_autonomy import AUTONOMY_RECIPES

    assert len(AUTONOMY_RECIPES) == 5
    assert RECIPE_KEY not in AUTONOMY_RECIPES
    manifest = structured_data_release_manifest(uuid4())
    assert (
        manifest["approval_class"] == "owner_review" and manifest["max_resources_per_revision"] == 1
    )


@pytest.mark.parametrize(
    "path,route",
    [("index.html", "/"), ("guides/index.html", "/guides/"), ("myindex.html", "/myindex.html")],
)
def test_static_routes_do_not_confuse_index_suffix(path, route):
    assert static_page_url(path, ORIGIN) == ORIGIN + route


@pytest.mark.parametrize("kind", ["article", "long_article", "long_faq"])
def test_nested_live_verification_requires_exact_json_ld_bytes_and_nothing_else(kind):
    import rfc8785
    from signal_core.live_verification import sealed_live_postcondition, verify_live_html

    source = HTML.replace("Signal Guide", "x" * 1000) if kind == "long_article" else HTML
    proposal = {"@type": "Article"}
    if kind == "long_faq":
        source = source.replace("What is Signal?", "q" * 1000).replace(
            "A useful guide.", "a" * 1000
        )
        proposal = {
            "@type": "FAQPage",
            "questions": [{"question": "q" * 1000, "answer": "a" * 1000}],
        }
    extension, checkout, evidence = _case(
        source, "metadata.title.missing", path="guides/index.html"
    )
    evidence["page_url"] = ORIGIN + "/guides/"
    patch, block = make_structured_data_patch(
        evidence=evidence,
        extension=extension,
        checkout=checkout,
        proposal=proposal,
        source_path="guides/index.html",
    )
    manifest = {
        "schema_version": 1,
        "source_path": patch.path,
        "source_sha256": hashlib.sha256(patch.before).hexdigest(),
        "result_sha256": hashlib.sha256(patch.after).hexdigest(),
        "evidence": {
            "page_url": evidence["page_url"],
            "site_origin": ORIGIN,
            "finding": {"key": "metadata.title.missing"},
        },
        "patch": {"before": patch.before_fragment, "after": patch.after_fragment},
        "recovery_plan": patch.recovery_plan,
        "structured_data": {
            "recipe_key": RECIPE_KEY,
            "json_ld": block.document,
            "autonomy_eligible": False,
        },
    }
    canonical = rfc8785.dumps(manifest)
    contract = sealed_live_postcondition(canonical, hashlib.sha256(canonical).hexdigest())
    assert (
        verify_live_html(
            contract, body=patch.after, http_status=200, final_url=evidence["page_url"]
        ).outcome
        == "verified"
    )
    for altered in (
        patch.after + b"<!-- changed -->",
        patch.after.replace(b'"@type":', b'"@type": '),
    ):
        assert (
            verify_live_html(
                contract, body=altered, http_status=200, final_url=evidence["page_url"]
            ).outcome
            == "inconclusive"
        )
    from signal_core.live_verification import LiveVerificationUnavailable

    for malformed in ("not a packet", {"recipe_key": RECIPE_KEY, "autonomy_eligible": True}):
        manifest["structured_data"] = malformed
        canonical = rfc8785.dumps(manifest)
        with pytest.raises(LiveVerificationUnavailable):
            sealed_live_postcondition(canonical, hashlib.sha256(canonical).hexdigest())
    manifest["structured_data"] = {"recipe_key": RECIPE_KEY, "autonomy_eligible": False}
    manifest["source_path"] = "../index.html"
    canonical = rfc8785.dumps(manifest)
    with pytest.raises(LiveVerificationUnavailable):
        sealed_live_postcondition(canonical, hashlib.sha256(canonical).hexdigest())
