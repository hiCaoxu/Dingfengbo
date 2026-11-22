# 定风波 · 个人工作台

单机优先、可多端同步的 Windows 桌面个人工作台。数据落本机 SQLite，断网照常用；联网后按时间戳增量同步到自建服务端。绿色免安装，删除文件夹即卸载。

| 项目 | 说明 |
|---|---|
| 版本 | 1.2.0 |
| 运行环境 | Windows 10/11；源码运行需 Python 3.11 |
| 技术栈 | Flet（Flutter 渲染）+ SQLAlchemy 2.x + SQLite；同步服务端 FastAPI |
| 数据目录 | `D:\Data\Dingfengbo\`（与程序目录分离，替换升级不丢数据） |
| 数据规模 | 16 张业务表全部参与同步，另有 5 张本地同步/审计表 |

## 十个模块

| 模块 | 一句话 | 主要能力 |
|---|---|---|
| 首页 | 今天该做什么 | 今日必做（可直接勾选）、日记提醒与连续天数、健康摘要、今日支出与预算进度、习惯打卡、随机回顾、AI 今日建议；可切回「数据概览」 |
| 任务 | 待办 + 习惯同屏 | 左侧月历 + 四象限、中间任务列表、右侧习惯打卡；到期时间精确到 `HH:MM`；自定义循环（天/周/月/年）与法定节假日跳过；番茄钟 |
| 日记 | 每天一篇 | 月历选择、三栏编辑、配图、写作连续天数、「人生大事」条目 |
| 笔记 | 带高亮的富文本 | Pygments 语法高亮、行号与代码逐行对齐滚动、标签 |
| 健康 | 身体数据 | 锻炼、睡眠、体重、饮食四类记录与趋势图 |
| 娱乐 | 待看清单 | 想看/在看/已看、评分、封面（网络图下载到本地缓存） |
| 记账 | 收支与预算 | 多账户、分类收支、月度总预算与分类预算、超支提示、固定支出/订阅自动生成（幂等，最多补 12 期）、一句话记账 |
| 回顾 | 周/月/年报告 | 待办完成率、日记笔记数、健康、财务与预算、娱乐、习惯统计；AI 生成叙述；导出 Markdown |
| 时间轴 | 生活流 | 跨模块标签聚合、按模块/标签/时间段筛选、记录间双向关联 |
| AI 助手 | 会干活的那种 | 对话、`@引用` 业务数据、任务卡（周报/拆解/润色/消费分析等）、草稿写回需用户确认 |

## 快速开始

**绿色版**：解压 `定风波-v1.2.0.zip`，双击 `定风波.exe`。`_internal\` 必须与 exe 同目录，只拷 exe 会启动失败。

**源码运行**：

```powershell
python -m pip install -r requirements.txt
python main.py
```

首次启动会在 `D:\Data\Dingfengbo\` 建库并写入示例数据；同步与 AI 都在「设置」里配置，可先不配，全部功能离线可用。

## 快捷键

| 快捷键 | 作用 |
|---|---|
| `Ctrl+K` | 全局搜索 / 命令面板：跨待办、笔记、日记、收支、娱乐检索，并可新建待办、记一笔、同步、打开设置 |
| `Ctrl+T` | 亮色 / 暗色主题切换 |
| `Ctrl+1` ~ `Ctrl+9`、`Ctrl+0` | 按左侧导航顺序跳到第 1~10 个模块 |
| `F1` | 帮助与上手（按当前模块显示说明） |
| 双击顶栏 | 窗口最大化 / 还原 |

命令面板支持自然语言过滤，例如 `待办 今天 高优先级`、`笔记 AI`、`日记 本月`。

## 任务提醒与循环

- **到期时间**：待办可填 `HH:MM`，列表与首页显示日期 + 时间，排序按（日期, 时间）。
- **自定义循环**：
  - 天：每 N 天，可「跳过双休日」「跳过法定节假日」；
  - 周：每 N 周 + 多选星期；
  - 月：按日期（1–31 日或「最后一天」）、按星期（第几个 + 星期几）、按工作日；
  - 年：按日期或按星期。
  - 旧数据（「每周 N 次」「每月最后一个工作日」等）自动转成新结构；旧「不定期」按「自定义但未配置」处理，不推进下一次。
- **法定节假日与调休**：内置 2026 年（国办发明电〔2025〕7 号）与 2027 年（国办发明电〔2026〕10 号）安排；调休上班的周末按工作日处理，不会被「跳过双休日」误跳。后续年份在数据目录 `holidays.txt` 补充：每行 `YYYY-MM-DD` 为放假，行首 `!` 为调休上班日（如 `!2028-01-02`），`#` 后为注释；也可直接改 `app/services/holiday_data.py`。
- **提前提醒**：可多选「当天 / 提前 1 / 3 / 7 天」，每个时间点各提醒一次；不选即不提醒；时间留空依次退回到期时间、`09:00`。
- **系统通知**：运行期间每分钟检查一次，到点弹 Windows 通知（PowerShell WinRT toast，无额外依赖；失败退回提示音并在应用内提示）。`todo_reminders` 表按（待办, 提醒时刻）去重；应用关闭超过 12 小时的提醒不补发，避免刷屏。
- **番茄钟**：可选关联任务与 25/45/60 分钟，结束后通知并给任务累加 `pomodoro_count`（随同步传播）。

## 同步与多端

- **本地优先**：写操作先落 SQLite 再进待同步队列，界面不等网络。
- **触发**：启动一次、每 5 分钟、本地写入后 10 秒，以及顶栏「立即同步」。
- **接口**：`POST /api/sync/pull`、`POST /api/sync/push`、`GET /api/sync/check`、`GET /api/health`、`POST /api/upload`；Bearer token 鉴权，服务端按 `owner_id` 隔离数据。
- **冲突**：按 `updated_at` 最后写入获胜（LWW）；删除为软删除，同步完成后再物理清理。被丢弃的远端旧版本记入 `sync_conflicts`（不参与同步），在「同步明细 → 冲突」核对。
- **序列**：服务端用全局单调递增的 `sync_seq` 发号，客户端以 `sync_seq > cursor` 增量拉取。升级已有数据的服务端库时会把计数器锚定到存量最大值；自检可跑 `python scripts/check_sync_sequence.py <cloud.db 路径>`。
- **队列可见性**：顶栏在有待同步/失败记录时显示「待同步 N · 失败 M」（单条 `UNION ALL` 聚合，不额外往返）。点击打开「同步明细」：单条重试/忽略、看冲突、同步历史、本机设备、按模块选择性同步（未勾选模块的游标不推进，重新勾选会补拉历史）。
- **被拒记录**：push 校验失败由服务端回传 `table`/`id`/`reason`，本地标 `error` 退出队列不再无限重推；原因存 `sync_issues`，设置页列出「哪张表 / 哪条记录 / 为什么」，点「全部重试」放回队列；重新编辑该记录也会自动回到 `pending`。
- **游标与换库**：`sync_cursors` 是「已同步到服务端某序列号」的进度，与库内容强绑定。恢复备份 / 手工换库后必须重置游标（恢复流程已内置），否则云端更旧的记录会被 `sync_seq > cursor` 永久跳过，表现为「同步一直成功但列表空」。自救入口：同步明细 → 「重新拉取云端全量」。

## 数据与备份

- **应用数据**：`D:\Data\Dingfengbo\`（`dingfengbo.db`、`settings.json`、`logs\`、`backups\`、`covers\`、`exports\`、`holidays.txt`）；可用环境变量 `DINGFENGBO_DATA_DIR` 覆盖。
- **自动备份**：`backups\dingfengbo-时间戳-auto.db`，SQLite 官方 `backup` API 在线快照（WAL 模式下也一致），每天首次启动一次，默认保留最近 7 份。
- **备份恢复中心**（设置 → 备份恢复中心）：查看备份时间/大小/来源、`PRAGMA quick_check` 校验、立即备份、从指定备份恢复（恢复前自动把当前库另存为 `pre-restore`）、自定义目录与保留份数、导出完整数据 zip（库快照 + `settings.json`）。
- **导入导出**（设置 → 数据导入导出，或命令面板）：JSON（全量，可回灌合并）、Markdown（待办/收支表格，日记笔记逐篇落文件）、CSV（按表）；导入 JSON 按 LWW 合并，导入 CSV 按列名匹配、单行失败不影响其他行。
- **敏感字段**：`settings.json` 里的同步令牌与 AI Key 用 Windows DPAPI 按当前用户加密（`dpapi:` 前缀），拷到别的机器或别的 Windows 账户解不开；旧 `b64:` 值仍可读，下次保存自动升级。
- **娱乐封面**：网络地址由程序带 UA/Referer 下载到 `covers\` 后再渲染（`Image.network` 会被豆瓣等图床 403），缓存可随时删，下次进娱乐页重下。
- **图片上传**：日记选图后本地压缩（长边 1600、JPEG q82）上传到同步服务端 `/api/upload`，落服务端 `files/`，链接随记录同步。

## AI 助手

- 通过 OpenAI 兼容协议对接国产大模型，设置页填服务地址 / Key / 模型。
- 被 `@引用` 的数据会发给所配置的服务商，设置页有隐私说明，首次发送前另有确认。
- **任务卡**（`app/services/ai/tasks.py`）：今日建议、写周报、任务拆解、日记润色、消费分析、笔记摘要、健康趋势、待看清单，一键带上对应数据提问。
- **草稿写回**（`app/services/ai/drafts.py`）：模型只输出 JSON 草稿，应用解析校验后在预览弹窗等用户确认才走 `repo.create` 落库（自动进同步队列），保存后 SnackBar 提供「撤销」。模型始终不直接写库。

## 开发

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q          # 或：python -m unittest discover -s tests -t .
```

测试依赖锁在 `requirements-dev.txt`：**不要装 `pytest-timeout` 2.7 以下版本**，它在 pytest 8 的 `addini` 类型校验下会让 pytest 在收集阶段崩溃，本项目也不需要超时插件。

目录结构：

```
main.py              桌面端入口
app/
  main.py            固定左导航 + 右内容区
  theme.py           色板常量 + 运行时 set_mode()
  config.py          DATA_DIR / settings 读写（DPAPI）
  db/                models / engine / session / repo / migrations / backup
  services/          业务逻辑：sync、search、focus、reports、budget、habits、ai/…
  ui/                各模块页面与弹窗
server/main.py       同步服务端（FastAPI）
docker/              Dockerfile、compose、Caddyfile、deploy.ps1
packaging/           PyInstaller spec、build.ps1、字体子集化
scripts/             运维脚本（同步序列自检等）
tests/               65 个测试文件
```

约定：

- 主题用模块级色板 + 运行时 `set_mode()`，界面统一按 `theme.X` 读色；静态 `from theme import X` 会把导入时的值钉死，切主题失效。
- **时间口径**：`created_at` / `updated_at` 统一存 **naive UTC**（多端 LWW 需要公共时间轴），
  界面与 AI 上下文展示前一律用 `dates.local_datetime_from_utc()` 转本地时间；
  CSV/JSON 导出保持 UTC，便于原样回灌。写库时两个字段用同一个（已对齐的）时钟。
- **日志**：统一走 `app/logging_setup.py`（滚动文件 2MB × 5 份，控制台同格式）。
  排查时设 `DINGFENGBO_LOG_LEVEL=DEBUG` 即可看到写库、解析、同步分页等细节，不必改代码。
  一次同步的全部日志带同一个 `[sync xxxxxxxx]` 前缀，按 id 就能还原一轮。
  **日志里不写用户正文、凭据和完整 URL**：地址一律过 `logging_setup.safe_url()`（只保留
  `scheme://host`），记录只写表名 + 主键前 8 位。`tests/test_logging.py` 会强制「每个模块都有
  module logger」「不允许完全吞掉异常」。
- **注释**：模块/函数用中文 docstring 讲**为什么**（LWW 决胜、时间戳方向、金额单位边界、
  SQLite 怪癖这类反直觉处必须写），不复述代码在做什么；改到这些地方时顺手补一句，
  比事后集中补注释更省力。
- 界面文案新增生僻字会影响字体子集，改完跑 `python packaging\subset_font.py`（见下）。
- 金额：库里存**整数分**（`models.MoneyCents` 类型装饰器，写元读元），同步载荷与 CSV/JSON 导出仍是「元」——旧客户端按元推送不会被读成 1/100；`app/services/money.py` 统一 Decimal 舍入与比较。迁移由 `PRAGMA user_version` 保证只执行一次。
- 已知技术债：Caddy TLS 仅文档就绪，未在真实域名生产演练。

## 打包发布

```powershell
.\packaging\build.ps1
```

产物在 `dist\`：

- `Dingfengbo\`：PyInstaller **onedir** 产物，含 `定风波.exe` / `Dingfengbo.exe`、`_internal\`（Python 运行时、Flet 客户端、字体）与 `使用说明.txt`。
- `定风波-v{版本号}.zip`：上面的整个文件夹，版本号读自 `app/__init__.py` 的 `__version__`。

分发要点：

- 解压即用，没有安装目录 / 开始菜单 / 卸载入口的概念，删文件夹即卸载。
- `_internal\` 是程序本体，必须与 exe 同目录。
- 升级 = 用新文件夹替换旧文件夹；数据始终在 `D:\Data\Dingfengbo\`。
- 开机自启：把 exe 快捷方式放进 `shell:startup`（Win+R 输入 `shell:startup`）。
- 用 onedir 而非 onefile：onefile 每次启动都要解压全部内容到临时目录，冷启动慢且易被杀软拦；代价是发布物变成文件夹。
- exe 带版本资源，右键属性可见版本号。

### 字体子集化

`assets\fonts` 里是裁过的霞鹜文楷（18.42MB → 3.46MB，7727 个字形），不是全量字体：

- 范围 = ASCII / Latin-1 / 通用与 CJK 标点 / 全角 + **GB2312 全集**（6763 汉字）+ 源码与 README 实际出现的所有字符 + `EXTRA_CHARS` 手工补充字。
- 全量原字体在 `packaging\fonts\`（不进包，仅供重新子集化）。
- 重新生成：`python packaging\subset_font.py`；只校验缺字与体积上限：`--check`。
- GB2312 之外的生僻字不会被裁掉，由 Flutter 回退系统字体渲染（字形不同但可读）；要固定成文楷就把字加进 `EXTRA_CHARS` 重跑脚本。
- `tests\test_font_subset.py` 反查 cmap，确保代码里出现过的字一个不缺。
- 许可：霞鹜文楷为 SIL OFL 1.1，`assets\fonts\OFL.txt` 随包分发；子集由原字体直接生成，字体内部的版权与许可字段完整保留。

## 服务端部署

本地起服务：

```powershell
python run_server.py
# 默认 0.0.0.0:8000，可用 DINGFENGBO_HOST / DINGFENGBO_PORT 覆盖
```

线上用 Docker（服务器上只需 Docker 与 SSH）：

```powershell
.\docker\deploy.ps1 -HostIp <服务器IP> -User root
```

脚本只在远端创建 `/opt/dingfengbo-sync` 并启动容器 `dingfengbo-sync`，不改动主机其他环境；数据卷 `/opt/dingfengbo-sync/data`，监听 `8000:8000`，容器内以非 root（uid 10001）运行。桌面端「设置」填 `http://<服务器IP>:8000`，点「测试同步连接」验证。

公网访问需在云厂商安全组放行 TCP 8000（腾讯云轻量在「防火墙」加规则）。

`files/` 只增不删：服务端无法判断图片是否仍被引用，按修改时间人工清理，删掉的旧图客户端会重新上传。

### 可选：Caddy 自动 HTTPS

token 即全部数据访问权，公网明文 HTTP 有被嗅探风险。需要 TLS 时启动 compose 的 `tls` profile（自动申请证书）：

```powershell
# 在服务器上，docker/docker-compose.yml 同目录
$env:DOMAIN = "sync.example.com"
docker compose --profile tls up -d
```

收口顺序（先验证再关闭，避免把自己锁在门外）：

1. 启动 `tls` profile，用 `https://sync.example.com/api/health` 验证证书与反代；
2. 桌面端把服务地址改成 `https://sync.example.com`，点「测试同步连接」确认；
3. 确认可用后再删除 compose 里 8000 的端口映射（或从安全组移除 8000）。

写入权限异常时先 `chown -R 10001:10001 /opt/dingfengbo-sync/data`。

## 常见问题

**数据在哪个文件？换电脑怎么迁？** 全部在 `D:\Data\Dingfengbo\dingfengbo.db`。换机拷贝这个文件（连同 `settings.json` 则连配置一起带；但 token/Key 受 DPAPI 保护，换机需重填）。也可以只依赖同步：新机装好、填同一服务地址与令牌，等待首次全量拉取。

**列表突然空了，但同步一直显示成功？** 大概率是游标与库不匹配（恢复过备份或手工换过库）。打开顶栏「同步明细」→「重新拉取云端全量」，10 秒内自动补齐。

**只把 exe 拷到桌面，启动报错？** `_internal\` 必须与 exe 同目录，拷整个文件夹。

**娱乐封面显示不出来？** 图床有防盗链，程序已带 UA/Referer 下载到 `covers\`；若仍失败多为网络问题，可换成图片直链。

**提醒没弹出来？** 检查待办是否填了提醒节点、应用是否在运行；Windows 通知被系统「专注助手」/通知设置屏蔽时只会有提示音。

## 许可与致谢

- 界面字体：[霞鹜文楷](https://github.com/lxgw/LxgwWenKai)，SIL Open Font License 1.1，许可证见 `assets\fonts\OFL.txt`。
- 代码字体：Consolas（系统自带）。
- 农历换算：[zhdate](https://github.com/CutePandaSh/zhdate)。
