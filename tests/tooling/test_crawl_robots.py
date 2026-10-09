import hashlib

import pytest
from signal_core.crawl_robots import (
    ROBOTS_MAX_BODY_BYTES,
    ROBOTS_MAX_LINE_BYTES,
    ROBOTS_MAX_LINES,
    ROBOTS_PARSER_RELEASE,
    parse_robots,
)
from signal_core.crawl_urls import CrawlScopePolicy

ORIGIN = "https://crawl.example"
USER_AGENT = "SignalBot/1.0 (+https://signal.example/bot)"


def policy():
    return CrawlScopePolicy(
        schema_version=1,
        allowed_origins=(ORIGIN,),
        user_agent=USER_AGENT,
    )


def admitted(path):
    return policy().admit(f"{ORIGIN}{path}")


def test_rfc_groups_merge_exact_tokens_and_fallback_to_star():
    rules = parse_robots(
        b"""
User-agent: *
Disallow: /fallback
User-agent: signalbot
Disallow: /one
User-agent: SIGNALBOT
Disallow: /two
""",
        origin=ORIGIN,
        user_agent=USER_AGENT,
    )

    assert rules.can_fetch(admitted("/")) is True
    assert rules.can_fetch(admitted("/one")) is False
    assert rules.can_fetch(admitted("/two")) is False
    assert rules.can_fetch(admitted("/fallback")) is True


def test_rfc_longest_match_allow_tie_wildcard_end_and_percent_encoding():
    rules = parse_robots(
        b"""
User-agent: SignalBot
Disallow: /private
Allow: /private/public
Disallow: /download/*.zip$
Disallow: /encoded/%2Fsecret
""",
        origin=ORIGIN,
        user_agent=USER_AGENT,
    )

    assert rules.can_fetch(admitted("/private/page")) is False
    assert rules.can_fetch(admitted("/private/public")) is True
    assert rules.can_fetch(admitted("/download/file.zip")) is False
    assert rules.can_fetch(admitted("/download/file.zip?mirror=1")) is True
    assert rules.can_fetch(admitted("/encoded/%2Fsecret")) is False
    assert rules.can_fetch(admitted("/robots.txt")) is True


def test_parseable_utf8_lines_survive_malformed_and_unknown_records():
    body = (
        b"User-agent: SignalBot\n"
        b"unknown-field: ignored\n"
        b"Disallow: /private\n"
        b"\xffinvalid\n"
        b"Allow: /private/public\x00hidden\n"
    )
    rules = parse_robots(body, origin=ORIGIN, user_agent=USER_AGENT)

    assert rules.can_fetch(admitted("/private")) is False
    assert rules.can_fetch(admitted("/private/public")) is False
    assert rules.parse_error_count == 2
    assert rules.rule_count == 1
    assert rules.source_sha256 == hashlib.sha256(body).hexdigest()
    assert rules.parser_release == ROBOTS_PARSER_RELEASE
    assert "private" not in repr(rules)


def test_crawl_delay_is_voluntary_bounded_metadata():
    ordinary = parse_robots(
        b"User-agent: SignalBot\nCrawl-delay: 2.125\n",
        origin=ORIGIN,
        user_agent=USER_AGENT,
    )
    excessive = parse_robots(
        b"User-agent: SignalBot\nCrawl-delay: 900\n",
        origin=ORIGIN,
        user_agent=USER_AGENT,
    )
    assert ordinary.crawl_delay_ms == 2125
    assert excessive.crawl_delay_ms == 60_000


def test_parser_accepts_exact_500_kib_and_rejects_larger_content():
    exact = b"#" * ROBOTS_MAX_BODY_BYTES
    assert parse_robots(exact, origin=ORIGIN, user_agent=USER_AGENT).rule_count == 0
    with pytest.raises(ValueError, match="bounded"):
        parse_robots(exact + b"x", origin=ORIGIN, user_agent=USER_AGENT)


def test_line_count_line_size_and_wildcard_complexity_are_bounded():
    with pytest.raises(ValueError, match="line limit"):
        parse_robots(b"\n" * (ROBOTS_MAX_LINES + 1), origin=ORIGIN, user_agent=USER_AGENT)

    oversized_line = b"x" * (ROBOTS_MAX_LINE_BYTES + 1)
    rules = parse_robots(
        b"User-agent: SignalBot\n" + oversized_line + b"\nDisallow: /safe\n",
        origin=ORIGIN,
        user_agent=USER_AGENT,
    )
    assert rules.parse_error_count == 1
    assert rules.can_fetch(admitted("/safe")) is False

    wildcarded = parse_robots(
        b"User-agent: SignalBot\nDisallow: /" + b"*" * 65 + b"\n",
        origin=ORIGIN,
        user_agent=USER_AGENT,
    )
    assert wildcarded.parse_error_count == 1
    assert wildcarded.can_fetch(admitted("/anything")) is True


@pytest.mark.parametrize("body", ["text", bytearray(b"bytes")])
def test_parser_rejects_non_bytes(body):
    with pytest.raises(ValueError, match="bounded bytes"):
        parse_robots(body, origin=ORIGIN, user_agent=USER_AGENT)


def test_decisions_require_the_exact_origin_and_typed_admission():
    rules = parse_robots(b"User-agent: *\nDisallow: /\n", origin=ORIGIN, user_agent=USER_AGENT)
    with pytest.raises(ValueError, match="exact origin"):
        rules.can_fetch("https://crawl.example/")
    other = CrawlScopePolicy(
        schema_version=1,
        allowed_origins=("https://other.example",),
        user_agent=USER_AGENT,
    ).admit("https://other.example/")
    with pytest.raises(ValueError, match="exact origin"):
        rules.can_fetch(other)
