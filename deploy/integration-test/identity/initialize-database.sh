#!/bin/sh
set -eu
KC_PASSWORD=$(cat /run/signal-secrets/keycloak-password)
export KC_PASSWORD
psql -v ON_ERROR_STOP=1 --username postgres --dbname keycloak <<'SQL'
\getenv kc_password KC_PASSWORD
CREATE ROLE signal_keycloak LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD :'kc_password';
ALTER DATABASE keycloak OWNER TO signal_keycloak;
REVOKE ALL ON DATABASE keycloak FROM PUBLIC;
SQL
unset KC_PASSWORD
cp /run/signal-config/pg_hba.conf "$PGDATA/pg_hba.conf"
