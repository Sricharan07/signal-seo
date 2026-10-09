import hashlib
import socket
import ssl
import threading
from email.message import Message

import pytest
from signal_core.crawl_http import (
    BoundedSystemResolver,
    CrawlFetchRejected,
    CrawlFetchUnavailable,
    EgressHttpRequest,
    PinnedHttpFetcher,
)
from signal_core.crawl_urls import CrawlScopePolicy


class FakeResponse:
    def __init__(self, status=200, *, headers=(), body=b"<html>ok</html>"):
        self.status = status
        self.headers = Message()
        for name, value in headers:
            self.headers.add_header(name, value)
        self._body = body
        self.closed = False

    def getheaders(self):
        return list(self.headers.items())

    def read(self, limit):
        return self._body[:limit]

    def close(self):
        self.closed = True


def policy(**overrides):
    values = {
        "schema_version": 1,
        "allowed_origins": ("http://crawl.example",),
        "user_agent": "SignalBot/1.0 (+https://signal.example/bot)",
        "max_body_bytes": 1024,
        **overrides,
    }
    return CrawlScopePolicy(**values)


def fetcher(answers=("93.184.216.34",)):
    return PinnedHttpFetcher(lambda host, port, timeout: answers)


def test_shared_egress_request_contract_rejects_unsafe_shapes():
    with pytest.raises(ValueError, match="GET and HEAD"):
        EgressHttpRequest("GET", "http://crawl.example/", body=b"no")
    with pytest.raises(ValueError, match="headers"):
        EgressHttpRequest(
            "POST",
            "http://crawl.example/",
            headers=(("cookie", "secret"), ("content-type", "application/json")),
            body=b"{}",
        )
    with pytest.raises(ValueError, match="headers"):
        EgressHttpRequest(
            "POST",
            "http://crawl.example/",
            headers=(("authorization", "Bearer good\r\nX-Evil: yes"),),
            body=b"{}",
        )
    with pytest.raises(ValueError, match="Content-Type"):
        EgressHttpRequest("POST", "http://crawl.example/", body=b"{}")
    with pytest.raises(ValueError, match="timeout"):
        EgressHttpRequest("GET", "http://crawl.example/", timeout_seconds=0)
    assert EgressHttpRequest(
        "GET",
        "http://crawl.example/",
        headers=(("x-github-api-version", "2026-03-10"),),
    ).headers == (("x-github-api-version", "2026-03-10"),)
    with pytest.raises(ValueError, match="headers"):
        EgressHttpRequest("GET", "http://crawl.example/", headers=(("user-agent", "spoofed"),))


def test_shared_egress_post_is_pinned_bounded_and_opaque(monkeypatch):
    boundary = fetcher()
    response = FakeResponse(
        headers=[("Content-Type", "application/json"), ("Content-Length", "11")],
        body=b'{"ok":true}',
    )
    calls = []

    def exchange(url, address, *, request, user_agent, timeout):
        calls.append((url.fetch_url, address, request.method, request.body, request.headers))
        return response

    monkeypatch.setattr(boundary, "_exchange_egress", exchange)
    request = EgressHttpRequest(
        "POST",
        "http://crawl.example/v1/check",
        headers=(
            ("authorization", "Bearer provider-secret"),
            ("content-type", "application/json"),
            ("accept", "application/json"),
        ),
        body=b'{"query":"private"}',
    )

    result = boundary.request(request, policy=policy())

    assert calls == [
        (
            "http://crawl.example/v1/check",
            "93.184.216.34",
            "POST",
            b'{"query":"private"}',
            request.headers,
        )
    ]
    assert result.outcome == "fetched"
    assert result.body == b'{"ok":true}'
    assert result.body_sha256 == hashlib.sha256(result.body).hexdigest()
    assert "provider-secret" not in repr(request)
    assert "private" not in repr(request)
    assert "ok" not in repr(result)


def test_shared_egress_post_never_retries_an_ambiguous_transport_failure(monkeypatch):
    boundary = fetcher(("93.184.216.34", "93.184.216.35"))
    calls = []

    def exchange(url, address, *, request, user_agent, timeout):
        calls.append(address)
        raise OSError("response lost after an ambiguous write")

    monkeypatch.setattr(boundary, "_exchange_egress", exchange)
    request = EgressHttpRequest(
        "POST",
        "http://crawl.example/v1/check",
        headers=(("content-type", "application/json"),),
        body=b"{}",
    )

    with pytest.raises(CrawlFetchUnavailable, match="unavailable"):
        boundary.request(request, policy=policy())

    assert calls == ["93.184.216.34"]


def test_shared_egress_rejects_redirect_without_following(monkeypatch):
    boundary = fetcher()
    calls = []

    def exchange(*args, **kwargs):
        calls.append(1)
        return FakeResponse(
            302,
            headers=[("Location", "http://169.254.169.254/latest/meta-data")],
            body=b"",
        )

    monkeypatch.setattr(boundary, "_exchange_egress", exchange)
    result = boundary.request(
        EgressHttpRequest("GET", "http://crawl.example/start", accepted_media_types=("text/html",)),
        policy=policy(),
    )

    assert calls == [1]
    assert result.outcome == "redirect_rejected"
    assert result.body == b""
    assert dict(result.response_headers).get("location") is None


def test_shared_egress_enforces_the_absolute_response_deadline(monkeypatch):
    ticks = iter((0.0, 0.0, 0.0, 0.0, 1.0))
    boundary = PinnedHttpFetcher(
        lambda host, port, timeout: ("93.184.216.34",),
        clock=lambda: next(ticks),
    )
    response = FakeResponse(
        headers=[("Content-Type", "application/json")],
        body=b'{"ok":true}',
    )
    monkeypatch.setattr(boundary, "_exchange_egress", lambda *args, **kwargs: response)

    with pytest.raises(CrawlFetchUnavailable, match="total timeout"):
        boundary.request(
            EgressHttpRequest(
                "GET",
                "http://crawl.example/status",
                timeout_seconds=0.25,
            ),
            policy=policy(),
        )

    assert response.closed is True


def test_shared_egress_maps_response_stream_failures_to_unavailable(monkeypatch):
    boundary = fetcher()
    response = FakeResponse(headers=[("Content-Type", "application/json")])

    def fail_read(limit):
        raise TimeoutError("synthetic response timeout")

    response.read = fail_read
    monkeypatch.setattr(boundary, "_exchange_egress", lambda *args, **kwargs: response)

    with pytest.raises(CrawlFetchUnavailable, match="response became unavailable"):
        boundary.request(
            EgressHttpRequest("GET", "http://crawl.example/status"),
            policy=policy(),
        )

    assert response.closed is True


def test_shared_egress_head_accepts_representation_length_without_body(monkeypatch):
    boundary = fetcher()
    response = FakeResponse(
        headers=[("Content-Type", "application/json"), ("Content-Length", "42")],
        body=b"",
    )
    monkeypatch.setattr(boundary, "_exchange_egress", lambda *args, **kwargs: response)

    result = boundary.request(
        EgressHttpRequest("HEAD", "http://crawl.example/status"),
        policy=policy(),
    )

    assert result.outcome == "fetched"
    assert result.body == b""
    assert result.decoded_bytes == 0


def test_bounded_system_resolver_returns_unique_ipv4_and_ipv6_answers(monkeypatch):
    calls = []

    def getaddrinfo(host, port, *, family, type, proto):
        calls.append((host, port, family, type, proto))
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", port)),
            (
                socket.AF_INET6,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("2606:2800:220:1:248:1893:25c8:1946", port, 0, 0),
            ),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", port)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)

    assert BoundedSystemResolver()("example.com", 443, 1.0) == (
        "93.184.216.34",
        "2606:2800:220:1:248:1893:25c8:1946",
    )
    assert calls == [("example.com", 443, socket.AF_UNSPEC, socket.SOCK_STREAM, socket.IPPROTO_TCP)]


@pytest.mark.parametrize(
    ("host", "port", "timeout"),
    [("", 443, 1.0), ("example.com", 0, 1.0), ("example.com", 443, 0.0)],
)
def test_bounded_system_resolver_rejects_invalid_inputs(host, port, timeout):
    with pytest.raises(ValueError):
        BoundedSystemResolver()(host, port, timeout)


def test_bounded_system_resolver_fails_closed_on_dns_error_and_timeout(monkeypatch):
    resolver = BoundedSystemResolver(max_in_flight=1)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [])
    with pytest.raises(OSError, match="unavailable"):
        resolver("example.com", 443, 1.0)

    release = threading.Event()

    def blocked_lookup(*args, **kwargs):
        release.wait(1.0)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", blocked_lookup)
    try:
        with pytest.raises(TimeoutError, match="timeout"):
            resolver("example.com", 443, 0.01)
        with pytest.raises(TimeoutError, match="capacity"):
            resolver("example.com", 443, 0.01)
    finally:
        release.set()


def test_fetch_records_bounded_sanitized_evidence_and_exact_body_hash(monkeypatch):
    response = FakeResponse(
        headers=[
            ("Content-Type", "Text/HTML; charset=utf-8"),
            ("Content-Length", "33"),
            ("ETag", '"public-value"'),
            ("Set-Cookie", "private=secret"),
            ("Authorization", "Bearer secret"),
            ("X-Unbounded", "ignored"),
        ],
        body=b"<html>private-page-content</html>",
    )
    boundary = fetcher()
    calls = []

    def exchange(url, address, *, user_agent, timeout):
        calls.append((url.fetch_url, address, user_agent, timeout))
        return response

    monkeypatch.setattr(boundary, "_exchange", exchange)
    result = boundary.fetch("HTTP://CRAWL.EXAMPLE/?token=private-query#fragment", policy=policy())

    assert calls == [
        (
            "http://crawl.example/?token=private-query",
            "93.184.216.34",
            "SignalBot/1.0 (+https://signal.example/bot)",
            10.0,
        )
    ]
    assert result.original_url == "HTTP://CRAWL.EXAMPLE/?token=private-query#fragment"
    assert result.final_url == "http://crawl.example/?token=private-query"
    assert result.normalized_key == result.final_url
    assert result.outcome == "fetched"
    assert result.http_status == 200
    assert result.media_type == "text/html"
    assert result.body == b"<html>private-page-content</html>"
    assert result.body_sha256 == hashlib.sha256(result.body).hexdigest()
    assert result.decoded_bytes == len(result.body)
    assert result.response_headers == (
        ("content-length", "33"),
        ("content-type", "Text/HTML; charset=utf-8"),
        ("etag", '"public-value"'),
    )
    assert "private-query" not in repr(result)
    assert "private-page-content" not in repr(result)
    assert response.closed is True


def test_plaintext_fetch_uses_narrow_accept_profile_and_retains_exact_body(monkeypatch):
    response = FakeResponse(
        headers=[("Content-Type", "text/plain; charset=utf-8")],
        body=b"signal-site-verification=proof\n",
    )
    boundary = fetcher()
    calls = []

    def exchange(url, address, *, user_agent, timeout, accept_header):
        calls.append((url.fetch_url, address, accept_header))
        return response

    monkeypatch.setattr(boundary, "_exchange", exchange)
    result = boundary.fetch_text("http://crawl.example/proof.txt", policy=policy())

    assert calls == [("http://crawl.example/proof.txt", "93.184.216.34", "text/plain")]
    assert result.outcome == "fetched"
    assert result.media_type == "text/plain"
    assert result.body == b"signal-site-verification=proof\n"
    assert "signal-site-verification" not in repr(result)


def test_plaintext_fetch_rejects_html_and_redirects_without_retaining_body(monkeypatch):
    boundary = fetcher()
    html = FakeResponse(headers=[("Content-Type", "text/html")], body=b"not proof")
    monkeypatch.setattr(boundary, "_exchange", lambda *args, **kwargs: html)

    unsupported = boundary.fetch_text("http://crawl.example/proof.txt", policy=policy())

    assert unsupported.outcome == "unsupported_media_type"
    assert unsupported.body == b""

    redirected = fetcher()
    response = FakeResponse(302, headers=[("Location", "/other-proof.txt")])
    monkeypatch.setattr(redirected, "_exchange", lambda *args, **kwargs: response)
    with pytest.raises(CrawlFetchRejected, match="redirect limit"):
        redirected.fetch_text(
            "http://crawl.example/proof.txt",
            policy=policy(max_redirects=0),
        )


def test_all_resolver_answers_are_validated_before_any_connection(monkeypatch):
    boundary = fetcher(("93.184.216.34", "127.0.0.1"))
    calls = []
    monkeypatch.setattr(boundary, "_exchange", lambda *args, **kwargs: calls.append(args))

    with pytest.raises(CrawlFetchRejected, match="resolution was rejected"):
        boundary.fetch("http://crawl.example/", policy=policy())

    assert calls == []


def test_resolver_failure_is_retryable_and_sanitized():
    def fail(host, port, timeout):
        raise RuntimeError("private resolver topology")

    with pytest.raises(CrawlFetchUnavailable) as captured:
        PinnedHttpFetcher(fail).fetch("http://crawl.example/", policy=policy())
    assert "private" not in str(captured.value)
    assert "topology" not in str(captured.value)


def test_resolver_receives_the_remaining_bounded_request_timeout(monkeypatch):
    calls = []

    def resolve(host, port, timeout):
        calls.append((host, port, timeout))
        return ["93.184.216.34"]

    boundary = PinnedHttpFetcher(resolve)
    monkeypatch.setattr(
        boundary,
        "_exchange",
        lambda *args, **kwargs: FakeResponse(headers=[("Content-Type", "text/html")]),
    )
    boundary.fetch(
        "http://crawl.example/",
        policy=policy(request_timeout_seconds=3, total_timeout_seconds=4),
    )
    assert calls == [("crawl.example", 80, 3.0)]


def test_connection_falls_through_only_prevalidated_public_answers(monkeypatch):
    boundary = fetcher(("93.184.216.34", "93.184.216.35"))
    attempts = []
    response = FakeResponse(headers=[("Content-Type", "text/html")])

    def exchange(url, address, *, user_agent, timeout):
        attempts.append(address)
        if address.endswith("34"):
            raise OSError("unavailable")
        return response

    monkeypatch.setattr(boundary, "_exchange", exchange)
    result = boundary.fetch("http://crawl.example/", policy=policy())
    assert attempts == ["93.184.216.34", "93.184.216.35"]
    assert result.resolved_address == "93.184.216.35"


def test_address_fallbacks_share_one_bounded_exchange_timeout(monkeypatch):
    times = iter([0.0, 0.0, 0.0, 9.0, 9.0])
    boundary = PinnedHttpFetcher(
        lambda host, port, timeout: ["93.184.216.34", "93.184.216.35"],
        clock=lambda: next(times),
    )
    timeouts = []

    def exchange(url, address, *, user_agent, timeout):
        timeouts.append(timeout)
        if address.endswith("34"):
            raise OSError("unavailable")
        return FakeResponse(headers=[("Content-Type", "text/html")])

    monkeypatch.setattr(boundary, "_exchange", exchange)
    result = boundary.fetch("http://crawl.example/", policy=policy())

    assert timeouts == [10.0, 1.0]
    assert result.resolved_address == "93.184.216.35"


def test_same_origin_redirect_is_revalidated_and_cross_origin_is_rejected(monkeypatch):
    boundary = fetcher()
    responses = iter(
        [
            FakeResponse(302, headers=[("Location", "/final?q=1#ignored")]),
            FakeResponse(headers=[("Content-Type", "text/html")], body=b"done"),
        ]
    )
    monkeypatch.setattr(boundary, "_exchange", lambda *args, **kwargs: next(responses))
    result = boundary.fetch("http://crawl.example/start", policy=policy())
    assert result.final_url == "http://crawl.example/final?q=1"
    assert result.redirect_chain == ("http://crawl.example/final?q=1",)
    assert result.body == b"done"

    blocked = fetcher()
    first = FakeResponse(302, headers=[("Location", "http://other.example/private")])
    monkeypatch.setattr(blocked, "_exchange", lambda *args, **kwargs: first)
    with pytest.raises(CrawlFetchRejected, match="redirect target"):
        blocked.fetch("http://crawl.example/start", policy=policy())
    assert first.closed is True


def test_redirect_loop_limit_and_missing_or_duplicate_location_fail_closed(monkeypatch):
    cases = [
        (FakeResponse(302, headers=[]), policy()),
        (
            FakeResponse(302, headers=[("Location", "/a"), ("Location", "/b")]),
            policy(),
        ),
        (FakeResponse(302, headers=[("Location", "/next")]), policy(max_redirects=0)),
    ]
    for response, scope in cases:
        boundary = fetcher()
        monkeypatch.setattr(boundary, "_exchange", lambda *args, _r=response, **kwargs: _r)
        with pytest.raises(CrawlFetchRejected):
            boundary.fetch("http://crawl.example/start", policy=scope)

    loop = fetcher()
    monkeypatch.setattr(
        loop,
        "_exchange",
        lambda *args, **kwargs: FakeResponse(302, headers=[("Location", "/start")]),
    )
    with pytest.raises(CrawlFetchRejected, match="loop"):
        loop.fetch("http://crawl.example/start", policy=policy())


@pytest.mark.parametrize(
    ("headers", "body", "outcome"),
    [
        ([("Content-Type", "application/pdf")], b"pdf", "unsupported_media_type"),
        (
            [("Content-Type", "text/html"), ("Content-Encoding", "gzip")],
            b"compressed",
            "unsupported_encoding",
        ),
        (
            [("Content-Type", "text/html"), ("Content-Length", "2048")],
            b"not-read",
            "body_limit",
        ),
        ([("Content-Type", "text/html")], b"x" * 1025, "body_limit"),
    ],
)
def test_unsupported_or_oversized_bodies_have_explicit_empty_outcomes(
    monkeypatch, headers, body, outcome
):
    response = FakeResponse(headers=headers, body=body)
    boundary = fetcher()
    monkeypatch.setattr(boundary, "_exchange", lambda *args, **kwargs: response)

    result = boundary.fetch("http://crawl.example/", policy=policy())
    assert result.outcome == outcome
    assert result.body == b""
    assert result.body_sha256 is None
    assert result.decoded_bytes == 0


@pytest.mark.parametrize(
    "headers",
    [
        [("Content-Type", "text/html"), ("Content-Length", "1x")],
        [("Content-Type", "text/html"), ("Content-Length", "1"), ("Content-Length", "1")],
        [("Content-Type", "text/html"), ("Content-Length", "1"), ("Transfer-Encoding", "chunked")],
        [("Content-Type", "text/html"), ("Transfer-Encoding", "gzip")],
        [("Content-Type", "text/html\x7f")],
    ],
)
def test_ambiguous_framing_and_invalid_headers_are_rejected(monkeypatch, headers):
    boundary = fetcher()
    monkeypatch.setattr(
        boundary,
        "_exchange",
        lambda *args, **kwargs: FakeResponse(headers=headers, body=b"x"),
    )
    with pytest.raises(CrawlFetchRejected):
        boundary.fetch("http://crawl.example/", policy=policy())


def test_truncated_body_and_unusable_not_modified_response_fail_closed(monkeypatch):
    boundary = fetcher()
    monkeypatch.setattr(
        boundary,
        "_exchange",
        lambda *args, **kwargs: FakeResponse(
            headers=[("Content-Type", "text/html"), ("Content-Length", "10")],
            body=b"short",
        ),
    )
    with pytest.raises(CrawlFetchUnavailable, match="incomplete"):
        boundary.fetch("http://crawl.example/", policy=policy())

    boundary = fetcher()
    monkeypatch.setattr(
        boundary,
        "_exchange",
        lambda *args, **kwargs: FakeResponse(304, headers=[("Content-Length", "0")], body=b""),
    )
    with pytest.raises(CrawlFetchRejected, match="retained prior body"):
        boundary.fetch("http://crawl.example/", policy=policy())


def test_total_timeout_includes_body_read_and_returns_no_result(monkeypatch):
    times = iter([0.0, 0.0, 0.0, 31.0])
    boundary = PinnedHttpFetcher(
        lambda host, port, timeout: ["93.184.216.34"],
        clock=lambda: next(times),
    )
    monkeypatch.setattr(
        boundary,
        "_exchange",
        lambda *args, **kwargs: FakeResponse(headers=[("Content-Type", "text/html")]),
    )
    with pytest.raises(CrawlFetchUnavailable, match="total timeout"):
        boundary.fetch("http://crawl.example/", policy=policy())


def test_fetcher_requires_explicit_valid_ports():
    with pytest.raises(ValueError, match="resolver"):
        PinnedHttpFetcher(None)
    with pytest.raises(ValueError, match="TLS"):
        PinnedHttpFetcher(lambda host, port, timeout: [], tls_context=object())
    with pytest.raises(ValueError, match="clock"):
        PinnedHttpFetcher(lambda host, port, timeout: [], clock=None)
    with pytest.raises(ValueError, match="scope"):
        fetcher().fetch("http://crawl.example/", policy=object())

    context = ssl.create_default_context()
    assert isinstance(
        PinnedHttpFetcher(lambda host, port, timeout: [], tls_context=context),
        PinnedHttpFetcher,
    )


def test_robots_fetch_uses_exact_path_profile_and_bounded_body(monkeypatch):
    response = FakeResponse(
        headers=[("Content-Type", "text/plain; charset=utf-8")],
        body=b"User-agent: *\nDisallow: /private\n",
    )
    boundary = fetcher()
    calls = []

    def exchange(url, address, *, user_agent, timeout, accept_header):
        calls.append((url.fetch_url, address, user_agent, timeout, accept_header))
        return response

    monkeypatch.setattr(boundary, "_exchange", exchange)
    result = boundary.fetch_robots("http://crawl.example", policy=policy())

    assert calls == [
        (
            "http://crawl.example/robots.txt",
            "93.184.216.34",
            "SignalBot/1.0 (+https://signal.example/bot)",
            10.0,
            "text/plain,*/*;q=0.1",
        )
    ]
    assert result.outcome == "fetched"
    assert result.body == b"User-agent: *\nDisallow: /private\n"
    assert result.body_sha256 == hashlib.sha256(result.body).hexdigest()
    assert "private" not in repr(result)


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (404, "not_found"),
        (401, "forbidden"),
        (403, "forbidden"),
        (429, "backoff"),
        (503, "server_error"),
        (410, "client_error"),
        (304, "policy_rejected"),
    ],
)
def test_robots_statuses_have_conservative_bodyless_outcomes(monkeypatch, status, outcome):
    response = FakeResponse(
        status,
        headers=[("Retry-After", "30")] if status == 429 else [],
        body=b"must-not-be-retained",
    )
    boundary = fetcher()
    monkeypatch.setattr(boundary, "_exchange", lambda *args, **kwargs: response)

    result = boundary.fetch_robots("http://crawl.example", policy=policy())

    assert result.outcome == outcome
    assert result.http_status == status
    assert result.body == b""
    assert result.body_sha256 is None


@pytest.mark.parametrize(
    ("headers", "body", "outcome"),
    [
        ([("Content-Type", "text/html")], b"rules", "unsupported_media_type"),
        (
            [("Content-Type", "text/plain"), ("Content-Encoding", "gzip")],
            b"rules",
            "unsupported_encoding",
        ),
        (
            [("Content-Type", "text/plain"), ("Content-Length", str(500 * 1024 + 1))],
            b"rules",
            "body_limit",
        ),
        ([("Content-Type", "text/plain")], b"x" * (500 * 1024 + 1), "body_limit"),
    ],
)
def test_robots_rejects_unsupported_or_oversized_success_bodies(
    monkeypatch, headers, body, outcome
):
    boundary = fetcher()
    monkeypatch.setattr(
        boundary, "_exchange", lambda *args, **kwargs: FakeResponse(headers=headers, body=body)
    )
    result = boundary.fetch_robots("http://crawl.example", policy=policy())
    assert result.outcome == outcome
    assert result.body == b""


def test_robots_unsafe_redirect_and_transport_failure_become_closed_evidence(monkeypatch):
    boundary = fetcher()
    monkeypatch.setattr(
        boundary,
        "_exchange",
        lambda *args, **kwargs: FakeResponse(
            302, headers=[("Location", "http://other.example/robots.txt")]
        ),
    )
    rejected = boundary.fetch_robots("http://crawl.example", policy=policy())
    assert rejected.outcome == "policy_rejected"
    assert rejected.http_status == 302
    assert rejected.redirect_chain == ()

    unavailable = PinnedHttpFetcher(lambda host, port, timeout: (_ for _ in ()).throw(OSError()))
    failed = unavailable.fetch_robots("http://crawl.example", policy=policy())
    assert failed.outcome == "transport_error"
    assert failed.http_status is None
    assert failed.resolved_address is None


def test_robots_origin_must_be_an_exact_admitted_origin():
    with pytest.raises(CrawlFetchRejected, match="outside"):
        fetcher().fetch_robots("http://other.example", policy=policy())
