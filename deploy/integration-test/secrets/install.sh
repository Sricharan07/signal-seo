#!/bin/sh
set -eu
umask 077

# Run as the SSH operator after copying this profile and generated TLS files.
staging="$HOME/signal-secrets-staging"
destination=/opt/signal-integration/secrets
image=ghcr.io/openbao/openbao:2.6.1@sha256:5b2486ab0fb90bbc788cc345b0a08616dfb375873ee8be5df3a2fd4d378a67e0

test -f "$staging/compose.yaml"
test -f "$staging/server.hcl"
test -f "$staging/server.pem"
test -f "$staging/server-key.pem"
test -f "$staging/ca.pem"
sudo docker image inspect "$image" >/dev/null
sudo install -d -m 0750 "$destination"
sudo install -d -o 100 -g 1000 -m 0500 "$destination/tls"
sudo install -m 0644 "$staging/compose.yaml" "$destination/compose.yaml"
sudo install -m 0644 "$staging/server.hcl" "$destination/server.hcl"
sudo install -m 0755 "$staging/deny-egress.sh" "$destination/deny-egress.sh"
sudo install -m 0644 "$staging/signal-secrets-egress.service" /etc/systemd/system/signal-secrets-egress.service
sudo install -o 100 -g 1000 -m 0400 "$staging/server-key.pem" "$destination/tls/server-key.pem"
sudo install -o 100 -g 1000 -m 0400 "$staging/server.pem" "$destination/tls/server.pem"
sudo install -o 100 -g 1000 -m 0400 "$staging/ca.pem" "$destination/tls/ca.pem"
if ! sudo docker network inspect signal-test-secrets >/dev/null 2>&1; then
  sudo docker network create --internal signal-test-secrets >/dev/null
fi
sudo docker compose -f "$destination/compose.yaml" config --quiet
sudo systemctl daemon-reload
sudo systemctl enable --now signal-secrets-egress.service
sudo docker compose -f "$destination/compose.yaml" up --detach
rm -f "$staging/server-key.pem"
