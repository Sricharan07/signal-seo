import hashlib
from dataclasses import replace

import pytest
import rfc8785
from signal_core.live_verification import (
    LivePostcondition,
    LiveVerificationUnavailable,
    sealed_live_postcondition,
    verify_live_html,
)

PAGE = "https://site.example/"
SOURCE = b"<html><head></head><body></body></html>"


@pytest.mark.parametrize(
    "finding,before,after,page",
    [
        (
            "metadata.title.missing",
            "",
            "<title>Evidence title</title>",
            "<title>Evidence title</title>",
        ),
        (
            "metadata.title.duplicate",
            "Old title",
            "Evidence title",
            "<title>Evidence title</title>",
        ),
        (
            "metadata.meta_description.missing",
            "",
            '<meta name="description" content="Evidence description">',
            '<meta name="description" content="Evidence description">',
        ),
        (
            "metadata.meta_description.duplicate",
            '<meta name="description" content="Old description">',
            '<meta name="description" content="Evidence description">',
            '<meta name="description" content="Evidence description">',
        ),
        (
            "images.alt.missing",
            '<img src="guide.png">',
            '<img src="guide.png" alt="Evidence image">',
            '<img src="guide.png" alt="Evidence image">',
        ),
        (
            "canonical.missing",
            "",
            '<link rel="canonical" href="https://site.example/">',
            '<link rel="canonical" href="https://site.example/">',
        ),
        (
            "structured_data.invalid_json_ld",
            "broken",
            '{"@context":"https://schema.org","@type":"WebPage"}',
            '<script type="application/ld+json">{"@context":"https://schema.org","@type":"WebPage"}</script>',
        ),
        ("links.internal.not_found", '<a href="/missing">', "<a>", "<a>Evidence link</a>"),
    ],
)
def test_each_recipe_verifies_only_its_exact_sealed_page(finding, before, after, page):
    result = ("<html><head>" + page + "</head><body></body></html>").encode()
    manifest = {
        "schema_version": 1,
        "source_path": "index.html",
        "source_sha256": hashlib.sha256(SOURCE).hexdigest(),
        "result_sha256": hashlib.sha256(result).hexdigest(),
        "evidence": {
            "page_url": PAGE,
            "site_origin": PAGE.rstrip("/"),
            "finding": {"key": finding},
        },
        "patch": {"before": before, "after": after},
        "recovery_plan": "Revert this exact patch with conflict review.",
    }
    canonical = rfc8785.dumps(manifest)
    contract = sealed_live_postcondition(canonical, hashlib.sha256(canonical).hexdigest())
    verified = verify_live_html(contract, body=result, http_status=200, final_url=PAGE)
    assert verified.outcome == "verified" and verified.matched
    assert all(item["matched"] for item in verified.postconditions)
    assert verified.fetched_sha256 == manifest["result_sha256"]
    assert (
        verify_live_html(contract, body=SOURCE, http_status=200, final_url=PAGE).reason
        == "EC_077_STALE_PAGE"
    )
    assert (
        verify_live_html(
            contract, body=result + b"<!-- CDN alteration -->", http_status=200, final_url=PAGE
        ).outcome
        == "inconclusive"
    )


def title_contract():
    return LivePostcondition(
        "technical_title",
        PAGE,
        hashlib.sha256(SOURCE).hexdigest(),
        hashlib.sha256(b"<title>Exact</title>").hexdigest(),
        {"title": "Exact"},
        "Revert exact patch.",
    )


@pytest.mark.parametrize(
    "body,reason,outcome",
    [
        (b"<title>Other</title>", "EC_123_POSTCONDITION_MISMATCH", "inconclusive"),
        (
            b'<title>Exact</title><meta name="robots" content="noindex">',
            "LIVE_NOINDEX",
            "regressed",
        ),
        (
            b'<title>Exact</title><link rel="canonical" href="https://other.example/">',
            "LIVE_CANONICAL_DRIFT",
            "regressed",
        ),
        (b'<meta name="description" name="robots">', "LIVE_HTML_UNAVAILABLE", "inconclusive"),
        (b"\xff", "LIVE_HTML_UNAVAILABLE", "inconclusive"),
        (b"x" * (128 * 1024 + 1), "LIVE_HTML_UNAVAILABLE", "inconclusive"),
    ],
)
def test_changed_absent_ambiguous_and_harmful_content_is_never_verified(body, reason, outcome):
    result = verify_live_html(title_contract(), body=body, http_status=200, final_url=PAGE)
    assert (result.outcome, result.reason) == (outcome, reason)


@pytest.mark.parametrize(
    "status,outcome", [(404, "regressed"), (500, "regressed"), (302, "inconclusive")]
)
def test_http_failure_is_explicit(status, outcome):
    assert (
        verify_live_html(title_contract(), body=b"", http_status=status, final_url=PAGE).outcome
        == outcome
    )


def test_redirect_and_unsealed_contract_rejected():
    assert (
        verify_live_html(
            title_contract(),
            body=b"<title>Exact</title>",
            http_status=200,
            final_url="https://other.example/",
        ).outcome
        == "inconclusive"
    )
    with pytest.raises(LiveVerificationUnavailable):
        sealed_live_postcondition(b"{}", "a" * 64)
    empty = replace(title_contract(), expected={})
    assert (
        verify_live_html(
            empty, body=b"<title>Exact</title>", http_status=200, final_url=PAGE
        ).outcome
        != "verified"
    )


@pytest.mark.parametrize(
    "surround,verified",
    [
        ("<article>{}</article>", True),
        ("<div hidden>{}</div>", False),
        ('<div aria-hidden="true">{}</div>', False),
        ('<div style="display: none">{}</div>', False),
        ("<script>{}</script>", False),
        ("<template>{}</template>", False),
        ('<meta name="robots" content="noindex"><article>{}</article>', False),
    ],
)
def test_wordpress_public_get_requires_visible_sealed_article(surround, verified):
    fragment = "<h2>Audience</h2>\n<p>Founders use drafts &amp; reviews.</p>"
    contract = replace(
        title_contract(), recipe_key="wordpress_article", expected={"article_fragment": fragment}
    )
    body = ("<html><body>" + surround.format(fragment) + "</body></html>").encode()
    result = verify_live_html(contract, body=body, http_status=200, final_url=PAGE)
    assert (result.outcome == "verified") is verified
    changed = body.replace(b"Founders", b"Editors")
    assert (
        verify_live_html(contract, body=changed, http_status=200, final_url=PAGE).outcome
        != "verified"
    )
