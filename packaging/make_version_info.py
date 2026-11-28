"""从 app/__init__.py 的 __version__ 生成 PyInstaller 版本资源文件。

产物 ``packaging/version_info.txt`` 会被 dingfengbo.spec 通过 ``version=`` 引用，
使 exe 右键「属性 → 详细信息」显示版本号/产品名，避免用户无法判断手头是哪一版。
"""

from __future__ import annotations

import re
from pathlib import Path

SPEC_DIR = Path(__file__).resolve().parent
ROOT = SPEC_DIR.parent
VERSION_FILE = SPEC_DIR / "version_info.txt"

_COMPANY = "定风波"
_PRODUCT = "定风波 · 个人工作台"


def read_version() -> str:
    source = (ROOT / "app" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', source)
    if not match:
        raise SystemExit("app/__init__.py 中未找到 __version__")
    return match.group(1)


def main() -> None:
    version = read_version()
    numbers = [int(part) for part in re.findall(r"\d+", version)[:4]]
    numbers += [0] * (4 - len(numbers))
    tuple_text = ", ".join(str(n) for n in numbers)
    VERSION_FILE.write_text(
        f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({tuple_text}),
    prodvers=({tuple_text}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '080404B0',
        [StringStruct('CompanyName', '{_COMPANY}'),
         StringStruct('FileDescription', '{_PRODUCT}'),
         StringStruct('FileVersion', '{version}'),
         StringStruct('InternalName', 'Dingfengbo'),
         StringStruct('OriginalFilename', '定风波.exe'),
         StringStruct('ProductName', '{_PRODUCT}'),
         StringStruct('ProductVersion', '{version}')])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
""",
        encoding="utf-8",
    )
    print(f"version_info.txt -> {version}")


if __name__ == "__main__":
    main()
