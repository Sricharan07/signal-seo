"""Repository writes cannot borrow another outbound capability."""

from dataclasses import replace
from uuid import uuid4

import pytest
from signal_core.crawl_http import EgressHttpRequest
from signal_core.egress_profiles import (
    PROFILE_RULES,
    EgressProfile,
    GitHubRepositoryWriteScope,
    profile_headers,
)
from signal_core.shared_egress import SharedEgressRequest

SCOPE = GitHubRepositoryWriteScope("SignalOwner", "website", "synthetic-installation-token")
ROOT = "https://api.github.com/repos/SignalOwner/website"


def outbound(
    path="/git/trees",
    *,
    method="POST",
    body=b"{}",
    profile=EgressProfile.GITHUB_REPOSITORY_WRITE,
    scope=SCOPE,
):
    headers = profile_headers(profile, method, "Bearer " + SCOPE.installation_token)
    rules = PROFILE_RULES[profile]
    return SharedEgressRequest(
        rules.purpose,
        EgressHttpRequest(
            method,
            ROOT + path,
            headers=headers,
            body=body,
            accepted_media_types=rules.response_media_types,
            max_response_bytes=min(256 * 1024, rules.max_response_bytes),
            timeout_seconds=5,
        ),
        profile,
        github_write_scope=scope,
    )


@pytest.mark.parametrize("path", ["/git/trees", "/git/blobs", "/git/commits", "/pulls"])
def test_exact_json_creation_routes(path):
    request = outbound(path)
    assert request.profile == EgressProfile.GITHUB_REPOSITORY_WRITE
    assert SCOPE.installation_token not in repr(request)
    assert SCOPE.installation_token not in repr(SCOPE)


def test_only_operation_specific_ref_and_exact_lookups():
    branch = "signal/" + uuid4().hex
    outbound("/git/refs", body=('{"ref":"refs/heads/' + branch + '"}').encode())
    outbound("/git/ref/heads/" + branch, method="GET", body=b"")
    outbound("/git/trees/" + "a" * 40, method="GET", body=b"")
    outbound(
        "/pulls?head=SignalOwner%3A" + branch + "&base=main&state=all&per_page=100",
        method="GET",
        body=b"",
    )
    with pytest.raises(ValueError):
        outbound("/git/refs", body=b'{"ref":"refs/heads/main"}')


@pytest.mark.parametrize(
    "path",
    [
        "/pulls/7/merge",
        "/actions/workflows",
        "/actions/secrets",
        "/secrets",
        "/hooks",
        "/git/refs/heads/main",
        "/contents/.github/workflows/ci.yml",
        "/git/trees?extra=1",
    ],
)
def test_forbidden_routes(path):
    with pytest.raises(ValueError):
        outbound(path)


def test_wrong_repository_origin_token_or_absent_scope():
    valid = outbound()
    for url in [
        "https://api.github.com/repos/SignalOwner/other/git/trees",
        "https://other.example/repos/SignalOwner/website/git/trees",
        ROOT + "/../other/git/trees",
        ROOT + "/git/%74rees",
    ]:
        with pytest.raises(ValueError):
            replace(valid, http=replace(valid.http, url=url))
    for scope in [
        None,
        GitHubRepositoryWriteScope("SignalOwner", "website", "synthetic-different-token"),
    ]:
        with pytest.raises(ValueError):
            replace(valid, github_write_scope=scope)


@pytest.mark.parametrize(
    "body",
    [
        b"grant_type=refresh_token",
        b"<html>no</html>",
        b"[]",
        b'{"value":NaN}',
        b'{"value":Infinity}',
        b"x" * (128 * 1024 + 1),
    ],
)
def test_non_json_and_oversize_requests(body):
    with pytest.raises(ValueError):
        outbound(body=body)


def test_closed_media_time_headers_and_methods():
    valid = outbound()
    for http in [
        replace(valid.http, timeout_seconds=6),
        replace(valid.http, max_response_bytes=256 * 1024 + 1),
        replace(valid.http, accepted_media_types=("text/html",)),
        replace(
            valid.http,
            headers=tuple(
                (k, "text/plain" if k == "content-type" else v) for k, v in valid.http.headers
            ),
        ),
    ]:
        with pytest.raises(ValueError):
            replace(valid, http=http)
    for method in ["PUT", "PATCH", "DELETE"]:
        with pytest.raises(ValueError):
            outbound("/pulls/7/merge", method=method)


@pytest.mark.parametrize(
    "profile", [p for p in EgressProfile if p != EgressProfile.GITHUB_REPOSITORY_WRITE]
)
def test_other_profiles_reject_repository_mutations(profile):
    with pytest.raises(ValueError):
        outbound(profile=profile, scope=None)
