import json
import subprocess

import pytest
from integration_provider_egress import HOSTS, render
from integration_secrets import OperatorError
from signal_core.crawl_urls import CrawlUrlRejected


def test_provider_window_is_screened_api_only_and_persistently_expires(tmp_path):
    render(tmp_path, lambda *args: ("93.184.216.34",), now=1000)
    pin = json.loads((tmp_path / "provider-pins.json").read_text())
    assert set(pin["hosts"]) == set(HOSTS)
    assert pin["issued_at"] == 1000 and pin["expires_at"] == 4600
    script = (tmp_path / "provider-egress.sh").read_text()
    assert "source=$(docker inspect signal-integration-application-api-1" in script
    assert '-s "$source/32"' in script and "--ctstate NEW" in script
    assert "-j DROP" in script and "--dport 443" in script
    assert "0.0.0.0/0" not in script and "--dport 80" not in script
    assert "Persistent=true" in (tmp_path / "signal-provider-egress-expiry.timer").read_text()
    subprocess.run(["/bin/sh", "-n", str(tmp_path / "provider-egress.sh")], check=True)
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in tmp_path.iterdir())


@pytest.mark.parametrize(
    "addresses",
    [
        (),
        ("127.0.0.1",),
        ("169.254.169.254",),
        ("93.184.216.34", "10.0.0.1"),
        ("2606:4700:4700::1111",),
    ],
)
def test_unsafe_or_ipv4_unavailable_fails_without_files(tmp_path, addresses):
    with pytest.raises((OperatorError, CrawlUrlRejected)):
        render(tmp_path, lambda *args: addresses, now=1000)
    assert not list(tmp_path.iterdir())
