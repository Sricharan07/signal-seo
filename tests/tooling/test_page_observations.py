from datetime import UTC, datetime
from email.message import Message
from uuid import uuid4

import pytest
from signal_core.crawl_http import PinnedHttpFetcher
from signal_core.page_observations import (
    PreparedPageObservation,
    extract_page_metadata,
    observe_verified_homepage,
)


class FakeResponse:
    def __init__(self, *, status=200, content_type="text/html; charset=utf-8", body=b""):
        self.status = status
        self.headers = Message()
        self.headers.add_header("Content-Type", content_type)
        self.headers.add_header("Content-Length", str(len(body)))
        self._body = body

    def getheaders(self):
        return list(self.headers.items())

    def read(self, limit):
        return self._body[:limit]

    def close(self):
        return None


def prepared() -> PreparedPageObservation:
    return PreparedPageObservation(
        id=uuid4(),
        site_id=uuid4(),
        origin="https://example.com",
        command_id=uuid4(),
        manifest_id=uuid4(),
        manifest_sha256="a" * 64,
        verification_id=uuid4(),
        prepared_at=datetime.now(UTC),
        replayed=False,
    )


def test_extracts_normalized_title_first_heading_and_description():
    assert extract_page_metadata(
        "<html><head><title>  Useful   product </title>"
        '<meta NAME="description" content=" A focused summary. "></head>'
        "<body><h1>First <strong>heading</strong></h1><h1>Ignored</h1></body></html>"
    ) == ("Useful product", "First heading", "A focused summary.")
    assert extract_page_metadata("<html><head><title>Only title</title></head></html>") == (
        "Only title",
        None,
        None,
    )


@pytest.mark.parametrize("html", [None, "", "x\x00y", "x" * (512 * 1024 + 1)])
def test_metadata_parser_rejects_unbounded_or_ambiguous_html(html):
    with pytest.raises(ValueError, match="bounded"):
        extract_page_metadata(html)


def test_verified_homepage_fetches_exact_origin_and_extracts_real_metadata(monkeypatch):
    body = (
        b"<html><head><title>Observed title</title></head>"
        b"<body><h1>Observed heading</h1></body></html>"
    )
    response = FakeResponse(body=body)
    boundary = PinnedHttpFetcher(lambda host, port, timeout: ("93.184.216.34",))
    calls = []

    def exchange(url, address, *, user_agent, timeout):
        calls.append((url.fetch_url, address, user_agent, timeout))
        return response

    monkeypatch.setattr(boundary, "_exchange", exchange)
    result = observe_verified_homepage(boundary, prepared())

    assert calls == [
        (
            "https://example.com/",
            "93.184.216.34",
            "SignalBot/1.0 (+https://signal.example/bot)",
            10.0,
        )
    ]
    assert result.fetch_outcome == "observed"
    assert result.title == "Observed title"
    assert result.heading == "Observed heading"
    assert result.meta_description is None
    assert result.body_sha256 is not None


def test_verified_homepage_maps_network_http_and_decode_failures(monkeypatch):
    unavailable = PinnedHttpFetcher(
        lambda host, port, timeout: (_ for _ in ()).throw(OSError("private provider detail"))
    )
    assert observe_verified_homepage(unavailable, prepared()).fetch_outcome == (
        "transport_unavailable"
    )

    for response, expected in (
        (FakeResponse(status=503, body=b"unavailable"), "http_rejected"),
        (
            FakeResponse(content_type="text/html; charset=not-a-codec", body=b"<html></html>"),
            "invalid_html",
        ),
    ):
        boundary = PinnedHttpFetcher(lambda host, port, timeout: ("93.184.216.34",))
        monkeypatch.setattr(
            boundary,
            "_exchange",
            lambda *args, _response=response, **kwargs: _response,
        )
        assert observe_verified_homepage(boundary, prepared()).fetch_outcome == expected
