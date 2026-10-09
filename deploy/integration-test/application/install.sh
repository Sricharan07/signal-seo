#!/bin/sh
set -eu
test "$(id -u)" -eq 0
test "$#" -eq 1
source_directory=$1
cd "$(dirname "$0")"
test "$(findmnt -n -o FSTYPE /run)" = tmpfs
for name in environment.json environment.env dashboard-origin.json Caddyfile postgres-password migrator-password initialize.sql application.json pkce-writer.json pkce-consumer.json recovery-reader.json ca.pem application-database.pem application-database-key.pem api.pem api-key.pem dashboard.pem dashboard-key.pem; do
  test -f "$source_directory/$name"
  test ! -L "$source_directory/$name"
  test "$(stat -c %a "$source_directory/$name")" = 600
  test "$(stat -c %h "$source_directory/$name")" = 1
  test "$(stat -c %s "$source_directory/$name")" -le 65536
done
install -d -m 0700 /run/signal-application
for name in database api migration; do
  if test "$name" = database; then owner=70; else owner=10001; fi
  install -d -o "$owner" -g "$owner" -m 0700 "/run/signal-application/$name"
done
for name in postgres-password initialize.sql; do
  install -o 70 -g 70 -m 0400 "$source_directory/$name" "/run/signal-application/database/$name"
done
install -o 10001 -g 10001 -m 0400 "$source_directory/migrator-password" /run/signal-application/migration/migrator-password
for name in environment.json application.json pkce-writer.json pkce-consumer.json recovery-reader.json; do
  install -o 10001 -g 10001 -m 0400 "$source_directory/$name" "/run/signal-application/api/$name"
done
install -d -o 1000 -g 1000 -m 0700 /run/signal-application/ingress
install -o 1000 -g 1000 -m 0400 "$source_directory/Caddyfile" /run/signal-application/ingress/Caddyfile
install -m 0600 "$source_directory/environment.env" environment.env
install -d -o 1000 -g 1000 -m 0700 /run/signal-application/dashboard
install -o 1000 -g 1000 -m 0400 "$source_directory/dashboard-origin.json" /run/signal-application/dashboard/dashboard-origin.json
for name in application-database api dashboard; do
  case "$name" in application-database) owner=70;; api) owner=10001;; dashboard) owner=1000;; esac
  install -d -m 0750 -o root -g "$owner" "tls/$name"
  install -o "$owner" -g "$owner" -m 0400 "$source_directory/$name-key.pem" "tls/$name/$name-key.pem"
  install -o "$owner" -g "$owner" -m 0444 "$source_directory/$name.pem" "tls/$name/$name.pem"
  install -o "$owner" -g "$owner" -m 0444 "$source_directory/ca.pem" "tls/$name/ca.pem"
done
for network in signal-test-oidc signal-test-secrets; do
  if ! docker network inspect "$network" >/dev/null 2>&1; then
    docker network create --internal "$network" >/dev/null
  fi
done
install -d -m 0750 /opt/signal-integration/application
install -m 0555 deny-egress.sh /opt/signal-integration/application/deny-egress.sh
install -m 0644 signal-application-egress.service /etc/systemd/system/signal-application-egress.service
systemctl daemon-reload
systemctl enable --now signal-application-egress.service
chmod 0555 initialize-database.sh
printf '%s\n' 'Application secret material installed; images and migrations must be qualified separately.'
