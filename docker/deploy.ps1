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
# The remote script is base64-encoded: passing quotes/parens through PowerShell + ssh
# mangles them (observed: bash syntax error near '('), so ship opaque bytes instead.
$backupScript = @'
set -e
DIR="__DIR__"
if [ -f "$DIR/data/cloud.db" ]; then
  STAMP=$(date +%Y%m%d-%H%M%S)
  docker run --rm -v "$DIR/data:/data" python:3.11-slim python -c "import sqlite3, sys; s = sqlite3.connect('/data/cloud.db'); d = sqlite3.connect('/data/cloud.db.bak-' + sys.argv[1]); s.backup(d); d.close(); s.close()" "$STAMP"
  ls -1t "$DIR"/data/cloud.db.bak-* 2>/dev/null | tail -n +6 | xargs -r rm -f
  echo db-backed-up
else
  echo db-missing
fi
'@
$backupScript = $backupScript.Replace("__DIR__", $RemoteDir)
# The script file is CRLF; bash would read "set -e\r" and fail with "set: -: invalid option".
# Normalize to LF before encoding.
$backupScript = $backupScript.Replace("`r`n", "`n").Replace("`r", "")
$backupBytes = [System.Text.Encoding]::UTF8.GetBytes($backupScript)
$backupB64 = [Convert]::ToBase64String($backupBytes)
Invoke-Remote "echo $backupB64 | base64 -d | bash" "backup-db"

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
export DINGFENGBO_SYNC_IMAGE=dingfengbo-sync:__VERSION__
# Pick a reachable PyPI index: the official one occasionally times out from CN hosts
# (observed: ReadTimeoutError on files.pythonhosted.org), which fails the whole build.
if [ -z "$DINGFENGBO_PIP_INDEX" ]; then
  if curl -m 5 -fsI https://pypi.tuna.tsinghua.edu.cn/simple/ >/dev/null 2>&1; then
    export DINGFENGBO_PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
  fi
fi
echo "pip index: ${DINGFENGBO_PIP_INDEX:-https://pypi.org/simple}"
docker compose -f docker/docker-compose.yml up -d --build
for i in $(seq 1 30); do
  curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1 && echo health-ready && break
  sleep 1
done
docker ps --filter name=dingfengbo-sync --format '{{.Status}}'
curl -fsS http://127.0.0.1:8000/api/health
'@
# Image tag follows app/__init__.py __version__ (single source of truth), so the
# running container is always traceable to a release number.
$appVersion = (Select-String -Path "app/__init__.py" -Pattern '__version__\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
# Use string replacement instead of PowerShell's -f operator: -f collapses
# {{.Status}} into {.Status}, which breaks docker's --format argument.
$deploy = $deploy.Replace("__REMOTE_DIR__", $RemoteDir).Replace("__VERSION__", $appVersion)
# Same CRLF trap as the backup script: bash would see "set -e\r" and abort with
# "set: -: invalid option". Normalize to LF before shipping the command.
$deploy = $deploy.Replace("`r`n", "`n").Replace("`r", "")

Invoke-Remote $deploy "compose-up"
Write-Host "Deploy done: http://${HostIp}:8000"
