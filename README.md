# 定风波 · 个人工作台

单机优先、可多端同步的桌面个人工作台，覆盖首页、待办、日记、笔记、健康、娱乐、记账七个模块。

## 运行

```powershell
python -m pip install -r requirements.txt
python main.py
```

同步服务端（部署在腾讯云轻量服务器）：

```powershell
python run_server.py
# 默认监听 0.0.0.0:8000，可用 DINGFENGBO_HOST / DINGFENGBO_PORT 覆盖
```

桌面端「设置」中填写服务地址与令牌即可启用自动同步。断网时七模块均可正常使用，联网后按时间戳增量补传。

## 数据目录

- 安装目录：默认 `D:\Dingfengbo`
- 应用数据：统一在 `D:\Data\Dingfengbo\`（`dingfengbo.db`、`settings.json`），与安装目录分离，重装不丢数据。
- 可通过环境变量 `DINGFENGBO_DATA_DIR` 覆盖本地数据目录。

## 打包

```powershell
.\packaging\build.ps1
```

依赖 PyInstaller 与 WiX Toolset v3（也可将 `candle.exe`/`light.exe` 放到 `packaging\wix314\`）。产物为 `packaging\Dingfengbo.msi`，安装后生成 `定风波.exe`、桌面快捷方式、开始菜单项与卸载入口。

## 同步机制

- 本地优先：写操作先落 SQLite，再进入待同步队列。
- 触发：启动一次、每 5 分钟一次、本地写入后 10 秒一次，以及「立即同步」按钮。
- 接口：`POST /api/sync/pull`、`POST /api/sync/push`、`GET /api/sync/check`，Bearer token 鉴权并按用户隔离。
- 冲突：按 `updated_at` 最后写入获胜；删除使用软删除，同步完成后物理清理。

## Docker 部署同步服务

```powershell
.\docker\deploy.ps1 -HostIp 43.142.92.115 -User root
```

脚本仅创建远程目录 `/opt/dingfengbo-sync` 并启动容器 `dingfengbo-sync`，不改动主机已有环境。数据卷为 `/opt/dingfengbo-sync/data`，容器监听 `8000:8000`。桌面端「设置」里的服务地址填 `http://43.142.92.115:8000`。

若公网无法访问，需在云厂商安全组放行 TCP 8000（服务器内部 `ufw` 无需改动）；腾讯云轻量应用服务器可在「防火墙」中添加规则。

## 关键实现

- Python 3.11 + Flet + SQLAlchemy 2.x + SQLite。
- ISO 周数使用 `date.isocalendar()[1]`，年内天数使用 `timetuple().tm_yday`，农历使用 `zhdate`。
- 笔记使用 Pygments 高亮，左侧行号与代码逐行对齐滚动；不支持公式与图表。
- 图表由 matplotlib 生成，文字使用系统中文字体，配色遵循米白主题。
