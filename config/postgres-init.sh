#!/bin/sh
# Runs once when the Postgres volume is first created.
# - authentik database for SSO
# - gateway database owned by the admin role; the Gateway runs as `gateway_app`, which is NOT
#   the table owner, so row-level security policies always apply to it.
set -e
APP_PW="$(cat /run/secrets/gateway_db_password)"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<SQL
CREATE DATABASE authentik;
CREATE DATABASE gateway;
CREATE ROLE gateway_app LOGIN PASSWORD '${APP_PW}';
GRANT CONNECT ON DATABASE gateway TO gateway_app;
SQL
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname gateway -c "CREATE EXTENSION IF NOT EXISTS vector;"
