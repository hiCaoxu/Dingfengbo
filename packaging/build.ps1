# 构建 Windows 安装包：PyInstaller -> WiX
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

python -m pip install -r requirements.txt
python -m PyInstaller --clean --noconfirm "packaging\定风波.spec"

Push-Location $PSScriptRoot
& candle.exe -nologo -out "installer.wixobj" "installer.wxs"
& light.exe -nologo -b "$root\dist" -b "$PSScriptRoot" -out "定风波.msi" "installer.wixobj"
Pop-Location
Write-Host "产物：packaging\定风波.msi"
