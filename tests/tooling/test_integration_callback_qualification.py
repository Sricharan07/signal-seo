import httpx2
import pytest
from integration_secrets import OperatorError
from qualify_integration_application import rejected_callback

ORIGIN = "https://signal-test.example.invalid"

DESTINATION = ORIGIN + "/connectors?gsc=unavailable"
COOKIE = "__Host-signal-gsc-attempt"


def response(**headers):
    return httpx2.Response(
        303,
        headers={
            "location": DESTINATION,
            "referrer-policy": "no-referrer",
            "cache-control": "no-store",
            "set-cookie": COOKIE + "=; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0",
            **headers,
        },
    )


def test_rejected_callback_requires_fixed_denial_and_only_expired_correlation_cookie():
    rejected_callback(response(), DESTINATION, COOKIE)


@pytest.mark.parametrize(
    "headers",
    [
        {"location": "https://attacker.invalid/"},
        {"location": ORIGIN + "/connectors?gsc=bound"},
        {"referrer-policy": "same-origin"},
        {"cache-control": "public"},
        {"content-disposition": "attachment"},
        {"set-cookie": COOKIE + "=active; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=600"},
        {"set-cookie": COOKIE + "=; Path=/; SameSite=Lax; Max-Age=0"},
    ],
)
def test_callback_qualification_rejects_redirect_cache_download_and_cookie_failures(headers):
    with pytest.raises(OperatorError):
        rejected_callback(response(**headers), DESTINATION, COOKIE)


@pytest.mark.parametrize("status", [200, 404, 503])
def test_callback_qualification_rejects_missing_or_unavailable_route(status):
    value = response()
    value.status_code = status
    with pytest.raises(OperatorError):
        rejected_callback(value, DESTINATION, COOKIE)


def test_callback_qualification_rejects_an_extra_cookie():
    value = response()
    value.headers = httpx2.Headers(
        [*value.headers.multi_items(), ("set-cookie", "__Host-signal_tenant=unexpected; Secure")]
    )
    with pytest.raises(OperatorError):
        rejected_callback(value, DESTINATION, COOKIE)
