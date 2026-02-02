#!/bin/bash
# =============================================================================
# Database Restore Script for remnawave-tg-shop
# =============================================================================
# Usage: ./restore_db.sh <backup_file>
#
# This script restores the PostgreSQL database from a backup file.
# WARNING: This will overwrite all current data in the database!
# =============================================================================

set -e

# Configuration
CONTAINER_NAME="${DB_CONTAINER_NAME:-remnawave-tg-shop-db}"
BACKUP_FILE="$1"

if [ -z "$BACKUP_FILE" ]; then
    echo "Usage: $0 <backup_file>"
    echo "Example: $0 ./backups/backup_20240101_030000.sql.gz"
    exit 1
fi

if [ ! -f "$BACKUP_FILE" ]; then
    echo "ERROR: Backup file not found: $BACKUP_FILE"
    exit 1
fi

# Load environment variables if .env exists
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/../.env"
if [ -f "$ENV_FILE" ]; then
    export $(grep -E '^POSTGRES_' "$ENV_FILE" | xargs)
fi

# Default values if not set
POSTGRES_USER="${POSTGRES_USER:-user}"
POSTGRES_DB="${POSTGRES_DB:-vpn_shop_db}"

echo "=== Database Restore Started at $(date) ==="
echo "Container: $CONTAINER_NAME"
echo "Database: $POSTGRES_DB"
echo "Backup file: $BACKUP_FILE"
echo ""
echo "WARNING: This will OVERWRITE all current data in the database!"
read -p "Are you sure you want to continue? (yes/no): " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
    echo "Restore cancelled."
    exit 0
fi

# Check if container is running
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "ERROR: Container '$CONTAINER_NAME' is not running"
    exit 1
fi

echo "Stopping bot container to prevent conflicts..."
docker stop remnawave-tg-shop 2>/dev/null || true

echo "Dropping and recreating database..."
docker exec "$CONTAINER_NAME" psql -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS ${POSTGRES_DB};"
docker exec "$CONTAINER_NAME" psql -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE ${POSTGRES_DB};"

echo "Restoring from backup..."
if [[ "$BACKUP_FILE" == *.gz ]]; then
    gunzip -c "$BACKUP_FILE" | docker exec -i "$CONTAINER_NAME" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
else
    docker exec -i "$CONTAINER_NAME" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < "$BACKUP_FILE"
fi

echo "Starting bot container..."
docker start remnawave-tg-shop 2>/dev/null || true

echo "=== Restore Completed at $(date) ==="
echo "Please verify the data is correct."
