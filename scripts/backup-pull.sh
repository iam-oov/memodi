#!/usr/bin/env bash
set -euo pipefail

pi="${MEMODI_PI:?set MEMODI_PI, e.g. memodi@192.168.1.50}"
src_dir="${MEMODI_PI_BACKUP_DIR:-backups}"
dest_dir="${MEMODI_BACKUP_DIR:-$HOME/memodi-backups}"

mkdir -p "$dest_dir"
rsync -a --exclude '*.tmp' "${pi}:${src_dir}/" "${dest_dir}/"
