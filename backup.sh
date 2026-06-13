#!/bin/bash
# Backup PostgreSQL data + uploaded PDF files. Keeps 30 days of backups.
set -euo pipefail

DATE=$(date +%Y%m%d_%H%M)
BACKUP_DIR=${BACKUP_DIR:-/backups}
PG_CONTAINER=${PG_CONTAINER:-pageserve-server-postgres-1}
PG_USER=${POSTGRES_USER:-pageserve}
PG_DB=${POSTGRES_DB:-pageserve}
FILES_VOLUME=${FILES_VOLUME:-pageserve-server_pdf_files}

mkdir -p "$BACKUP_DIR"

# Backup PostgreSQL
docker exec "$PG_CONTAINER" pg_dump -U "$PG_USER" "$PG_DB" \
  | gzip > "$BACKUP_DIR/pageserve_${DATE}.sql.gz"

# Backup PDF files
tar -czf "$BACKUP_DIR/files_${DATE}.tar.gz" \
  -C "$(docker volume inspect "$FILES_VOLUME" --format '{{.Mountpoint}}')" .

# Keep 30 days
find "$BACKUP_DIR" -mtime +30 -delete

echo "Backup ${DATE} completed"
