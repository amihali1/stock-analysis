# Pull nightly stock-analysis DB dumps from the homelab VM to this machine.
#
# The VM-side cron (scripts/backup_db.sh, 08:00 UTC) writes pg_dump files to
# /home/proxmox/backups/stock-analysis/ with 14-day retention, but the VM disk,
# the other VMs, and the Proxmox host all share one physical disk, so any copy
# that stays on that box does not survive disk loss. This script runs from a
# Windows Scheduled Task ("StockAnalysis DB Backup Pull", daily 09:00 local)
# and mirrors the dumps here over SSH.
#
# Requirements: Windows OpenSSH client, passwordless key auth to proxmox@10.0.0.47
# for the account running the task.
#
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads BOM-less files
# as ANSI, and any non-ASCII character (em-dash etc.) breaks the parse.

$ErrorActionPreference = "Stop"

$RemoteHost = "proxmox@10.0.0.47"
$RemoteDir = "/home/proxmox/backups/stock-analysis"
$LocalDir = "C:\Backups\stock-analysis"
$LogFile = Join-Path $LocalDir "pull.log"
$RetentionDays = 60
$MinSizeBytes = 1048576  # 1 MB, same runt guard as the VM-side script
$NtfyTopic = "https://ntfy.sh/andym-homelab-9a7b9dce"

function Write-Log($msg) {
    $line = "{0:yyyy-MM-ddTHH:mm:sszzz} {1}" -f (Get-Date), $msg
    Add-Content -Path $LogFile -Value $line -Encoding utf8
}

function Send-Alert($msg) {
    # Same public ntfy.sh topic Alertmanager uses (see homelab-monitoring).
    # Best-effort: alerting must never fail the pull itself.
    try {
        Invoke-RestMethod -Method Post -Uri $NtfyTopic -Body $msg -Headers @{
            Title = "stock-analysis backup pull FAILED"
            Priority = "high"
            Tags = "warning,floppy_disk"
        } -TimeoutSec 15 | Out-Null
    } catch {
        Write-Log "WARN: ntfy alert failed: $_"
    }
}

if (-not (Test-Path $LocalDir)) {
    New-Item -ItemType Directory -Force $LocalDir | Out-Null
}

try {
    $remoteFiles = ssh -o BatchMode=yes -o ConnectTimeout=15 $RemoteHost "ls $RemoteDir/*.dump 2>/dev/null | xargs -n1 basename"
    if ($LASTEXITCODE -ne 0 -or -not $remoteFiles) {
        Write-Log "FAIL: could not list remote dumps (ssh exit $LASTEXITCODE)"
        Send-Alert "Could not list remote dumps on 10.0.0.47 (ssh exit $LASTEXITCODE). Check VM/SSH."
        exit 1
    }
} catch {
    Write-Log "FAIL: ssh error: $_"
    Send-Alert "SSH to 10.0.0.47 failed: $_"
    exit 1
}

$pulled = 0
$failed = 0
foreach ($name in $remoteFiles) {
    $name = $name.Trim()
    if (-not $name) { continue }
    $localPath = Join-Path $LocalDir $name
    if (Test-Path $localPath) { continue }

    scp -o BatchMode=yes -q "${RemoteHost}:${RemoteDir}/${name}" $localPath
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $localPath)) {
        Write-Log "FAIL: scp $name (exit $LASTEXITCODE)"
        $failed++
        continue
    }
    $size = (Get-Item $localPath).Length
    if ($size -lt $MinSizeBytes) {
        Remove-Item $localPath -Force -Confirm:$false
        Write-Log "FAIL: $name runt ($size bytes), deleted"
        $failed++
        continue
    }
    Write-Log ("OK: {0} ({1:N1} MB)" -f $name, ($size / 1048576))
    $pulled++
}

# Local retention is longer than the VM's 14 days so this side keeps history
# the VM has already rotated out.
$cutoff = (Get-Date).AddDays(-$RetentionDays)
Get-ChildItem $LocalDir -Filter "*.dump" | Where-Object { $_.LastWriteTime -lt $cutoff } | ForEach-Object {
    Write-Log "PRUNE: $($_.Name)"
    Remove-Item $_.FullName -Force -Confirm:$false
}

$total = (Get-ChildItem $LocalDir -Filter "*.dump").Count
Write-Log "DONE: pulled $pulled, failed $failed, $total dumps local"

if ($failed -gt 0) {
    Send-Alert "Pull finished with $failed failed transfer(s) ($pulled ok). See C:\Backups\stock-analysis\pull.log"
    exit 1
}

# Touch the replication marker on the VM so the VM-side staleness check
# (scripts/check_backup_replication.sh, daily cron) knows the off-VM copy is
# current. Only on fully clean runs.
ssh -o BatchMode=yes -o ConnectTimeout=15 $RemoteHost "date -u +%Y-%m-%dT%H:%M:%SZ > $RemoteDir/.last_pull_ok"
if ($LASTEXITCODE -ne 0) {
    Write-Log "WARN: could not update .last_pull_ok marker on VM"
}
