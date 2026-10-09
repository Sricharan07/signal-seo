#!/bin/bash
set -euo pipefail
test -s /run/signal-secrets/keycloak-password
KC_DB_PASSWORD=$(</run/signal-secrets/keycloak-password)
export KC_DB_PASSWORD
if test -s /run/signal-secrets/bootstrap-password; then
  KC_BOOTSTRAP_ADMIN_USERNAME=signal-test-operator
  KC_BOOTSTRAP_ADMIN_PASSWORD=$(</run/signal-secrets/bootstrap-password)
  export KC_BOOTSTRAP_ADMIN_USERNAME KC_BOOTSTRAP_ADMIN_PASSWORD
fi
exec /opt/keycloak/bin/kc.sh start --optimized --import-realm
