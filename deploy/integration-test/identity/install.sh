#!/bin/sh
set -eu
test "$(id -u)" -eq 0
test "$#" -eq 1
source_directory=$1
cd "$(dirname "$0")"
test "$(findmnt -n -o FSTYPE /run)" = tmpfs
for name in environment.env realm.json postgres-password keycloak-password bootstrap-password signal_google ca.pem database.pem database-key.pem identity.pem identity-key.pem; do
  test -f "$source_directory/$name"
  test ! -L "$source_directory/$name"
  test "$(stat -c %a "$source_directory/$name")" = 600
  test "$(stat -c %h "$source_directory/$name")" = 1
  test "$(stat -c %s "$source_directory/$name")" -le 65536
done
install -d -m 0700 /run/signal-identity
install -d -o 70 -g 70 -m 0700 /run/signal-identity/database
install -d -o 1000 -g 1000 -m 0700 /run/signal-identity/keycloak
install -d -o 1000 -g 1000 -m 0700 /run/signal-identity/keycloak/vault
install -o 70 -g 70 -m 0400 "$source_directory/postgres-password" /run/signal-identity/database/postgres-password
install -o 70 -g 70 -m 0400 "$source_directory/keycloak-password" /run/signal-identity/database/keycloak-password
install -o 1000 -g 1000 -m 0400 "$source_directory/keycloak-password" /run/signal-identity/keycloak/keycloak-password
install -o 1000 -g 1000 -m 0400 "$source_directory/bootstrap-password" /run/signal-identity/keycloak/bootstrap-password
install -o 1000 -g 1000 -m 0400 "$source_directory/signal_google" /run/signal-identity/keycloak/vault/signal_google
install -o 1000 -g 1000 -m 0400 "$source_directory/realm.json" /run/signal-identity/keycloak/realm.json
install -m 0600 "$source_directory/environment.env" environment.env
install -d -m 0750 -o root -g 70 tls/database
install -d -m 0750 -o root -g 1000 tls/identity
for name in database identity; do
  if test "$name" = database; then owner=70; else owner=1000; fi
  install -o "$owner" -g "$owner" -m 0400 "$source_directory/$name-key.pem" "tls/$name/$name-key.pem"
  install -o "$owner" -g "$owner" -m 0444 "$source_directory/$name.pem" "tls/$name/$name.pem"
  install -o "$owner" -g "$owner" -m 0444 "$source_directory/ca.pem" "tls/$name/ca.pem"
done
chmod 0555 initialize-database.sh start.sh
install -d -m 0750 /opt/signal-integration/identity
install -m 0555 deny-egress.sh /opt/signal-integration/identity/deny-egress.sh
install -m 0644 signal-identity-egress.service /etc/systemd/system/signal-identity-egress.service
systemctl daemon-reload
systemctl enable signal-identity-egress.service
systemctl restart signal-identity-egress.service
docker image pull postgres:17.11-alpine@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73
SIGNAL_IDENTITY_IMAGE=signal-integration-keycloak:26.7.3
export SIGNAL_IDENTITY_IMAGE
docker compose --env-file environment.env build identity
SIGNAL_IDENTITY_IMAGE=$(docker image inspect signal-integration-keycloak:26.7.3 --format '{{.Id}}')
export SIGNAL_IDENTITY_IMAGE
if ! docker network inspect signal-test-oidc >/dev/null 2>&1; then
  docker network create --internal signal-test-oidc >/dev/null
fi
umask 077
printf 'SIGNAL_IDENTITY_IMAGE=%s\n' "$SIGNAL_IDENTITY_IMAGE" > .env
docker compose --env-file .env --env-file environment.env up -d
printf '%s\n' 'Private identity deployment started; qualification and public ingress remain separate.'
