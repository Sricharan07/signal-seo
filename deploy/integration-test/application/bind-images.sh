#!/bin/sh
set -eu
test "$(id -u)" -eq 0
cd "$(dirname "$0")"
api=$(docker image inspect signal-test-api:20260930 --format '{{.Id}}')
dashboard=$(docker image inspect signal-test-dashboard:20260930 --format '{{.Id}}')
ingress=$(docker image inspect signal-test-ingress:20260930 --format '{{.Id}}')
umask 077
printf 'SIGNAL_API_IMAGE=%s\nSIGNAL_DASHBOARD_IMAGE=%s\nSIGNAL_INGRESS_IMAGE=%s\n' "$api" "$dashboard" "$ingress" > .env
docker compose config --quiet
