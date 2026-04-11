#!/bin/bash
set -euo pipefail

BACKUP_DIR="/data/memodi/backups"
RETENTION_DAYS=7
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

mkdir -p "$BACKUP_DIR"

echo "Starting backup: $TIMESTAMP"

docker exec memodi-db pg_dump \
    -U "$MEMODI_DB_USER" \
    -d "$MEMODI_DB_NAME" \
    --no-owner \
    --no-privileges \
    | gzip > "$BACKUP_DIR/memodi_$TIMESTAMP.sql.gz"

echo "Backup saved: $BACKUP_DIR/memodi_$TIMESTAMP.sql.gz"

# Remove backups older than retention period
find "$BACKUP_DIR" -name "memodi_*.sql.gz" -mtime +$RETENTION_DAYS -delete

echo "Cleaned backups older than $RETENTION_DAYS days"
