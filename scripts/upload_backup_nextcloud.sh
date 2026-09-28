#!/usr/bin/env bash
# Replicate nightly DB dumps to Nextcloud (docker-nextcloud VM) over WebDAV.
#
# Third backup layer, alongside the VM-local dumps (backup_db.sh) and the
# Windows pull (windows_pull_backup.ps1). Uploads every local dump that the
# Nextcloud folder doesn't already have, so a missed night (VM or Nextcloud
# down) is caught up on the next run. Keeps REMOTE_RETENTION_DAYS on Nextcloud.
#
# Cron (user proxmox), after the 08:00 UTC dump:
#   15 8 * * * /opt/stock-analysis/scripts/upload_backup_nextcloud.sh >> /home/proxmox/backups/nextcloud-upload.log 2>&1
#
# Credentials live outside the repo in $NC_ENV (chmod 600):
#   NC_URL=http://10.0.0.45:8080
#   NC_USER=<nextcloud username>
#   NC_APP_PASSWORD=<app password from Nextcloud > Settings > Security>
#   NC_DIR=Backups/stock-analysis        # optional
# On success touches $BACKUP_DIR/.last_nextcloud_ok, which
# check_backup_replication.sh watches.
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/home/proxmox/backups/stock-analysis}"
NC_ENV="${NC_ENV:-/home/proxmox/.config/nextcloud-backup.env}"
REMOTE_RETENTION_DAYS="${REMOTE_RETENTION_DAYS:-30}"

[ -r "$NC_ENV" ] || { echo "$(date -Is) ERROR: $NC_ENV missing" >&2; exit 1; }
# shellcheck disable=SC1090
. "$NC_ENV"
: "${NC_URL:?}" "${NC_USER:?}" "${NC_APP_PASSWORD:?}"
NC_DIR="${NC_DIR:-Backups/stock-analysis}"

DAV="${NC_URL%/}/remote.php/dav/files/${NC_USER}"
# Credentials go to curl via a process-substitution config file, so they never show in `ps`.
curl_nc() {
    curl -sS --fail-with-body --max-time 300 \
        --config <(printf 'user = "%s:%s"\n' "$NC_USER" "$NC_APP_PASSWORD") "$@"
}

# Create each folder level; 405 = already exists.
path=""
IFS='/' read -ra parts <<< "$NC_DIR"
for part in "${parts[@]}"; do
    path="${path:+$path/}$part"
    code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 30 \
        --config <(printf 'user = "%s:%s"\n' "$NC_USER" "$NC_APP_PASSWORD") \
        -X MKCOL "$DAV/$path/")
    case "$code" in
        201|405) ;;
        *) echo "$(date -Is) ERROR: MKCOL $path -> HTTP $code" >&2; exit 1 ;;
    esac
done

# Names already on Nextcloud.
remote=$(curl_nc -X PROPFIND -H "Depth: 1" "$DAV/$NC_DIR/" \
    | grep -o 'stock_analysis_[0-9-]*\.dump' | sort -u || true)

uploaded=0
for f in "$BACKUP_DIR"/stock_analysis_*.dump; do
    [ -e "$f" ] || continue
    name=$(basename "$f")
    if grep -qx "$name" <<< "$remote"; then
        continue
    fi
    curl_nc -T "$f" "$DAV/$NC_DIR/$name" > /dev/null
    echo "$(date -Is) UPLOADED: $name ($(numfmt --to=iec "$(stat -c%s "$f")"))"
    uploaded=$((uploaded + 1))
done

# Prune remote dumps older than the retention window (dates are in the names).
cutoff=$(date -d "-${REMOTE_RETENTION_DAYS} days" +%F)
pruned=0
for name in $remote; do
    d=${name#stock_analysis_}; d=${d%.dump}
    if [[ "$d" < "$cutoff" ]]; then
        curl_nc -X DELETE "$DAV/$NC_DIR/$name" > /dev/null
        echo "$(date -Is) PRUNED: $name"
        pruned=$((pruned + 1))
    fi
done

touch "$BACKUP_DIR/.last_nextcloud_ok"
echo "$(date -Is) OK: uploaded $uploaded, pruned $pruned, remote dir $NC_DIR"
