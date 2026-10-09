"""Prepare an expiring, exact Google identity-only network pin set."""

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/control_plane/src"))
from integration_secrets import OperatorError, private_directory, write_private  # noqa: E402
from signal_core.crawl_http import BoundedSystemResolver  # noqa: E402
from signal_core.crawl_urls import validate_public_addresses  # noqa: E402

HOSTS = (
    "accounts.google.com",
    "oauth2.googleapis.com",
    "www.googleapis.com",
    "openidconnect.googleapis.com",
)


def pins(resolver):
    result = {}
    for host in HOSTS:
        addresses = validate_public_addresses(resolver(host, 443, 5))
        ipv4 = [str(address) for address in addresses if ip_address(address).version == 4]
        if not ipv4:
            raise OperatorError("Google identity IPv4 pin is unavailable.")
        result[host] = ipv4[0]
    return result


def render(directory: Path, addresses: dict[str, str]):
    if set(addresses) != set(HOSTS) or any(
        ip_address(value).version != 4 or not ip_address(value).is_global
        for value in addresses.values()
    ):
        raise OperatorError("Google identity pin set rejected.")
    validate_public_addresses(tuple(addresses.values()))
    expires = int((datetime.now(UTC) + timedelta(hours=1)).timestamp())
    document = {
        "services": {
            "identity": {
                "extra_hosts": [f"{host}:{addresses[host]}" for host in HOSTS],
                "environment": {
                    "KC_SPI_CONNECTIONS_HTTP_CLIENT__DEFAULT__"
                    "ESTABLISH_CONNECTION_TIMEOUT_MILLIS": "5000",
                    "KC_SPI_CONNECTIONS_HTTP_CLIENT__DEFAULT__SOCKET_TIMEOUT_MILLIS": "5000",
                    "KC_SPI_CONNECTIONS_HTTP_CLIENT__DEFAULT__"
                    "CONNECTION_REQUEST_TIMEOUT_MILLIS": "5000",
                    "KC_SPI_CONNECTIONS_HTTP_CLIENT__DEFAULT__CONNECTION_POOL_SIZE": "8",
                    "KC_SPI_CONNECTIONS_HTTP_CLIENT__DEFAULT__MAX_POOLED_PER_ROUTE": "4",
                    "KC_SPI_CONNECTIONS_HTTP_CLIENT__DEFAULT__ALLOW_REDIRECTS": "false",
                    "KC_SPI_CONNECTIONS_HTTP_CLIENT__DEFAULT__MAX_RETRIES": "0",
                    "KC_SPI_CONNECTIONS_HTTP_CLIENT__DEFAULT__CONNECTION_TTL_MILLIS": "30000",
                },
            }
        }
    }
    write_private(directory / "google-pins.json", json.dumps(document).encode())
    # The existing bridge drop remains in force. Only these pinned peers gain NAT.
    script = [
        "#!/bin/sh",
        "set -eu",
        "mode=${1:-install}",
        'case "$mode" in install|expire) ;; *) exit 1;; esac',
        'if test "$mode" = install; then',
        f'  test "$(date +%s)" -lt {expires}',
        "fi",
        "subnet=$(docker network inspect signal-integration-identity_identity "
        "--format '{{(index .IPAM.Config 0).Subnet}}')",
    ]
    for address in sorted(set(addresses.values())):
        allow = (
            f"DOCKER-USER -i sig-identity -d {address}/32 -p tcp --dport 443 "
            "-m conntrack --ctstate NEW -j ACCEPT"
        )
        deny = f"DOCKER-USER -i sig-identity -d {address}/32 -p tcp --dport 443 -j DROP"
        nat = f'POSTROUTING -s "$subnet" -d {address}/32 -p tcp --dport 443 -j MASQUERADE'
        inserted = allow.replace("DOCKER-USER", "DOCKER-USER 1", 1)
        inserted_deny = deny.replace("DOCKER-USER", "DOCKER-USER 1", 1)
        script.extend(
            [
                'if test "$mode" = expire; then',
                f"  iptables -C {deny} 2>/dev/null || iptables -I {inserted_deny}",
                "else",
                f"  while iptables -C {deny} 2>/dev/null; do iptables -D {deny}; done",
                f"  iptables -C {allow} 2>/dev/null || iptables -I {inserted}",
                f"  iptables -t nat -C {nat} 2>/dev/null || iptables -t nat -A {nat}",
                "fi",
            ]
        )
    write_private(directory / "google-egress.sh", ("\n".join(script) + "\n").encode())
    deadline = datetime.fromtimestamp(expires, UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    write_private(
        directory / "signal-google-egress-expiry.service",
        (
            b"[Unit]\nDescription=Expire the test Google identity pins\nAfter=docker.service\n"
            b"[Service]\nType=oneshot\n"
            b"ExecStart=/opt/signal-integration/identity/google-egress.sh expire\n"
        ),
    )
    write_private(
        directory / "signal-google-egress-expiry.timer",
        (
            "[Unit]\nDescription=Bound test Google identity network admission\n"
            f"[Timer]\nOnCalendar={deadline}\nPersistent=true\n"
            "[Install]\nWantedBy=timers.target\n"
        ).encode(),
    )
    write_private(
        directory / "google-pins-evidence.json",
        json.dumps(
            {
                "hosts": addresses,
                "expires_at": expires,
                "provider_data_connectors": "DISABLED",
            }
        ).encode(),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    try:
        render(private_directory(args.directory), pins(BoundedSystemResolver(max_in_flight=1)))
        print("Exact public Google identity pins prepared; no product connector route opened.")
        return 0
    except Exception as error:
        print(
            f"Identity pin preparation rejected ({type(error).__name__}); no network rule changed."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
