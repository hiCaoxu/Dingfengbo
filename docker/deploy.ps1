# Deploy sync server to a remote host over SSH.
# Usage: .\docker\deploy.ps1 -HostIp 1.2.3.4 -User root
#
# NOTE: keep this file ASCII-only. Windows PowerShell decodes BOM-less scripts
# with the ANSI code page, and non-ASCII text can break parsing on some hosts.
#
# NOTE: ErrorActionPreference must stay "Continue": ssh/scp write diagnostics to
# stderr, and with "Stop" PowerShell turns that into a terminating error before
# the retry loop can look at $LASTEXITCODE.
param(
    [Parameter(Mandatory = $true)][string]$HostIp,
    [string]$User = "root",
    [string]$RemoteDir = "/opt/dingfengbo-sync",
    [int]$Retries = 4
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$target = "$User@$HostIp"
$sshOpts = @("-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new")

# Cloud hosts often rate-limit rapid SSH connections (PerSourcePenalties /
# fail2ban) and may drop the handshake. Retry every step so one transient
# failure does not abort the whole deployment.
function Invoke-Remote {
    param([string]$Command, [string]$Label)
    for ($attempt = 1; $attempt -le $Retries; $attempt++) {
        $output = & ssh @sshOpts $target $Command 2>&1 | Out-String
        if ($LASTEXITCODE -eq 0) {
            if ($output.Trim()) { Write-Host $output.Trim() }
            return
        }
        Write-Host "  [$Label] attempt $attempt failed: $($output.Trim().Split("`n")[0])"
        Start-Sleep -Seconds 3
    }
    throw "$Label failed after $Retries attempts"
}

function Send-Files {
    param([string]$Source, [string]$Destination, [string]$Label)
    for ($attempt = 1; $attempt -le $Retries; $attempt++) {
        $output = & scp @sshOpts -r $Source "$target`:$Destination" 2>&1 | Out-String
        if ($LASTEXITCODE -eq 0) { return }
        Write-Host "  [$Label] attempt $attempt failed: $($output.Trim().Split("`n")[0])"
        Start-Sleep -Seconds 3
    }
    throw "$Label failed after $Retries attempts"
}

Invoke-Remote "mkdir -p '$RemoteDir/docker' '$RemoteDir/data' && echo dirs-ready" "make-dirs"
# Container runs as uid 10001: the host data dir must be owned by it, otherwise
# first deploy fails with "unable to open database file" and restarts forever
# (docker creates the folder as root 755).
Invoke-Remote "chown -R 10001:10001 '$RemoteDir/data' && echo chown-ready" "chown-data"

# Back up the cloud DB BEFORE shipping new code: the new image runs schema migrations
# at startup, and a bad migration would otherwise rewrite the only copy in place.
# sqlite3's .backup API is WAL-safe (plain cp is not); keep the newest 5 snapshots.
# Single-quoted here-string: PowerShell expands nothing, so $(date ...) runs remotely.
$backupCmd = @'
if [ -f "__DIR__/data/cloud.db" ]; then
  docker run --rm -v "__DIR__/data:/data" python:3.11-slim python -c "import sqlite3; src = sqlite3.connect('/data/cloud.db'); dst = sqlite3.connect('/data/cloud.db.bak-' + __STAMP__); src.backup(dst); dst.close(); src.close()" && echo db-backed-up || echo db-backup-failed
  ls -1t __DIR__/data/cloud.db.bak-* 2>/dev/null | tail -n +6 | xargs -r rm -f
else
  echo db-missing
fi
'@
$backupCmd = $backupCmd.Replace("__DIR__", $RemoteDir).Replace("__STAMP__", '$(date +%Y%m%d-%H%M%S)')
Invoke-Remote $backupCmd "backup-db"

Send-Files "app" "$RemoteDir/" "copy-app"
Send-Files "server" "$RemoteDir/" "copy-server"
Send-Files "run_server.py" "$RemoteDir/" "copy-entry"
Send-Files "requirements-server.txt" "$RemoteDir/" "copy-requirements"
Send-Files ".dockerignore" "$RemoteDir/" "copy-dockerignore"
Send-Files "docker/Dockerfile" "$RemoteDir/docker/" "copy-dockerfile"
Send-Files "docker/docker-compose.yml" "$RemoteDir/docker/" "copy-compose"
if (Test-Path "docker/Caddyfile") {
    Send-Files "docker/Caddyfile" "$RemoteDir/docker/" "copy-caddyfile"
}

$deploy = @'
set -e
cd __REMOTE_DIR__
docker compose -f docker/docker-compose.yml up -d --build
for i in $(seq 1 30); do
  curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1 && echo health-ready && break
  sleep 1
done
docker ps --filter name=dingfengbo-sync --format '{{.Status}}'
curl -fsS http://127.0.0.1:8000/api/health
'@
# Use string replacement instead of PowerShell's -f operator: -f collapses
# {{.Status}} into {.Status}, which breaks docker's --format argument.
$deploy = $deploy.Replace("__REMOTE_DIR__", $RemoteDir)

Invoke-Remote $deploy "compose-up"
Write-Host "Deploy done: http://${HostIp}:8000"
