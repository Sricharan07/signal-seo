"""Prepare one expiring public-address pin for the approved test-origin proof."""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from integration_environment import load_integration_scope

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/control_plane/src"))
from integration_secrets import OperatorError, private_directory, write_private  # noqa: E402
from signal_core.crawl_http import BoundedSystemResolver  # noqa: E402
from signal_core.crawl_urls import validate_public_addresses  # noqa: E402


def render(directory, resolver, *, now=None):
    scope = load_integration_scope()
    addresses = validate_public_addresses(resolver(scope.host, 443, 5))
    if addresses != (scope.public_ipv4,) or any(directory.iterdir()):
        raise OperatorError("The approved test-origin public address or destination changed.")
    issued = int(datetime.now(UTC).timestamp()) if now is None else now
    if type(issued) is not int or issued <= 0:
        raise OperatorError("Test-origin pin time rejected.")
    expires = issued + 3600
    write_private(
        directory / "origin-pin.json",
        json.dumps(
            {
                "origin": scope.origin,
                "address": scope.public_ipv4,
                "issued_at": issued,
                "expires_at": expires,
            }
        ).encode(),
    )
    deadline = datetime.fromtimestamp(expires, UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    write_private(
        directory / "origin-egress.sh",
        f"""#!/bin/sh
set -eu
mode=${{1:-install}}
case "$mode" in install|expire) ;; *) exit 1;; esac
deny="DOCKER-USER -i sig-app -d {scope.public_ipv4}/32 -p tcp --dport 443 -j DROP"
if test "$mode" = expire; then
  iptables -C $deny 2>/dev/null || \\
    iptables -I DOCKER-USER 1 -i sig-app -d {scope.public_ipv4}/32 -p tcp --dport 443 -j DROP
  exit 0
fi
test "$(date +%s)" -ge {issued}
test "$(date +%s)" -lt {expires}
subnet=$(docker network inspect signal-integration-application_ingress \\
  --format '{{{{(index .IPAM.Config 0).Subnet}}}}')
while iptables -C $deny 2>/dev/null; do iptables -D $deny; done
iptables -C DOCKER-USER -i sig-app -d {scope.public_ipv4}/32 -p tcp --dport 443 \\
  -m conntrack --ctstate NEW -j ACCEPT 2>/dev/null || \\
  iptables -I DOCKER-USER 1 -i sig-app -d {scope.public_ipv4}/32 -p tcp --dport 443 \\
    -m conntrack --ctstate NEW -j ACCEPT
iptables -t nat -C POSTROUTING -s "$subnet" -d {scope.public_ipv4}/32 -p tcp \\
  --dport 443 -j MASQUERADE 2>/dev/null || \\
  iptables -t nat -A POSTROUTING -s "$subnet" -d {scope.public_ipv4}/32 -p tcp \\
    --dport 443 -j MASQUERADE
""".encode(),
    )
    write_private(
        directory / "signal-origin-egress-expiry.service",
        b"""[Unit]
Description=Expire the dedicated test-origin public pin
After=docker.service
[Service]
Type=oneshot
ExecStart=/opt/signal-integration/application/origin-egress.sh expire
""",
    )
    write_private(
        directory / "signal-origin-egress-expiry.timer",
        f"""[Unit]
Description=Bound test-origin proof network admission
[Timer]
OnCalendar={deadline}
Persistent=true
[Install]
WantedBy=timers.target
""".encode(),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    try:
        render(private_directory(args.directory), BoundedSystemResolver(max_in_flight=1))
        print("Exact test-origin proof pin prepared; provider and administrative routes unchanged.")
        return 0
    except Exception as error:
        print(f"Test-origin pin preparation rejected ({type(error).__name__}); no rule changed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
