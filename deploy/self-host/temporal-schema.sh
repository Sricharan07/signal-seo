#!/bin/sh
set -eu
export SQL_PASSWORD="$(cat /run/signal-temporal/database-password)"
export SQL_HOST=temporal-database SQL_USER=signal_temporal SQL_PLUGIN=postgres12 SQL_PORT=5432
export SQL_TLS=true SQL_TLS_CA_FILE=/run/signal-temporal/tls/ca.pem SQL_TLS_SERVER_NAME=temporal-database
export TEMPORAL_ADDRESS=temporal:7233 TEMPORAL_TLS=true
export TEMPORAL_TLS_CA=/run/signal-temporal/tls/ca.pem
export TEMPORAL_TLS_CERT=/run/signal-temporal/tls/client.pem TEMPORAL_TLS_KEY=/run/signal-temporal/tls/client-key.pem
export TEMPORAL_TLS_SERVER_NAME=temporal
case "$1" in
  schema)
    for database in temporal temporal_visibility; do
      export SQL_DATABASE="$database"
      temporal-sql-tool setup-schema -v 0.0 >/dev/null 2>&1
      if test "$database" = temporal; then
        directory=/etc/temporal/schema/postgresql/v12/temporal/versioned
      else
        directory=/etc/temporal/schema/postgresql/v12/visibility/versioned
      fi
      temporal-sql-tool update-schema -d "$directory" >/dev/null 2>&1
    done
    ;;
  namespace)
    temporal operator namespace describe --namespace signal >/dev/null 2>&1 ||
      temporal operator namespace create --namespace signal --retention 7d >/dev/null 2>&1
    ;;
  *) exit 1 ;;
esac
