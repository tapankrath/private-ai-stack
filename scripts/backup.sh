#!/usr/bin/env bash
# Encrypted backup of everything the stack stores, with rotation.
#   RESTIC_REPOSITORY      local path or offline drive, e.g. /mnt/backup/private-ai
#   RESTIC_PASSWORD_FILE   keep a copy of this password OFFLINE (paper / safe); without it backups are unreadable
#   INCLUDE_MODELS=1       also back up model files (needed for air-gapped sites; they are large)
# Rotation: deleted data disappears from backups once the snapshots holding it are pruned.
set -euo pipefail
: "${RESTIC_REPOSITORY:?set RESTIC_REPOSITORY}"
: "${RESTIC_PASSWORD_FILE:?set RESTIC_PASSWORD_FILE}"
export RESTIC_REPOSITORY RESTIC_PASSWORD_FILE
PROJECT="${COMPOSE_PROJECT_NAME:-private-ai}"
TAG="private-ai"

restic snapshots >/dev/null 2>&1 || restic init

# 1. Consistent database copies
if docker compose ps --services --status running | grep -qx postgres; then
  docker compose exec -T postgres pg_dumpall -U assistant | restic backup --tag "$TAG" --stdin --stdin-filename postgres.sql
fi
if docker compose ps --services --status running | grep -qx gateway; then
  docker compose exec -T gateway python -c "import sqlite3,os
p='/data/gateway.db'
if os.path.exists(p):
    s=sqlite3.connect(p); d=sqlite3.connect('/data/gateway.backup.db'); s.backup(d); d.close()" || true
fi

# 2. Volumes (read from their host mountpoints; run as root)
paths=()
for v in $(docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT"); do
  case "$v" in
    *ollama-models*|*hf-cache*|*whisper-models*) [ "${INCLUDE_MODELS:-0}" = "1" ] || continue ;;
    *postgres-data*) continue ;;   # covered by pg_dumpall above
  esac
  paths+=("$(docker volume inspect -f '{{.Mountpoint}}' "$v")")
done
paths+=(config generated profile.yaml)
restic backup --tag "$TAG" --exclude '*.wal' "${paths[@]}"

# 3. Rotation
restic forget --tag "$TAG" --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune
restic check --read-data-subset=5%
echo "Backup complete. Run scripts/restore-drill.sh at least quarterly."
