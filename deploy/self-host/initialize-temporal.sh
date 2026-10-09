#!/bin/sh
set -eu
psql -X -v ON_ERROR_STOP=1 --username postgres --dbname temporal -f /run/signal-database/initialize.sql >/dev/null
