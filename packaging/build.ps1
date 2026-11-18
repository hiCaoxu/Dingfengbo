# Build Windows installer: PyInstaller -> WiX
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

python -m pip install -r requirements.txt
python -m PyInstaller --clean --noconfirm "packaging\dingfengbo.spec"

$candle = (Get-Command candle.exe -ErrorAction SilentlyContinue).Source
$light = (Get-Command light.exe -ErrorAction SilentlyContinue).Source
if (-not $candle) { $candle = Join-Path $PSScriptRoot "wix314\candle.exe" }
if (-not $light) { $light = Join-Path $PSScriptRoot "wix314\light.exe" }
if (-not (Test-Path $candle) -or -not (Test-Path $light)) {
    throw "WiX Toolset v3 not found. Put candle.exe/light.exe in packaging\wix314 or add to PATH."
}

Push-Location $PSScriptRoot
& $candle -nologo -ext WixUIExtension -out "installer.wixobj" "installer.wxs"
if ($LASTEXITCODE -ne 0) { throw "candle failed" }
& $light -nologo -ext WixUIExtension -cultures:zh-CN -loc "zh-CN.wxl" -b "$root\dist" -b "$PSScriptRoot" -out "Dingfengbo.msi" "installer.wixobj"
if ($LASTEXITCODE -ne 0) { throw "light failed" }
Pop-Location
Write-Host "Output: packaging\Dingfengbo.msi"
