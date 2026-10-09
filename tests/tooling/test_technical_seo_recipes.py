import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from signal_core.github_app import (
    GitHubRepositoryCheckout,
    GitHubRepositoryInventory,
    GitHubRepositorySnapshot,
    GitHubTreeEntry,
)
from signal_core.github_pr_extension import GitHubPrExtension
from signal_core.technical_seo_recipes import (
    TechnicalRecipeUnavailable,
    make_technical_recipe_patch,
    technical_recipe_release_manifest,
)


def _sha(content):
    return hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()


def _case(
    html,
    key,
    *,
    title=None,
    description=None,
    canonical=None,
    images=None,
    links=None,
    errors=0,
    path="index.html",
    framework="eleventy",
):
    body = html.encode()
    files = {
        ".eleventy.js": b"module.exports = {};\n",
        "package.json": json.dumps({"scripts": {"build": "node build.js"}}).encode(),
        "build.js": b"require('fs').mkdirSync('_site');\n",
        path: body,
    }
    snapshot = GitHubRepositorySnapshot(
        123,
        245,
        "SignalOwner",
        "website",
        "SignalOwner/website",
        True,
        "main",
        "main",
        "a" * 40,
        True,
        path,
        datetime.now(UTC),
    )
    entries = tuple(
        GitHubTreeEntry(name, "100644", "blob", _sha(content))
        for name, content in sorted(files.items())
    )
    checkout = GitHubRepositoryCheckout(
        GitHubRepositoryInventory(snapshot, "b" * 40, entries, False),
        tuple(sorted(files.items())),
    )
    extension = GitHubPrExtension(
        uuid4(),
        uuid4(),
        uuid4(),
        "observed",
        repository_id=245,
        base_sha="a" * 40,
        tree_sha="b" * 40,
        framework=framework,
        content_format="html",
        coverage="complete",
        content_sha=_sha(body),
    )
    evidence = {
        "finding": {"key": key, "resource_locator": "https://example.test/missing"},
        "site_origin": "https://example.test",
        "page_url": "https://example.test/",
        "page_body_sha256": hashlib.sha256(body).hexdigest(),
        "page_title": title,
        "page_description": description,
        "page_canonical": canonical,
        "page_headings": [{"level": 1, "text": "Signal Guide"}],
        "missing_alt_images": images or [],
        "page_internal_links": links or [],
        "page_parse_error_count": errors,
    }
    return extension, checkout, evidence


@pytest.mark.parametrize(
    ("recipe", "key", "html", "kwargs", "draft", "expected"),
    [
        (
            "technical_title",
            "metadata.title.missing",
            "<head></head><h1>Signal Guide</h1>",
            {},
            "Signal Guide",
            "<title>Signal Guide</title>",
        ),
        (
            "technical_title",
            "metadata.title.duplicate",
            "<head><title>Signal Guide</title></head><h1>Signal Guide</h1>",
            {"title": "Signal Guide"},
            "Guide Signal",
            "<title>Guide Signal</title>",
        ),
        (
            "technical_description",
            "metadata.meta_description.missing",
            "<head><title>Signal Guide</title></head><h1>Signal Guide</h1>",
            {"title": "Signal Guide"},
            "Signal Guide: view more about Signal Guide and learn more about the "
            "Signal Guide page.",
            'name="description"',
        ),
        (
            "technical_description",
            "metadata.meta_description.duplicate",
            '<head><meta name="description" content="Signal Guide"></head><h1>Signal Guide</h1>',
            {"description": "Signal Guide"},
            "Signal Guide: view more about Signal Guide and learn more about the "
            "Signal Guide page.",
            "Signal Guide page",
        ),
        (
            "technical_alt",
            "images.alt.missing",
            '<head></head><h1>Signal Guide</h1><img src="guide.png">',
            {"images": ["guide.png"]},
            "Signal Guide image",
            'alt="Signal Guide image"',
        ),
        (
            "technical_canonical",
            "canonical.missing",
            "<head></head><h1>Signal Guide</h1>",
            {},
            None,
            '<link rel="canonical" href="https://example.test/">',
        ),
        (
            "technical_structured_data",
            "structured_data.invalid_json_ld",
            '<head><script type="application/ld+json">{bad}</script></head><h1>Signal Guide</h1>',
            {"errors": 1},
            None,
            '"@type":"WebPage"',
        ),
        (
            "technical_broken_link",
            "links.internal.not_found",
            '<head></head><h1>Signal Guide</h1><a href="/missing">old</a>',
            {"links": ["https://example.test/missing"]},
            None,
            "<a>old</a>",
        ),
    ],
)
def test_one_narrow_patch_per_committed_finding(recipe, key, html, kwargs, draft, expected):
    extension, checkout, evidence = _case(html, key, **kwargs)
    patch = make_technical_recipe_patch(
        recipe_key=recipe,
        evidence=evidence,
        extension=extension,
        checkout=checkout,
        drafted_text=draft,
    )
    assert expected.encode() in patch.after
    assert patch.before != patch.after
    assert patch.path == "index.html"
    assert patch.after == (
        patch.before[: patch.offset]
        + patch.after_fragment.encode()
        + patch.before[patch.offset + len(patch.before_fragment.encode()) :]
    )


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ("unsupported", "RECIPE_FORMAT_UNAVAILABLE"),
        ("protected", "RECIPE_FORMAT_UNAVAILABLE"),
        ("drift", "RECIPE_SOURCE_DRIFT"),
        ("claim", "RECIPE_OWNER_CLAIM_REVIEW_REQUIRED"),
        ("model", "RECIPE_MODEL_UNAVAILABLE"),
        ("finding", "RECIPE_FINDING_UNAVAILABLE"),
    ],
)
def test_unsafe_or_unavailable_recipe_never_produces_patch(change, code):
    extension, checkout, evidence = _case(
        "<head></head><h1>Signal Guide</h1>", "metadata.title.missing"
    )
    draft = "Signal Guide"
    if change == "unsupported":
        extension = GitHubPrExtension(**{**extension.__dict__, "framework": "nextjs"})
    elif change == "protected":
        extension, checkout, evidence = _case(
            "<head></head><h1>Signal Guide</h1>",
            "metadata.title.missing",
            path=".github/workflows/ci.yml",
        )
    elif change == "drift":
        evidence["page_body_sha256"] = "0" * 64
    elif change == "claim":
        draft = "Guaranteed rankings"
    elif change == "model":
        draft = None
    elif change == "finding":
        evidence["finding"]["key"] = "robots.blocked"
    with pytest.raises(TechnicalRecipeUnavailable, match=code):
        make_technical_recipe_patch(
            recipe_key="technical_title",
            evidence=evidence,
            extension=extension,
            checkout=checkout,
            drafted_text=draft,
        )


def test_release_contract_is_closed_and_reviewable():
    release_id = uuid4()
    manifest = technical_recipe_release_manifest("technical_alt", release_id)
    assert manifest["max_resources_per_revision"] == 1
    assert manifest["approval_class"] == "owner_review"
    assert manifest["allowed_fields"] == ["image_alt"]
    assert manifest["release_id"] == str(release_id)
