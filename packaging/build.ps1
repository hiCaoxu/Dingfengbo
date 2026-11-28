# Build Windows app: PyInstaller onedir -> dist\Dingfengbo\ (定风波.exe + _internal\)
$ErrorActionPreference = "Stop"
# 本脚本可能在 Windows PowerShell 5.1 下运行，它会把「原生命令写到 stderr 的提示」
# 当成终止错误——pip 一句「有新版本可用」就能让整个构建中断（历史踩过）。
# 因此调用外部命令期间临时放宽 ErrorActionPreference，改为显式检查退出码。
# PS 7.3+ 额外有这个同名开关，设了也无害。
$PSNativeCommandUseErrorActionPreference = $false

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$What,
        [Parameter(Mandatory = $true)][string]$Exe,
        [Parameter(Mandatory = $true)][string[]]$ArgList
    )
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Exe @ArgList
    } finally {
        $ErrorActionPreference = $previous
    }
    $code = $LASTEXITCODE
    if ($code -ne 0) { throw "$What 失败（exit $code）" }
}

Invoke-Native -What "pip install" -Exe "python" `
    -ArgList @("-m", "pip", "install", "-r", "requirements.txt")
Invoke-Native -What "PyInstaller" -Exe "python" `
    -ArgList @("-m", "PyInstaller", "--clean", "--noconfirm", "packaging\dingfengbo.spec")

$appDir = Join-Path "$root\dist" "Dingfengbo"
$built = Join-Path $appDir "Dingfengbo.exe"
$shipped = Join-Path $appDir "定风波.exe"
if (-not (Test-Path -LiteralPath $built)) { throw "PyInstaller did not produce $built" }

# spec 的 name 保持英文，避免引导程序和中间路径碰到非 ASCII。
# 发布只留一个 exe：改名不影响加载，仍共用旁边的 _internal\。
if (Test-Path -LiteralPath $shipped) { Remove-Item -LiteralPath $shipped -Force }
Move-Item -LiteralPath $built -Destination $shipped

# 绿色分发包：整个程序文件夹 + 使用说明，文件名带版本号（版本号唯一来源 app\__init__.py）
# 注意：这里不能用 Join-Path "$root\app\__init__.py"——Join-Path 需要两个参数，
# 单参数会抛 MissingMandatoryParameter 并让整个脚本中断（历史踩过，见 git log）。
$versionFile = "$root\app\__init__.py"
$version = (Select-String -Path $versionFile -Pattern '__version__\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
if (-not $version) { throw "无法从 $versionFile 解析 __version__" }
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
