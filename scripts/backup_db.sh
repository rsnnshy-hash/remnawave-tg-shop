#!/bin/bash
# =============================================================================
# Database Backup Script for remnawave-tg-shop
# =============================================================================
# Usage: ./backup_db.sh [backup_dir] [retention_days]
#
# This script creates a compressed backup of the PostgreSQL database
# and optionally removes old backups based on retention policy.
#
# Add to crontab for automatic daily backups:
#   0 3 * * * /path/to/scripts/backup_db.sh /path/to/backups 30
# =============================================================================

set -e

# Configuration
CONTAINER_NAME="${DB_CONTAINER_NAME:-remnawave-tg-shop-db}"
BACKUP_DIR="${1:-./backups}"
RETENTION_DAYS="${2:-30}"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="backup_${DATE}.sql.gz"

# Load environment variables if .env exists
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/../.env"
if [ -f "$ENV_FILE" ]; then
    export $(grep -E '^POSTGRES_' "$ENV_FILE" | xargs)
fi

# Default values if not set
POSTGRES_USER="${POSTGRES_USER:-user}"
POSTGRES_DB="${POSTGRES_DB:-vpn_shop_db}"

echo "=== Database Backup Started at $(date) ==="
echo "Container: $CONTAINER_NAME"
echo "Database: $POSTGRES_DB"
echo "Backup directory: $BACKUP_DIR"

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

# Check if container is running
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "ERROR: Container '$CONTAINER_NAME' is not running"
    exit 1
fi

# Create backup
echo "Creating backup: $BACKUP_FILE"
docker exec "$CONTAINER_NAME" pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" | gzip > "${BACKUP_DIR}/${BACKUP_FILE}"

# Verify backup was created
if [ -f "${BACKUP_DIR}/${BACKUP_FILE}" ]; then
    BACKUP_SIZE=$(du -h "${BACKUP_DIR}/${BACKUP_FILE}" | cut -f1)
    echo "Backup created successfully: ${BACKUP_FILE} (${BACKUP_SIZE})"
else
    echo "ERROR: Backup file was not created"
    exit 1
fi

# Remove old backups
echo "Removing backups older than $RETENTION_DAYS days..."
find "$BACKUP_DIR" -name "backup_*.sql.gz" -type f -mtime +$RETENTION_DAYS -delete 2>/dev/null || true

# Count remaining backups
BACKUP_COUNT=$(find "$BACKUP_DIR" -name "backup_*.sql.gz" -type f | wc -l)
echo "Total backups: $BACKUP_COUNT"

echo "=== Backup Completed at $(date) ==="
