# Build Windows app: PyInstaller onedir -> dist\Dingfengbo\ (Dingfengbo.exe + 定风波.exe + _internal\)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

python -m pip install -r requirements.txt
python -m PyInstaller --clean --noconfirm "packaging\dingfengbo.spec"

$appDir = Join-Path "$root\dist" "Dingfengbo"
$built = Join-Path $appDir "Dingfengbo.exe"
if (-not (Test-Path $built)) { throw "PyInstaller did not produce $built" }

# 中文名副本：与 Dingfengbo.exe 同目录，共用旁边的 _internal\，重命名不影响加载。
Copy-Item -LiteralPath $built -Destination (Join-Path $appDir "定风波.exe") -Force

# 绿色分发包：整个程序文件夹 + 使用说明，文件名带版本号（版本号唯一来源 app\__init__.py）
$version = (Select-String -Path (Join-Path "$root\app\__init__.py") -Pattern '__version__\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
$readme = Join-Path $appDir "使用说明.txt"
@"
定风波 · 个人工作台 v$version

1. 双击 定风波.exe 即可运行，无需安装；删除整个文件夹即完成卸载。
2. _internal 文件夹是程序本体的一部分，必须和 exe 待在同一个目录：只把 exe 单独拷到桌面会启动失败。
3. 升级 = 用新版本的整个文件夹替换旧文件夹，数据不受影响。
4. 应用数据（数据库、设置、日志）保存在 D:\Data\Dingfengbo\，与程序目录分离。
5. 需要开机自启时，把 定风波.exe 的快捷方式放入 shell:startup 目录。
"@ | Set-Content -LiteralPath $readme -Encoding UTF8

$zip = Join-Path "$root\dist" "定风波-v$version.zip"
if (Test-Path $zip) { Remove-Item -LiteralPath $zip -Force }
# 整个文件夹入包：解压得到一个完整目录，不会让 _internal 散落在下载文件夹里。
Compress-Archive -LiteralPath $appDir -DestinationPath $zip -Force
Write-Host "Output: dist\Dingfengbo\ (定风波.exe + _internal\) / dist\定风波-v$version.zip"
