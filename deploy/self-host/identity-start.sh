#!/bin/bash
set -euo pipefail
KC_DB_PASSWORD=$(</run/signal-identity/database-password)
export KC_DB_PASSWORD
if test -s /run/signal-identity/bootstrap-password; then
  KC_BOOTSTRAP_ADMIN_USERNAME=signal-self-host-operator
  KC_BOOTSTRAP_ADMIN_PASSWORD=$(</run/signal-identity/bootstrap-password)
  export KC_BOOTSTRAP_ADMIN_USERNAME KC_BOOTSTRAP_ADMIN_PASSWORD
fi
exec /opt/keycloak/bin/kc.sh start --optimized --import-realm
