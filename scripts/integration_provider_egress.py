"""Prepare screened, expiring numeric pins for the exact dedicated connectors."""

import argparse
import json
import sys
from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import Path

from integration_secrets import OperatorError, private_directory, write_private

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/control_plane/src"))
from signal_core.crawl_http import BoundedSystemResolver  # noqa: E402
from signal_core.crawl_urls import validate_public_addresses  # noqa: E402

HOSTS = ("api.github.com", "oauth2.googleapis.com", "www.googleapis.com", "slack.com")


def render(directory, resolver, *, now=None):
    if any(directory.iterdir()):
        raise OperatorError("Use an empty protected provider pin destination.")
    hosts = {}
    for host in HOSTS:
        addresses = validate_public_addresses(resolver(host, 443, 5))
        ipv4 = tuple(str(address) for address in addresses if ip_address(address).version == 4)
        if not ipv4:
            raise OperatorError("Provider public IPv4 unavailable.")
        hosts[host] = ipv4[0]
    issued = int(datetime.now(UTC).timestamp()) if now is None else now
    if type(issued) is not int or issued <= 0:
        raise OperatorError("Provider pin time rejected.")
    expires = issued + 3600
    write_private(
        directory / "provider-pins.json",
        json.dumps(
            {
                "hosts": hosts,
                "issued_at": issued,
                "expires_at": expires,
            }
        ).encode(),
    )
    script = [
        "#!/bin/sh",
        "set -eu",
        "mode=${1:-install}",
        'case "$mode" in install|expire) ;; *) exit 1;; esac',
        'if test "$mode" = install; then',
        f'  test "$(date +%s)" -ge {issued}',
        f'  test "$(date +%s)" -lt {expires}',
        "  source=$(docker inspect signal-integration-application-api-1 --format "
        "'{{(index .NetworkSettings.Networks "
        '"signal-integration-application_ingress").IPAddress}}\')',
        '  case "$source" in 172.*|192.168.*|10.*) ;; *) exit 1;; esac',
        "fi",
    ]
    for address in sorted(set(hosts.values())):
        deny = f"DOCKER-USER -i sig-app -d {address}/32 -p tcp --dport 443 -j DROP"
        allow = (
            f'DOCKER-USER -i sig-app -s "$source/32" -d {address}/32 -p tcp '
            "--dport 443 -m conntrack --ctstate NEW -j ACCEPT"
        )
        nat = f'POSTROUTING -s "$source/32" -d {address}/32 -p tcp --dport 443 -j MASQUERADE'
        script.extend(
            [
                'if test "$mode" = expire; then',
                f"  iptables -C {deny} 2>/dev/null || "
                f"iptables -I {deny.replace('DOCKER-USER', 'DOCKER-USER 1', 1)}",
                "else",
                f"  while iptables -C {deny} 2>/dev/null; do iptables -D {deny}; done",
                f"  iptables -C {allow} 2>/dev/null || "
                f"iptables -I {allow.replace('DOCKER-USER', 'DOCKER-USER 1', 1)}",
                f"  iptables -t nat -C {nat} 2>/dev/null || iptables -t nat -A {nat}",
                "fi",
            ]
        )
    write_private(directory / "provider-egress.sh", ("\n".join(script) + "\n").encode())
    write_private(
        directory / "signal-provider-egress-expiry.service",
        b"""[Unit]
Description=Expire dedicated test provider pins
After=docker.service
[Service]
Type=oneshot
ExecStart=/opt/signal-integration/application/provider-egress.sh expire
""",
    )
    deadline = datetime.fromtimestamp(expires, UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    write_private(
        directory / "signal-provider-egress-expiry.timer",
        f"""[Unit]
Description=Bound dedicated test connector network admission
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
        print("Exact screened provider pins prepared; no firewall or credential changed.")
        return 0
    except Exception as error:
        print(f"Provider pins rejected ({type(error).__name__}); no network rule changed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
