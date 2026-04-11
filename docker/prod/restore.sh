#!/bin/bash
set -euo pipefail

if [ -z "${1:-}" ]; then
    echo "Usage: ./restore.sh <backup_file.sql.gz>"
    echo ""
    echo "Available backups:"
    ls -lt /data/memodi/backups/memodi_*.sql.gz 2>/dev/null || echo "  No backups found"
    exit 1
fi

BACKUP_FILE="$1"

if [ ! -f "$BACKUP_FILE" ]; then
    echo "Error: File not found: $BACKUP_FILE"
    exit 1
fi

echo "Restoring from: $BACKUP_FILE"
echo "WARNING: This will overwrite the current database."
read -p "Continue? (y/N): " confirm

if [ "$confirm" != "y" ]; then
    echo "Aborted."
    exit 0
fi

gunzip -c "$BACKUP_FILE" | docker exec -i memodi-db psql \
    -U "$MEMODI_DB_USER" \
    -d "$MEMODI_DB_NAME"

echo "Restore complete."
