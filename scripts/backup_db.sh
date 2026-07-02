#!/usr/bin/env bash
# Nightly Postgres backup for stock-analysis.
# Installed as a cron job on the gpu-ai VM (see crontab -l for user proxmox):
#   0 8 * * * /opt/stock-analysis/scripts/backup_db.sh >> /var/log/stock-analysis-backup.log 2>&1
#
# Dumps in pg_dump custom format (compressed, pg_restore-able) with 14-day
# retention. NOTE: backups live on the same VM disk — this protects against
# DB corruption, bad migrations, and accidental deletes, NOT disk loss.
# Off-VM replication is a follow-up (needs SSH key to Proxmox host or NAS).
set -euo pipefail

BACKUP_DIR=/opt/backups/stock-analysis
CONTAINER=backend-postgres-1
DB_NAME=stock_analysis
DB_USER=stockuser
RETENTION_DAYS=14

mkdir -p "$BACKUP_DIR"

STAMP=$(date +%F)
OUT="$BACKUP_DIR/stock_analysis_${STAMP}.dump"

docker exec "$CONTAINER" pg_dump -U "$DB_USER" -Fc "$DB_NAME" > "$OUT".tmp
mv "$OUT".tmp "$OUT"

# Sanity check: a real dump of this DB is multi-MB; fail loudly on runts.
SIZE=$(stat -c%s "$OUT")
if [ "$SIZE" -lt 1048576 ]; then
    echo "$(date -Is) ERROR: dump suspiciously small (${SIZE} bytes): $OUT" >&2
    exit 1
fi

find "$BACKUP_DIR" -name 'stock_analysis_*.dump' -mtime +"$RETENTION_DAYS" -delete

echo "$(date -Is) OK: $OUT ($(numfmt --to=iec "$SIZE"))"
