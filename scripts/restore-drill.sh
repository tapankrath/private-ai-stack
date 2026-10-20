#!/usr/bin/env bash
# Restore drill: restores the latest snapshot to a SCRATCH folder and checks it.
# Never touches the running stack. Run quarterly and record the result in SECURITY_CHECKLIST.md.
set -euo pipefail
: "${RESTIC_REPOSITORY:?set RESTIC_REPOSITORY}"
: "${RESTIC_PASSWORD_FILE:?set RESTIC_PASSWORD_FILE}"
export RESTIC_REPOSITORY RESTIC_PASSWORD_FILE
TARGET="${1:-./restore-drill-$(date +%Y%m%d-%H%M)}"
mkdir -p "$TARGET"
restic restore latest --tag private-ai --target "$TARGET"
fail=0
db=$(find "$TARGET" -name gateway.backup.db -o -name gateway.db | head -1 || true)
if [ -n "$db" ]; then
  res=$(python3 -c "import sqlite3,sys;c=sqlite3.connect(sys.argv[1]);print(c.execute('PRAGMA integrity_check').fetchone()[0], c.execute('select count(*) from messages').fetchone()[0])" "$db")
  echo "SQLite: $res"; case "$res" in ok*) ;; *) fail=1 ;; esac
fi
sql=$(find "$TARGET" -name postgres.sql | head -1 || true)
if [ -n "$sql" ]; then
  if grep -q "CREATE TABLE" "$sql"; then echo "Postgres dump: $(grep -c 'CREATE TABLE' "$sql") tables"; else echo "Postgres dump looks empty"; fail=1; fi
fi
[ -d "$TARGET" ] && echo "Files restored: $(find "$TARGET" -type f | wc -l)"
if [ "$fail" = 0 ]; then echo "RESTORE DRILL PASSED ($TARGET). Delete the folder when done."; else echo "RESTORE DRILL FAILED"; exit 1; fi
