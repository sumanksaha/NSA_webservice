#!/usr/bin/env bash
# Render Postgres -> Supabase Postgres migration (one-off).
#
# Usage:
#   RENDER_DATABASE_URL=postgresql://...@render.../db \
#   SUPABASE_DIRECT_URL='postgresql://postgres.[ref]:[pw]@aws-0-[region].pooler.supabase.com:5432/postgres' \
#   bash scripts/migrate_render_to_supabase.sh
#
# Credentials come from env vars only — never pass URLs on the CLI
# (they leak into shell history / ps output). Do NOT use the Supabase
# POOLED (6543) URL here; pgbouncer transaction mode breaks pg_restore
# and migrations.
set -euo pipefail

: "${RENDER_DATABASE_URL:?set RENDER_DATABASE_URL}"
: "${SUPABASE_DIRECT_URL:?set SUPABASE_DIRECT_URL (port 5432)}"

DUMP="$(mktemp -d)/render.dump"

echo "== 1. Row counts BEFORE (Render) =="
psql "$RENDER_DATABASE_URL" -Atc \
  "SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY relname;" | tee /tmp/rows_before.txt

echo "== 2. Dump Render (schema + data, custom format) =="
pg_dump "$RENDER_DATABASE_URL" --format=custom --no-owner --no-privileges --file "$DUMP"

echo "== 3. Drop existing objects in Supabase target (fresh start), then restore =="
# public schema drop is the reliable way to clear partial schemas; extensions stay.
psql "$SUPABASE_DIRECT_URL" -c 'DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;'
pg_restore --dbname "$SUPABASE_DIRECT_URL" --no-owner --no-privileges --exit-on-error "$DUMP"

echo "== 4. Row counts AFTER (Supabase) =="
psql "$SUPABASE_DIRECT_URL" -Atc \
  "SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY relname;" | tee /tmp/rows_after.txt

echo "== 5. Diff (run ANALYZE first so n_live_tup is accurate) =="
psql "$SUPABASE_DIRECT_URL" -c 'ANALYZE;' >/dev/null
diff /tmp/rows_before.txt /tmp/rows_after.txt && echo "ROW COUNTS MATCH" || echo "MISMATCH — investigate above"

rm -rf "$(dirname "$DUMP")"
