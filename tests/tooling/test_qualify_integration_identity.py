import ssl

import httpx2
import pytest
import qualify_integration_identity as qualifier
from integration_secrets import OperatorError


def test_fixed_diagnostics_do_not_include_response_values():
    class Client:
        def request(self, method, url, **kwargs):
            assert method == "GET"
            assert url == qualifier.BASE + "/realms/signal"
            return httpx2.Response(200, content=b"sensitive" * 150000)

    with pytest.raises(OperatorError, match="bounded response") as caught:
        qualifier.request(Client(), "GET", "/realms/signal")
    assert "sensitive" not in str(caught.value)


def test_bounded_response_returned_without_redirect_following():
    response = httpx2.Response(302, headers={"location": "https://attacker.invalid"})

    class Client:
        def request(self, method, url, **kwargs):
            return response

    assert qualifier.request(Client(), "GET", "/realms/signal") is response


def test_certificate_failure_requires_actual_tls_validation_failure():
    assert qualifier.certificate_failure(ssl.SSLCertVerificationError())
    assert not qualifier.certificate_failure(ConnectionRefusedError())
    nested = RuntimeError()
    nested.__cause__ = ssl.SSLCertVerificationError()
    assert qualifier.certificate_failure(nested)
    nested.__cause__ = nested
    assert not qualifier.certificate_failure(nested)


def test_reject_check_reports_only_fixed_label():
    qualifier.require(True, "test")
    with pytest.raises(OperatorError, match="Identity check failed: test"):
        qualifier.require(False, "test")
