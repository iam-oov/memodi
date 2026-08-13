#!/usr/bin/env bash
set -euo pipefail

backup_dir="${MEMODI_BACKUP_DIR:-$HOME/backups}"
retention_days="${MEMODI_BACKUP_RETENTION_DAYS:-7}"
dump_file="${backup_dir}/memodi-$(date +%F).dump"

mkdir -p "$backup_dir"
pg_dump -Fc -n public -f "${dump_file}.tmp" memodi
mv "${dump_file}.tmp" "$dump_file"
find "$backup_dir" -name 'memodi-*.dump' -mtime +"$retention_days" -delete
