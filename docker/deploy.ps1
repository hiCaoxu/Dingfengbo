# Deploy sync server to a remote host over SSH.
# Usage: .\docker\deploy.ps1 -HostIp 1.2.3.4 -User root
param(
    [Parameter(Mandatory = $true)][string]$HostIp,
    [string]$User = "root",
    [string]$RemoteDir = "/opt/dingfengbo-sync"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$target = "$User@$HostIp"
$sshOpts = @("-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new")

& ssh @sshOpts $target "mkdir -p '$RemoteDir/docker' '$RemoteDir/data'"
if ($LASTEXITCODE -ne 0) { throw "SSH failed" }
# 容器以 uid 10001 运行，宿主目录必须归它所有，否则首次部署会因
# unable to open database file 陷入重启循环（docker 以 root 建目录时是 755/root）。
& ssh @sshOpts $target "chown -R 10001:10001 '$RemoteDir/data'"
if ($LASTEXITCODE -ne 0) { throw "数据目录授权失败" }

& scp @sshOpts -r app server run_server.py requirements-server.txt .dockerignore "$target`:$RemoteDir/"
if ($LASTEXITCODE -ne 0) { throw "Copy source failed" }
& scp @sshOpts docker/Dockerfile docker/docker-compose.yml docker/Caddyfile "$target`:$RemoteDir/docker/"
if ($LASTEXITCODE -ne 0) { throw "Copy docker config failed" }

& ssh @sshOpts $target "cd '$RemoteDir' && docker compose -f docker/docker-compose.yml up -d --build && for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1 && echo 'health-ready' && break; sleep 1; done && docker ps --filter name=dingfengbo-sync --format '{{.Status}}' && curl -fsS http://127.0.0.1:8000/api/health"
if ($LASTEXITCODE -ne 0) { throw "Remote deploy failed" }
