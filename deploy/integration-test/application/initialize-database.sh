#!/bin/sh
set -eu
psql --username postgres --dbname signal --set ON_ERROR_STOP=1 --file /run/signal-secrets/initialize.sql
cp /run/signal-config/pg_hba.conf "$PGDATA/pg_hba.conf"
