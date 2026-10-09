from itertools import product

import pytest
from signal_core.crawl_urls import (
    CrawlScopePolicy,
    CrawlUrlRejected,
    normalize_crawl_url,
    validate_public_addresses,
)


def policy(**overrides):
    values = {
        "schema_version": 1,
        "allowed_origins": ("https://Example.COM:443", "http://xn--bcher-kva.example"),
        "user_agent": "SignalBot/1.0 (+https://signal.example/bot)",
        **overrides,
    }
    return CrawlScopePolicy(**values)


def test_normalization_separates_original_fetch_display_origin_and_key():
    result = normalize_crawl_url(
        "HTTPS://B\N{LATIN SMALL LETTER U WITH DIAERESIS}CHER.Example.:443/A%2fb/?b=2&a=1#section"
    )

    assert result.original_url.endswith("#section")
    assert result.fetch_url == "https://xn--bcher-kva.example/A%2fb/?b=2&a=1"
    assert result.display_url == result.fetch_url
    assert result.normalized_key == result.fetch_url
    assert result.origin == "https://xn--bcher-kva.example"
    assert result.host == "xn--bcher-kva.example"
    assert result.port == 443
    assert result.request_target == "/A%2fb/?b=2&a=1"


def test_url_representation_does_not_dump_query_or_fragment_values():
    result = normalize_crawl_url("https://example.com/?token=private-query#private-fragment")
    assert "private-query" not in repr(result)
    assert "private-fragment" not in repr(result)


def test_normalization_is_idempotent_across_generated_safe_components():
    paths = ["", "/", "/A", "/a/", "/a%2Fb", "/caf\N{LATIN SMALL LETTER E WITH ACUTE}"]
    queries = ["", "?", "?a=1&b=2", "?b=2&a=1", "?x=%2F", "?x=one+x"]
    fragments = ["", "#top", "#client/route"]

    for path, query, fragment in product(paths, queries, fragments):
        first = normalize_crawl_url(f"https://EXAMPLE.com:443{path}{query}{fragment}")
        second = normalize_crawl_url(first.fetch_url)
        assert second.fetch_url == first.fetch_url
        assert second.normalized_key == first.normalized_key
        assert second.origin == "https://example.com"
        assert "#" not in first.fetch_url


def test_distinct_path_query_and_scheme_semantics_do_not_collapse():
    values = [
        "https://example.com/A",
        "https://example.com/a",
        "https://example.com/a/",
        "https://example.com/a?x=1&x=2",
        "https://example.com/a?x=2&x=1",
        "https://example.com/a?",
        "http://example.com/a",
    ]

    keys = [normalize_crawl_url(value).normalized_key for value in values]
    assert len(set(keys)) == len(values)


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "ftp://example.com/",
        "//example.com/",
        "https://user@example.com/",
        "https://user:pass@example.com/",
        "https://example.com:444/",
        "http://example.com:443/",
        "https://example.com/%",
        "https://example.com/%0dheader",
        "https://example.com/a\\b",
        "https://exa mple.com/",
        "https://example..com/",
        "https://[fe80::1%25en0]/",
        "https://example.com/\x00",
        "javascript:alert(1)",
        "https://example.com/" + "a" * 2048,
    ],
)
def test_malformed_ambiguous_and_unsafe_urls_fail_closed(value):
    with pytest.raises(CrawlUrlRejected):
        normalize_crawl_url(value)


def test_scope_canonicalizes_origins_and_rejects_cross_origin_redirects():
    scope = policy()
    assert scope.allowed_origins == (
        "https://example.com",
        "http://xn--bcher-kva.example",
    )
    current = scope.admit("https://example.com/a/index.html")
    assert scope.admit_redirect(current, "../next?q=1#fragment").fetch_url == (
        "https://example.com/next?q=1"
    )

    for location in [
        "https://www.example.com/",
        "http://example.com/",
        "//other.example/path",
        "file:///etc/passwd",
        "\r\nLocation: https://example.com/",
    ]:
        with pytest.raises(CrawlUrlRejected):
            scope.admit_redirect(current, location)


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema_version": 2},
        {"allowed_origins": ()},
        {"allowed_origins": ("https://example.com", "https://EXAMPLE.com:443")},
        {"allowed_origins": ("https://example.com/path",)},
        {"allowed_origins": ("https://example.com?",)},
        {"user_agent": "Mozilla/5.0"},
        {"user_agent": "SignalBot/1.0\r\nInjected: yes"},
        {"max_redirects": 11},
        {"max_redirects": True},
        {"max_body_bytes": 1023},
        {"request_timeout_seconds": 0.1},
        {"total_timeout_seconds": 0.5},
        {"request_timeout_seconds": 5, "total_timeout_seconds": 4},
    ],
)
def test_scope_policy_limits_are_closed(overrides):
    with pytest.raises(CrawlUrlRejected):
        policy(**overrides)


def test_every_resolved_address_must_be_public_and_answers_are_stable():
    assert validate_public_addresses(
        ["2606:2800:220:1:248:1893:25c8:1946", "93.184.216.34", "93.184.216.34"]
    ) == ("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946")

    blocked = [
        "0.0.0.0",
        "10.0.0.1",
        "100.64.0.1",
        "127.0.0.1",
        "169.254.169.254",
        "172.16.0.1",
        "192.0.2.1",
        "192.168.0.1",
        "224.0.0.1",
        "240.0.0.1",
        "::",
        "::1",
        "::ffff:127.0.0.1",
        "fc00::1",
        "fe80::1",
        "ff02::1",
        "2001:db8::1",
    ]
    for address in blocked:
        with pytest.raises(CrawlUrlRejected):
            validate_public_addresses([address])

    for invalid in [[], ["not-an-address"], ["93.184.216.34", "127.0.0.1"], [True]]:
        with pytest.raises(CrawlUrlRejected):
            validate_public_addresses(invalid)
