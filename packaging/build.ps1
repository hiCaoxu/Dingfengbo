# Build Windows executable: PyInstaller -> dist\Dingfengbo.exe / dist\定风波.exe
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

python -m pip install -r requirements.txt
python -m PyInstaller --clean --noconfirm "packaging\dingfengbo.spec"
$built = Join-Path "$root\dist" "Dingfengbo.exe"
if (-not (Test-Path $built)) { throw "PyInstaller did not produce $built" }
Copy-Item -LiteralPath $built -Destination (Join-Path "$root\dist" "定风波.exe") -Force

# 绿色分发包：exe + 使用说明，文件名带版本号（版本号唯一来源 app\__init__.py）
$version = (Select-String -Path (Join-Path "$root\app\__init__.py") -Pattern '__version__\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
$readme = Join-Path "$root\dist" "使用说明.txt"
@"
定风波 · 个人工作台 v$version

1. 双击 定风波.exe 即可运行，无需安装；删除 exe 即完成卸载。
2. 应用数据（数据库、设置、日志）保存在 D:\Data\Dingfengbo\，升级时覆盖 exe 即可，数据不受影响。
3. 需要开机自启时，把 exe 快捷方式放入 shell:startup 目录。
"@ | Set-Content -LiteralPath $readme -Encoding UTF8
$zip = Join-Path "$root\dist" "定风波-v$version.zip"
if (Test-Path $zip) { Remove-Item -LiteralPath $zip -Force }
Compress-Archive -LiteralPath (Join-Path "$root\dist" "定风波.exe"), $readme -DestinationPath $zip -Force
Write-Host "Output: dist\Dingfengbo.exe / dist\定风波.exe / dist\定风波-v$version.zip"
