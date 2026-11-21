# 定风波 · 个人工作台

单机优先、可多端同步的桌面个人工作台，覆盖首页（今日聚焦/数据概览）、任务（待办 + 习惯）、日记、笔记、健康、娱乐、记账、回顾、时间轴、AI 助手十个模块。

## 快捷键与入口

| 快捷键 | 作用 |
|---|---|
| `Ctrl+K` | 全局搜索 / 命令面板：跨待办、笔记、日记、收支、娱乐检索，并可直接新建待办、记一笔、同步、打开设置 |
| `Ctrl+T` | 亮色 / 暗色主题切换 |
| `Ctrl+1` ~ `Ctrl+9` | 按左侧导航顺序切换模块（`Ctrl+0` 对应第十个模块） |

命令面板支持自然语言过滤，例如 `待办 今天 高优先级`、`笔记 AI`、`日记 本月`。

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

- 应用数据：统一在 `D:\Data\Dingfengbo\`（`dingfengbo.db`、`settings.json`、`logs\`、`backups\`、`covers\`），与 exe 所在目录分离，替换升级不丢数据。
- 娱乐封面：本地路径直接用；网络地址由程序带 UA/Referer 下载到 `covers\` 缓存后再渲染（`Image.network` 会被豆瓣等图床以 403 拒绝），缓存文件可随时删除，下次进娱乐页自动重新下载。
- 可通过环境变量 `DINGFENGBO_DATA_DIR` 覆盖本地数据目录。
- 自动备份：`backups\dingfengbo-时间戳-auto.db` 是 SQLite 在线快照（官方 `backup` API，WAL 模式下同样一致），每天首次启动备份一次、默认保留最近 7 份。
- 备份恢复中心（设置 → 备份恢复中心）：查看备份时间/大小/来源、`PRAGMA quick_check` 校验、立即备份、从指定备份恢复（恢复前自动再备份当前库为 `pre-restore`）、自定义备份目录与保留份数、导出完整数据 zip（数据库快照 + settings.json）。
- 数据导入导出（设置 → 数据导入导出 / 命令面板）：导出 JSON（全量，可回灌合并）、Markdown（待办/收支表格，日记/笔记按篇落文件）、CSV（按表）；导入 JSON 按 LWW 合并，导入 CSV 按列名匹配、单行失败不影响其他行。
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
- 队列可见性：顶部状态栏在有待同步或失败记录时显示「待同步 N · 失败 M」，统计用一条 `UNION ALL` 聚合同步表，状态栏周期刷新不会带来额外往返；点击该区域打开「同步明细」，可单条重试/忽略、查看冲突、同步历史、本机设备记录，并按模块选择性同步（未勾选模块的游标不推进，重新勾选后会补拉历史）。
- 冲突记录：LWW 合并时远端版本更旧会被丢弃，同时写入本地 `sync_conflicts` 表（不参与同步），在「同步明细 → 冲突」里可核对与清除。
- 拒收明细：服务端回传的 `reason` 存在本地 `sync_issues` 表（不参与同步），设置页列出「哪张表 / 哪条记录 / 为什么」；点「全部重试」把所有 error 记录放回待同步队列并清空明细，若载荷本身非法则下次 push 会再次被拒并重新出现在明细里，不会静默循环。

## 个人工作台能力（P0–P3 落地）

- 全局搜索 / 命令面板（`app/services/search.py`、`app/ui/command_palette.py`）：跨模块关键字检索，结果按模块分组，支持自然语言过滤与快捷命令；`LIKE` 走 `autoescape`，用户输入里的 `%`/`_` 不会被当成通配符。
- AI 行动化（`app/services/ai/tasks.py`、`app/ui/ai_tasks_ui.py`）：今日建议、写周报、任务拆解、日记润色、消费分析、笔记摘要、健康趋势、待看清单等任务卡，一键带上对应数据提问。
- AI 草稿写回（`app/services/ai/drafts.py`）：模型只输出 JSON 草稿，应用解析校验后在预览弹窗里等用户确认，才走 `repo.create` 落库（自动进同步队列），保存后 SnackBar 提供「撤销」。模型始终不直接写库。
- 今日聚焦（`app/services/focus.py`、`app/ui/focus.py`）：首页默认视图，含今日必做（可直接勾选）、日记提醒与写作连续天数、健康摘要、今日支出与预算进度、习惯打卡、随机回顾与 AI 今日建议；可切回原「数据概览」。
- 回顾报告（`app/services/reports.py`、`app/ui/reports.py`）：周/月/年本地统计（待办完成率、日记笔记、健康、财务与预算、娱乐、习惯），可生成 AI 叙述并导出 Markdown 到 `exports\`。
- 统一标签 / 时间轴 / 关联（`app/services/timeline.py`、`app/ui/timeline.py`）：跨模块标签聚合、按模块/标签/时间段筛选的生活时间线，记录之间可建立双向关联（`record_links`）。
- 预算与周期记账（`app/services/budget.py`、`app/services/expense_rules.py`、`app/ui/budget_dialogs.py`）：月度总预算与分类预算、超支提示、固定支出/订阅到期自动生成交易（幂等，最多补 12 期）、规则版「一句话记账」（「今天午饭花了 32 元」）。
- 任务模块（`app/ui/todo.py`、`app/services/todo_views.py`、`app/ui/habits.py`）：待办与习惯合并为一页，左侧「月历 + 四象限」、中间任务列表、右侧习惯打卡。月历按到期日显示未完成数量，点日期只看当天（再点取消）；四象限支持点击筛选，规则为「高/中优先级 = 重要」「今明到期或逾期 = 紧急」，也可在任务编辑弹窗里手选象限（留空=自动）。
- 习惯（`app/services/habits.py`、`app/ui/habits.py`）：每日打卡、连续/最长天数、周目标进度、12 周打卡日历，紧凑面板嵌在任务页右侧。
- 系统体验：亮/暗主题（`app/theme.py` 运行时换色板）、`Ctrl+K`/`Ctrl+T`/`Ctrl+1..9`/`Ctrl+0` 快捷键、开机自启（注册表 Run 项）、关闭到系统托盘（`pystray` 可选，未安装时降级）、导航模块显隐与排序（设置页）。

## 任务提醒与番茄钟

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
- 番茄钟：任务页「番茄钟」按钮，可选关联任务与 25/45/60 分钟时长，结束后系统通知并给该任务累加 `pomodoro_count`（随同步传播）。

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
- AI 助手通过 OpenAI 兼容协议对接国产大模型；被 `@引用` 的数据会发送至所配置的服务商，设置页有隐私说明，首次发送前另有一次确认。任务卡与草稿写回同样复用该协议，草稿必须经用户确认才落库。
- 主题用模块级色板常量 + 运行时 `set_mode()` 切换，界面统一按 `theme.X` 读取颜色，切主题后重建页面即可生效（静态 `from theme import X` 会把导入时的值钉死）。
