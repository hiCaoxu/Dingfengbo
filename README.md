# 定风波 · 个人工作台

单机优先、可多端同步的桌面个人工作台，覆盖首页、待办、日记、笔记、健康、娱乐、记账、AI 助手八个模块。

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

桌面端「设置」中填写服务地址与令牌即可启用自动同步，并可点「测试同步连接」/「测试 AI 连接」验证配置。断网时七个数据模块均可正常使用，联网后按时间戳增量补传。

## 开发与测试

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q          # 或：python -m unittest discover -s tests -t .
```

测试依赖锁在 `requirements-dev.txt`：**不要装 `pytest-timeout` 2.7 以下版本**，它在 pytest 8 的 `addini` 类型校验下会让 pytest 在收集阶段直接崩溃，本项目也不需要超时插件。

已知技术债（暂不处理，出现实际问题再启动）：

- 金额仍用 `float` + 入库取两位小数，尚未迁移 `Decimal`；当前无分位误差故障报告。
- `ft.TextButton` 之外的 Flet 控件已全部避开弃用项（`ElevatedButton` 已换 `ft.Button`），Flet 1.0 正式版发布时再统一升级验证。
- Caddy TLS 仅在文档层面就绪，尚未在真实域名上做生产演练。

## 数据目录

- 应用数据：统一在 `D:\Data\Dingfengbo\`（`dingfengbo.db`、`settings.json`、`logs\`、`backups\`），与 exe 所在目录分离，替换升级不丢数据。
- 可通过环境变量 `DINGFENGBO_DATA_DIR` 覆盖本地数据目录。
- 自动备份：`backups\dingfengbo-时间戳-auto.db` 是 SQLite 在线快照（官方 `backup` API，WAL 模式下同样一致），每天首次启动备份一次、默认保留最近 7 份；设置页提供「立即备份」与「打开备份目录」。恢复方式：关闭应用，把备份文件复制回 `D:\Data\Dingfengbo\dingfengbo.db`。
- 敏感字段：`settings.json` 中的同步令牌与 AI Key 以 Windows DPAPI 按当前用户加密（`dpapi:` 前缀），拷到别的机器或别的 Windows 账户无法解开；旧版 `b64:` 值仍可读，下次保存自动升级。

## 打包（绿色单文件）

```powershell
.\packaging\build.ps1
```

产物（`dist\`）：

- `定风波.exe` / `Dingfengbo.exe`：PyInstaller 单文件，双击即用，无需安装。
- `定风波-v{版本号}.zip`：exe + 使用说明，版本号读自 `app/__init__.py` 的 `__version__`。

分发方式：

- 把 exe 复制到任意目录运行（可放桌面或自建程序文件夹），**没有安装目录、开始菜单项与卸载入口的概念**，删除 exe 即完成卸载。
- 升级 = 用新版本 exe 覆盖旧 exe；数据始终在 `D:\Data\Dingfengbo\`，不受影响。
- 需要开机自启时，把 exe 快捷方式放入 `shell:startup` 目录（Win+R 输入 `shell:startup`）。
- 单文件模式首次启动需解压到临时目录，冷启动较慢；exe 带版本资源，右键属性可查看版本号。

## 同步机制

- 本地优先：写操作先落 SQLite，再进入待同步队列。
- 触发：启动一次、每 5 分钟一次、本地写入后 10 秒一次，以及「立即同步」按钮。
- 接口：`POST /api/sync/pull`、`POST /api/sync/push`、`GET /api/sync/check`，Bearer token 鉴权并按用户隔离。
- 冲突：按 `updated_at` 最后写入获胜；删除使用软删除，同步完成后物理清理。
- 序列：服务端用全局单调递增的 `sync_seq` 分配号，客户端以 `sync_seq > cursor` 增量拉取；已有数据的服务端库升级时会把计数器锚定到存量最大值（启动日志会打印「同步序列当前值」）。需要自检时在服务端跑 `python scripts/check_sync_sequence.py <cloud.db 路径>`，输出计数器与各表 `MAX(sync_seq)` 的比对结论。
- 被拒记录：push 校验失败的记录由服务端回传 `table`/`id`，客户端标记为 `error` 退出待同步队列，不再无限重推；重新编辑该记录后自动回到 `pending`。
- 队列可见性：顶部状态栏在有待同步或失败记录时显示「待同步 N · 失败 M」，统计用一条 `UNION ALL` 聚合十张表，状态栏周期刷新不会带来额外往返；有失败记录时该区域可点击，直接打开设置页。
- 拒收明细：服务端回传的 `reason` 存在本地 `sync_issues` 表（不参与同步），设置页列出「哪张表 / 哪条记录 / 为什么」；点「全部重试」把所有 error 记录放回待同步队列并清空明细，若载荷本身非法则下次 push 会再次被拒并重新出现在明细里，不会静默循环。

## 待办提醒与番茄钟

- 到期时间：待办可填 `HH:MM`，列表与首页都显示日期 + 时间，排序按（日期, 时间）。
- 自定义循环（对齐「到期重复」面板）：
  - 天：每 N 天，可勾选「跳过双休日」「跳过法定节假日」；
  - 周：每 N 周，多选星期（周一…周日）；
  - 月：每 N 月，三种方式——按日期（多选 1–31 日或「最后一天」）、按星期（第一个/…/最后一个 + 星期几）、按工作日（第一个/最后一个工作日）；
  - 年：每 N 年，按日期（几月几日）或按星期（几月的第几个星期几）；
  - 兼容旧数据：「每周 N 次」「每月第一个/最后一个工作日」等旧配置自动转成新结构；旧「不定期」按「自定义但未配置」处理，不推进下一次。
- 法定节假日与调休：内置 2026 年（国办发明电〔2025〕7 号）与 2027 年（国办发明电〔2026〕10 号）的放假与补班安排，勾选「跳过法定节假日」后生效；调休上班的周末按工作日处理，不会被「跳过双休日」误跳。以后年份可在数据目录 `holidays.txt` 补充：每行 `YYYY-MM-DD` 表示放假，行首加 `!` 表示调休上班日（如 `!2028-01-02`），`#` 之后为注释。内置数据在 `app/services/holiday_data.py`，新增年份按同一格式补充即可。
- 提前提醒：可多选「到期当天 / 提前 1 天 / 提前 3 天 / 提前 7 天」，每个时间点各提醒一次（如同时选 1 天和 3 天，会在到期前 3 天和 1 天各弹一次）；不选即不提醒。提醒时间留空时依次退回到期时间、09:00。
- 系统提醒：应用运行期间每分钟检查一次，到点弹 Windows 通知（PowerShell WinRT toast，无需额外依赖；失败退回系统提示音，并在应用内提示）。`todo_reminders` 表按（待办, 提醒时刻）去重，同一次只提醒一次；应用关闭超过 12 小时的提醒不补发，避免一次性刷屏。
- 番茄钟：待办页「番茄钟」按钮，可选关联待办与 25/45/60 分钟时长，结束后系统通知并给该待办累加 `pomodoro_count`（随同步传播）。

## Docker 部署同步服务

```powershell
.\docker\deploy.ps1 -HostIp <服务器IP> -User root
```

脚本仅创建远程目录 `/opt/dingfengbo-sync` 并启动容器 `dingfengbo-sync`，不改动主机已有环境。数据卷为 `/opt/dingfengbo-sync/data`，容器监听 `8000:8000`，并以非 root 用户运行。桌面端「设置」里的服务地址填 `http://<服务器IP>:8000`。

若公网无法访问，需在云厂商安全组放行 TCP 8000（服务器内部 `ufw` 无需改动）；腾讯云轻量应用服务器可在「防火墙」中添加规则。

### 可选：Caddy 自动 HTTPS 反代

token 即全部数据访问权，公网直连明文 HTTP 有被嗅探风险。需要 TLS 时用 compose 的 `tls` profile 启动 Caddy（自动申请证书）：

```powershell
# 在服务器上，docker/docker-compose.yml 同目录
$env:DOMAIN = "sync.example.com"
docker compose --profile tls up -d
```

收口顺序（先验证再关闭，避免把自己锁在门外）：

1. 启动 `tls` profile，用 `https://sync.example.com/api/health` 验证证书与反代；
2. 桌面端「设置」把服务地址改成 `https://sync.example.com`，点「测试同步连接」确认；
3. 确认可用后，再删除 `docker-compose.yml` 里 8000 的端口映射（或从云安全组移除 8000）。

容器数据卷由宿主目录挂载，若出现写入权限问题，先 `chown -R 10001:10001 /opt/dingfengbo-sync/data`。

## 关键实现

- Python 3.11 + Flet + SQLAlchemy 2.x + SQLite。
- ISO 周数使用 `date.isocalendar()[1]`，年内天数使用 `timetuple().tm_yday`，农历使用 `zhdate`。
- 笔记使用 Pygments 高亮，左侧行号与代码逐行对齐滚动；不支持公式与图表。
- 图表由 matplotlib 生成，文字使用系统中文字体，配色遵循米白主题。
- AI 助手通过 OpenAI 兼容协议对接国产大模型；被 `@引用` 的数据会发送至所配置的服务商，设置页有隐私说明，首次发送前另有一次确认。
