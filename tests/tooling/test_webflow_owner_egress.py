import pytest
from signal_core.crawl_http import EgressHttpRequest
from signal_core.egress_profiles import PROFILE_RULES, EgressProfile, profile_headers
from signal_core.shared_egress import SharedEgressRequest


@pytest.mark.parametrize(
    "path,allowed",
    [
        ("/robots.txt", True),
        ("/robots.txt?extra=1", False),
        ("/v2/token/introspect", False),
        ("/oauth/access_token", False),
    ],
)
def test_webflow_owner_robots_exception_is_one_exact_credential_free_get(path, allowed):
    profile = EgressProfile.OWNER_CONNECTOR_ROBOTS
    request = EgressHttpRequest(
        "GET",
        "https://api.webflow.com" + path,
        headers=profile_headers(profile, method="GET", authorization=None),
        accepted_media_types=PROFILE_RULES[profile].response_media_types,
        timeout_seconds=5,
        max_response_bytes=16384,
    )
    if allowed:
        assert not SharedEgressRequest("crawl", request, profile).credentialed
    else:
        with pytest.raises(ValueError):
            SharedEgressRequest("crawl", request, profile)
