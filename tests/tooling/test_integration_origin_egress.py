import importlib.util
import json
import subprocess
from pathlib import Path

import pytest
from signal_core.crawl_urls import CrawlUrlRejected

PATH = Path(__file__).resolve().parents[2] / "scripts/integration_origin_egress.py"
spec = importlib.util.spec_from_file_location("integration_origin_egress", PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
ORIGIN = "https://signal-test.example.invalid"
ADDRESS = "93.184.216.34"


def test_exact_proof_pin_timer_and_network_shape(tmp_path):
    module.render(tmp_path, lambda *args: (ADDRESS,), now=1000)
    assert json.loads((tmp_path / "origin-pin.json").read_text()) == {
        "origin": ORIGIN,
        "address": ADDRESS,
        "issued_at": 1000,
        "expires_at": 4600,
    }
    script = (tmp_path / "origin-egress.sh").read_text()
    assert "sig-app" in script and "--dport 443" in script
    assert "--dport 80" not in script and "0.0.0.0/0" not in script
    assert "DOCKER-USER -i sig-app -d 93.184.216.34/32" in script
    assert "--ctstate NEW" in script
    assert "Persistent=true" in (tmp_path / "signal-origin-egress-expiry.timer").read_text()
    service = (tmp_path / "signal-origin-egress-expiry.service").read_text()
    assert "origin-egress.sh expire" in service
    subprocess.run(["/bin/sh", "-n", str(tmp_path / "origin-egress.sh")], check=True)
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in tmp_path.iterdir())


@pytest.mark.parametrize(
    "addresses", [(), ("127.0.0.1",), ("169.254.169.254",), ("8.8.8.8",), (ADDRESS, "10.0.0.1")]
)
def test_wrong_or_unsafe_resolution_writes_nothing(tmp_path, addresses):
    with pytest.raises((module.OperatorError, CrawlUrlRejected)):
        module.render(tmp_path, lambda *args: addresses, now=1000)
    assert not list(tmp_path.iterdir())


def test_existing_material_is_not_overwritten(tmp_path):
    owned = tmp_path / "owned"
    owned.write_text("fixture")
    with pytest.raises(module.OperatorError):
        module.render(tmp_path, lambda *args: (ADDRESS,), now=1000)
    assert owned.read_text() == "fixture"
