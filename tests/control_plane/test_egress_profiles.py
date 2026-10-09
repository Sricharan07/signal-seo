"""Cross-profile denial at the shared outbound boundary."""

import hashlib

import pytest
from signal_core.crawl_http import EgressHttpRequest, EgressHttpResult
from signal_core.crawl_urls import normalize_crawl_url
from signal_core.egress_profiles import EgressProfile, profile_headers
from signal_core.shared_egress import SharedEgressRequest, _valid_response


@pytest.mark.parametrize(
    "method,url,body,headers,timeout,size",
    [
        ("POST", "https://slack.com/api/conversations.history", b"{}", None, 5, 4096),
        ("POST", "https://slack.com/api/conversations.open", b"{}", None, 5, 4096),
        ("GET", "https://slack.com/api/chat.postMessage", b"{}", None, 5, 4096),
        ("POST", "https://api.slack.com/api/chat.postMessage", b"{}", None, 5, 4096),
        ("POST", "https://slack.com/api/chat.postMessage?token=synthetic", b"{}", None, 5, 4096),
        ("POST", "https://slack.com/api/chat.postMessage", b"token=synthetic", None, 5, 4096),
        ("POST", "https://slack.com/api/chat.postMessage", b"[]", None, 5, 4096),
        (
            "POST",
            "https://slack.com/api/chat.postMessage",
            b"{}",
            (
                ("authorization", "Bearer synthetic-token"),
                ("accept", "application/json"),
                ("content-type", "application/x-www-form-urlencoded"),
            ),
            5,
            4096,
        ),
        ("POST", "https://slack.com/api/chat.postMessage", b"{}", None, 6, 4096),
        ("POST", "https://slack.com/api/chat.postMessage", b"{}", None, 5, 16385),
    ],
)
def test_slack_closed_profile_negatives(method, url, body, headers, timeout, size):
    with pytest.raises(ValueError):
        outbound(
            EgressProfile.SLACK_BOT,
            "connector",
            method,
            url,
            authorization="Bearer synthetic-token",
            body=body,
            headers=headers,
            timeout=timeout,
            size=size,
        )


@pytest.mark.parametrize("path", ["chat.postMessage", "auth.revoke"])
def test_slack_bot_exact_json_methods(path):
    request = outbound(
        EgressProfile.SLACK_BOT,
        "connector",
        "POST",
        "https://slack.com/api/" + path,
        authorization="Bearer synthetic-token",
        body=b"{}",
    )
    assert request.credentialed


def test_slack_oauth_form_is_sensitive_and_not_bot_authority():
    request = outbound(
        EgressProfile.SLACK_OAUTH,
        "connector",
        "POST",
        "https://slack.com/api/oauth.v2.access",
        body=(
            b"client_id=123.456&client_secret=synthetic-secret&code=synthetic-code"
            b"&redirect_uri=https%3A%2F%2Fdashboard.example.invalid%2Fauth%2Fslack%2Fcallback"
        ),
        sensitive=True,
    )
    assert request.credentialed
    assert dict(request.http.headers)["content-type"] == "application/x-www-form-urlencoded"
    with pytest.raises(ValueError):
        outbound(
            EgressProfile.SLACK_BOT,
            "connector",
            "POST",
            "https://slack.com/api/oauth.v2.access",
            authorization="Bearer synthetic-token",
            body=b"{}",
        )


@pytest.mark.parametrize(
    "url,body,headers",
    [
        ("https://slack.com/api/oauth.v2.access", b"{}", None),
        ("https://slack.com/api/chat.postMessage", b"client_id=synthetic", None),
        ("https://slack.com/api/oauth.v2.access?code=synthetic", b"client_id=synthetic", None),
        ("https://slack.com/api/oauth.v2.access#fragment", b"client_id=synthetic", None),
        (
            "https://slack.com/api/oauth.v2.access",
            b"client_id=123.456&client_secret=synthetic-secret&code=synthetic-code&redirect_uri=https%3A%2F%2Fdashboard.example.invalid",
            (("accept", "application/json"), ("content-type", "application/json")),
        ),
        (
            "https://slack.com/api/oauth.v2.access",
            b"client_id=123.456&client_id=789.012&client_secret=synthetic-secret&code=synthetic-code&redirect_uri=synthetic",
            None,
        ),
    ],
)
def test_slack_oauth_rejects_json_other_routes_queries_and_ambiguous_fields(url, body, headers):
    with pytest.raises(ValueError):
        outbound(
            EgressProfile.SLACK_OAUTH,
            "connector",
            "POST",
            url,
            body=body,
            headers=headers,
            sensitive=True,
        )


def outbound(
    profile: EgressProfile,
    purpose: str,
    method: str,
    url: str,
    *,
    authorization: str | None = None,
    body: bytes = b"",
    media: tuple[str, ...] = ("application/json",),
    headers: tuple[tuple[str, str], ...] | None = None,
    sensitive: bool = False,
    size: int = 4096,
    timeout: float = 5,
) -> SharedEgressRequest:
    return SharedEgressRequest(
        purpose,
        EgressHttpRequest(
            method,
            url,
            headers=headers
            if headers is not None
            else profile_headers(profile, method, authorization),
            body=body,
            accepted_media_types=media,
            max_response_bytes=size,
            timeout_seconds=timeout,
        ),
        profile,
        sensitive_body=sensitive,
    )


def test_github_cannot_borrow_oauth_form_or_google_origin():
    with pytest.raises(ValueError):
        outbound(
            EgressProfile.GITHUB_REST,
            "connector",
            "POST",
            "https://api.github.com/app/installations/1/access_tokens",
            authorization="Bearer synthetic-github-token",
            body=b"grant_type=refresh_token",
            headers=(
                ("accept", "application/vnd.github+json"),
                ("authorization", "Bearer synthetic-github-token"),
                ("content-type", "application/x-www-form-urlencoded"),
                ("x-github-api-version", "2026-03-10"),
            ),
            media=("application/json", "application/vnd.github+json"),
        )
    with pytest.raises(ValueError):
        outbound(
            EgressProfile.GITHUB_REST,
            "connector",
            "GET",
            "https://www.googleapis.com/webmasters/v3/sites",
            authorization="Bearer synthetic-github-token",
            media=("application/json", "application/vnd.github+json"),
        )


def test_oauth_token_cannot_accept_html_or_bearer_header():
    url = "https://oauth2.googleapis.com/token"
    request = outbound(
        EgressProfile.GOOGLE_OAUTH_TOKEN,
        "connector",
        "POST",
        url,
        body=b"grant_type=authorization_code",
        sensitive=True,
    )
    body = b"<html>no</html>"
    html = EgressHttpResult(
        schema_version=1,
        request_url=url,
        final_url=url,
        method="POST",
        outcome="fetched",
        http_status=200,
        media_type="text/html",
        response_headers=(("content-type", "text/html"),),
        resolved_address="8.8.8.8",
        body=body,
        body_sha256=hashlib.sha256(body).hexdigest(),
        decoded_bytes=len(body),
        elapsed_ms=2,
    )
    assert not _valid_response(request.http, normalize_crawl_url(url), html)
    with pytest.raises(ValueError):
        outbound(
            EgressProfile.GOOGLE_OAUTH_TOKEN,
            "connector",
            "POST",
            url,
            authorization="Bearer synthetic-token",
            body=b"grant_type=authorization_code",
            sensitive=True,
        )


def test_crawl_cannot_carry_authorization_or_post_and_no_profile_accepts_cookies():
    url = "https://site.example/page"
    with pytest.raises(ValueError):
        outbound(
            EgressProfile.CRAWL_PAGE,
            "crawl",
            "GET",
            url,
            authorization="Bearer synthetic-token",
            media=("text/html", "application/xhtml+xml"),
        )
    with pytest.raises(ValueError):
        outbound(
            EgressProfile.CRAWL_PAGE,
            "crawl",
            "POST",
            url,
            body=b"{}",
            media=("text/html", "application/xhtml+xml"),
        )
    with pytest.raises(ValueError):
        outbound(
            EgressProfile.CRAWL_PAGE,
            "crawl",
            "GET",
            url,
            headers=(("cookie", "synthetic-session"),),
            media=("text/html", "application/xhtml+xml"),
        )


def test_gsc_and_assistant_cannot_borrow_github_vendor_json_or_exceed_bounds():
    with pytest.raises(ValueError):
        outbound(
            EgressProfile.GSC_API,
            "connector",
            "GET",
            "https://www.googleapis.com/webmasters/v3/sites",
            authorization="Bearer synthetic-google-token",
            media=("application/json", "application/vnd.github+json"),
        )
    with pytest.raises(ValueError):
        outbound(
            EgressProfile.OPENAI_ASSISTANT,
            "model",
            "POST",
            "https://api.openai.com/v1/responses",
            authorization="Bearer synthetic-openai-token",
            body=b"{}",
            size=129 * 1024,
        )
    with pytest.raises(ValueError):
        outbound(
            EgressProfile.OPENAI_ASSISTANT,
            "model",
            "POST",
            "https://api.openai.com/v1/responses",
            authorization="Bearer synthetic-openai-token",
            body=b"{}",
            timeout=31,
        )


def test_gemini_api_key_header_is_exclusive_and_absent_from_request_digest():
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    key = "synthetic-gemini-api-key-123456"
    request = outbound(
        EgressProfile.GEMINI_ASSISTANT,
        "model",
        "POST",
        url,
        body=b"{}",
        headers=profile_headers(EgressProfile.GEMINI_ASSISTANT, "POST", None, key),
    )
    assert request.credentialed
    assert key.encode() not in request.request_sha256
    with pytest.raises(ValueError):
        outbound(
            EgressProfile.OPENAI_ASSISTANT,
            "model",
            "POST",
            "https://api.openai.com/v1/responses",
            authorization="Bearer synthetic-openai-token",
            body=b"{}",
            headers=profile_headers(
                EgressProfile.OPENAI_ASSISTANT,
                "POST",
                "Bearer synthetic-openai-token",
                key,
            ),
        )


def test_assistant_profiles_reject_cross_provider_credentials_and_html():
    openai_url = "https://api.openai.com/v1/responses"
    request = outbound(
        EgressProfile.OPENAI_ASSISTANT,
        "model",
        "POST",
        openai_url,
        authorization="Bearer synthetic-openai-token",
        body=b"{}",
    )
    html = b"<html>untrusted</html>"
    response = EgressHttpResult(
        schema_version=1,
        request_url=openai_url,
        final_url=openai_url,
        method="POST",
        outcome="fetched",
        http_status=200,
        media_type="text/html",
        response_headers=(("content-type", "text/html"),),
        resolved_address="8.8.8.8",
        body=html,
        body_sha256=hashlib.sha256(html).hexdigest(),
        decoded_bytes=len(html),
        elapsed_ms=2,
    )
    assert not _valid_response(request.http, normalize_crawl_url(openai_url), response)
    with pytest.raises(ValueError):
        outbound(
            EgressProfile.PERPLEXITY_ASSISTANT,
            "model",
            "POST",
            openai_url,
            authorization="Bearer synthetic-perplexity-token",
            body=b"{}",
        )
    with pytest.raises(ValueError):
        outbound(
            EgressProfile.OPENAI_ASSISTANT,
            "connector",
            "POST",
            openai_url,
            authorization="Bearer synthetic-openai-token",
            body=b"{}",
        )
    with pytest.raises(ValueError):
        outbound(
            EgressProfile.GEMINI_ASSISTANT,
            "model",
            "POST",
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
            authorization="Bearer synthetic-openai-token",
            body=b"{}",
        )
