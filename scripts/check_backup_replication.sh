#!/usr/bin/env bash
# Daily backup health check — alerts via ntfy when either backup layer stalls.
#
# Layer 1: VM-side nightly pg_dump (scripts/backup_db.sh, 08:00 UTC cron).
#   Newest dump older than MAX_DUMP_AGE_H means the dump cron itself is broken.
# Layer 2: off-VM replication. The Windows pull task
#   (scripts/windows_pull_backup.ps1, daily 09:00 ET) touches .last_pull_ok
#   after every clean run. Marker older than MAX_PULL_AGE_H means dumps are no
#   longer leaving this machine — the Windows box may just be off, but after
#   3 days it deserves a ping either way.
#
# Cron (user proxmox): 0 18 * * * /opt/stock-analysis/scripts/check_backup_replication.sh
# 18:00 UTC = after both the 08:00 UTC dump and the ~13:00 UTC pull window.

set -u

BACKUP_DIR="${BACKUP_DIR:-/home/proxmox/backups/stock-analysis}"
NTFY_TOPIC="${NTFY_TOPIC:-https://ntfy.sh/andym-homelab-9a7b9dce}"
MAX_DUMP_AGE_H="${MAX_DUMP_AGE_H:-48}"
MAX_PULL_AGE_H="${MAX_PULL_AGE_H:-72}"

alert() {
    curl -s --max-time 15 \
        -H "Title: stock-analysis backup check" \
        -H "Priority: high" \
        -H "Tags: warning,floppy_disk" \
        -d "$1" \
        "$NTFY_TOPIC" > /dev/null || true
}

age_hours() {
    # Hours since file mtime; prints a huge number when the file is missing.
    if [ ! -e "$1" ]; then
        echo 999999
        return
    fi
    echo $(( ( $(date +%s) - $(stat -c %Y "$1") ) / 3600 ))
}

newest_dump=$(ls -t "$BACKUP_DIR"/*.dump 2>/dev/null | head -1)
if [ -z "$newest_dump" ]; then
    alert "No dumps found in $BACKUP_DIR at all — backup cron broken?"
    exit 1
fi

dump_age=$(age_hours "$newest_dump")
if [ "$dump_age" -gt "$MAX_DUMP_AGE_H" ]; then
    alert "Newest DB dump is ${dump_age}h old ($(basename "$newest_dump")) — nightly pg_dump cron looks broken."
fi

pull_age=$(age_hours "$BACKUP_DIR/.last_pull_ok")
if [ "$pull_age" -gt "$MAX_PULL_AGE_H" ]; then
    if [ "$pull_age" -ge 999999 ]; then
        alert "Off-VM replication marker .last_pull_ok has never been written — Windows pull task not running?"
    else
        alert "Off-VM backup replication stale: last clean Windows pull ${pull_age}h ago (threshold ${MAX_PULL_AGE_H}h)."
    fi
fi

echo "$(date -u +%FT%TZ) dump_age=${dump_age}h pull_age=${pull_age}h"
