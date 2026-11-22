# 定风波 · 深度代码审查报告（复核修订版）

- **日期**：2026-11-22
- **复核修订**：2026-11-22（二次复核，修正误报、影响范围、严重度与修复方案）
- **范围**：`app/`（76 个 Python 文件）、`server/`、`packaging/`、`scripts/`、入口与配置
- **方法**：7 个并行专项审查（数据库层 / 同步子系统 / 服务层A·财务循环提醒 / 服务层B·数据IO与内容 / AI 子系统 / UI 核心 / UI 其余）；二次复核逐项对照当前代码、迁移链和测试，并做最小复现实验
- **基线**：HEAD 工作区（v1.2.0）

## 一、总体结论

代码整体工程素养明显高于同类个人项目：整数分存储 + Decimal 累加的金额链路、确定性 UUID 幂等键、部分唯一索引、DPAPI 凭据保护、SSRF 逐跳校验、防解压炸弹、渲染 token 防串台、`background_section` 错误兜底……注释里大量记录了「为什么这样做才对」的事故复盘，历史坑（游标重置、发号锚定、限流 fail-open）几乎都有对应防御。

初审列出的 11 项原 P0 均有真实代码依据，但二次复核发现部分触发机制、影响范围和修复建议不够准确，且严重度整体偏高。**复核后保留 2 项 P0（P0-3、P0-8），其余原 P0 调整为 P1/P2；为保持追踪稳定，原编号不变，以各条目的「复核后等级」为准。**其余中低危项以第四至六节的修订内容为准。风险集中在四条主线：

1. **同步合并的边界闭环**：LWW 平局无回执、pull 无逐条容错（单条毒记录持续卡住同步）、导入时间戳不受钳制。
2. **「启动路径 × 唯一约束 × 软删除」的组合缺陷**：Diary 全表唯一约束与软删除冲突；`_ensure_indexes` 遍历范围错误，已在真实库沉淀 10 个常量表达式僵尸索引。
3. **模块级可变全局的跨线程纪律**：日记/笔记自动保存缓冲存在竞态、番茄钟全局会话竞态。
4. **写路径校验与外部数据边界**：非有限数值和负数校验不足、编辑目标失效后假保存、CSV 公式注入。

## 二、复核说明与误报纠正

### SQLite DQS 僵尸索引：不是「启动即崩」

`app/db/migrations.py:214-230` 的 `_ensure_indexes` 遍历 `Base.metadata.tables.values()`（全部 21 张表），对没有 `owner_id`/`sync_seq` 列的 5 张本地表（`foods`、`sync_sequences`、`sync_issues`、`sync_conflicts`、`todo_reminders`）也执行 `CREATE INDEX ... ("owner_id", "sync_seq")`。

**实测**（本机 SQLite 3.45.1）：对不存在列建索引，不加引号会报 `no such column`；但带双引号时命中 SQLite 的「双引号字符串」宽松解析特性（DQS misfeature），静默生成常量表达式索引。`PRAGMA index_xinfo` 对应列显示为表达式（`cid=-2`），因此当前应用不会崩溃。

**已在真实库实证**：`D:\Data\Dingfengbo\dingfengbo.db` 的 `sqlite_master` 中存在 10 个僵尸索引（`ix_foods_owner_sync_seq` 等），当前 `quick_check` 通过、查询正常。风险是 schema 污染和**条件性的前向兼容隐患**：如果未来连接或运行时关闭 `SQLITE_DBCONFIG_DQS_DDL`，已有 schema 在解析或语句准备时可能报 `malformed schema` / `no such column`，导致数据库无法正常访问；这不代表库文件已损坏，也不是升级 PyInstaller 后必然发生。修复见 P0-1。

### 二次复核结论

- Diary 唯一约束、月/年循环漂移、LWW 平局无回执、备份恢复退化复制等核心事实成立；相关触发条件和修复方案已在各条目收窄或改写。
- P0-5 的日记竞态成立，但笔记 pending 是读取实时控件的回调，不能套用完全相同的丢值过程；两者都应改版本化快照与串行保存，而不是持锁写库。
- P0-6 的表格行列缺陷与首渲染无保护成立，但 Flutter 客户端断言不等于异常沿 Python 调用链传播，「必白屏」未获复现支持。
- P1-11「体重曲线区间外不可见」是明确误报：`charts._resolve_ylim` 会自动扩展纵轴，已撤回。
- P1-19 当前金额计算正确，只是注释误导，降为 P3；P1-20 的「每分钟空转」不成立。
- 墓碑物理清理不能靠普通全量重拉解决；游标重置保存失败会被吞掉，因此第七节的「闭环」改为「正常路径已具备，失败处理未闭环」。
- 复核运行相关现有测试 48 项全部通过，并最小复现月/年漂移、DQS 常量索引和体重纵轴自动扩展；这些测试通过不代表覆盖本次问题场景。

## 三、原 P0 条目复核与修订（编号保留）

### P0-1 `_ensure_indexes` 遍历范围错误 + 僵尸索引沉淀（已实证）
- **复核后等级**：P2（schema 污染与兼容隐患，非当前启动故障）
- **位置**：`app/db/migrations.py:214-230`
- **问题**：5 张本地表被错误地建上引用不存在列的索引；启用 DQS 时实际为常量表达式索引，真实库已有 10 个。
- **修复**：循环改为遍历 `SYNC_MODELS`；在仍可读取 schema 的运行时中显式 `DROP INDEX IF EXISTS` 清除这 10 个索引（`ix_{foods,sync_sequences,sync_issues,sync_conflicts,todo_reminders}_owner_{sync_seq,sync_status}`）；补「本地 5 表无目标复合索引、16 张同步表索引齐全」的升级回归测试。

### P0-2 Diary 全表唯一约束 × 软删除 → 删除后重写撞唯一约束
- **复核后等级**：P1
- **位置**：`app/db/models.py:118` + `app/services/diary.py:16-18`
- **问题**：`UniqueConstraint("owner_id", "date")` 约束全表（含墓碑）。删除某日日记后，墓碑仍占用 `(owner, date)`，而 `ensure_diary` 只查活跃行；在墓碑物理清理前，同日重建会触发 `IntegrityError`，墓碑同步到其他端后也一样。
- **修复**：Diary 改 `WHERE is_deleted = 0` 的部分唯一索引；迁移先收敛**重复活跃行**，并重建旧 `diaries` 表以移除原全表自动唯一索引，只新增部分索引不够。`ensure_diary` 可复用墓碑，但必须通过统一更新路径重置内容、`updated_at` 和 `sync_status=pending`。补「旧约束升级、活跃行唯一、多墓碑共存、删除后重建」测试。

### P0-3 客户端 pull 无逐条容错，单条毒记录持续卡住全部同步
- **复核后等级**：P0
- **位置**：`app/db/repo.py:413,446-458` + `app/services/sync.py:215-216`
- **问题**：服务端 push 每条有 savepoint 隔离，客户端 pull 却整页一个事务：被选择同步的表中，一条持续引发 `updated_at` 缺失/非法、字段转换、约束或 flush 错误的记录会使整页回滚、游标不推进，pull/push/purge 随之中断；毒记录未修复、清理或排除前每轮都会复发。
- **修复**：优先在客户端 `_pull` 中逐条调用 `apply_remote`，每条独立 savepoint 且**在 savepoint 内完成 flush**。不要简单修改共享 `apply_remote` 吞异常，以免服务端 push 既无 accepted 也无 rejected。保留「数据库提交后再保存游标」的顺序；跳过并推进游标的坏条目必须记录远端方向、seq 和原因，提供 dead-letter 查询及重拉/全量重拉手段，避免从卡死变成静默漏数据。

### P0-4 LWW 时间戳平局：无决胜、无回执，记录持续 pending
- **复核后等级**：P1
- **位置**：`app/db/repo.py:426-445`
- **问题**：`obj.updated_at == remote_time` 且内容不同时直接 `continue`，没有 applied、冲突或回执；没有后续编辑打破僵局时，推送方会持续 pending，两端内容分歧。统一时钟提高了同时间轴的可能性，但微秒时间戳使正常人工并发平局仍较少见。
- **修复**：不能使用 `(updated_at, id)` 作为完整方案——同一记录多端编辑的 id 相同，业务键合并后行 id 也未必一致。应实现所有端共同的确定性全序，如不可变且随同步传播的 `change_id/writer_id`，或不改 schema 时使用规范化业务载荷散列作最终决胜。服务端判定推送方落败时，条目须同时进入 `accepted`（精确 `mark_clean` 出队）和 `superseded`（记冲突）；客户端 pull 则按本地/远端胜负分别保留 pending 或覆盖并留痕。补同 id 同时间不同内容、业务键合并和回执出队测试。

### P0-5 日记/笔记自动保存缓冲跨线程竞态
- **复核后等级**：P1
- **位置**：`app/ui/diary.py:56-71` + `app/ui/notes.py:69-78,257-269`
- **问题**：模块级 pending 和 Timer 由 UI 线程与 Timer 线程共同访问。日记存在「Timer 读旧快照 → UI 写新值 → Timer 清空新 pending/取消新 Timer → 只写旧值」的交错；是否永久丢失还取决于之后是否有成功的 blur/其他保存。笔记 pending 是执行时读取控件的回调，上述精确交错可能反而读到新值，但清空 pending、逐字段写库和旧 Timer 取消新 Timer 之间仍有其他丢值窗口。
- **修复**：与 P1-13 合并为共用自动保存状态机。锁只保护 pending、Timer 和单调版本号，**不得持锁写库**；UI 调度时捕获不可变字段快照，每次 Timer 校验身份/代次，写库在锁外串行执行。失败时仅在没有更新版本的情况下重排队，blur 直写也纳入同一串行协议。

### P0-6 笔记首次预览无异常保护 + Markdown 表格行列不齐
- **复核后等级**：P1（具体 UI 影响待实机复现）
- **位置**：`app/ui/notes.py:335`（对照 `notes.py:121-132, 353-377`）
- **问题**：编辑页首渲染同步执行 `render_markdown` 且无 try/except；`_table` 不校验列数，短行/长行可造成 `DataTable` 构造或客户端渲染异常。Flutter 客户端断言不会沿 Python 的 `build_preview → main.refresh` 传播，是否预览区失败或整窗白屏取决于实际 Flet/Flutter 版本，不能写成确定结论。
- **修复**：`_table` 在构造前把每行补齐或截断到表头长度；首渲染增加 try/except，失败时显示纯文本/错误占位并记录日志；保留异步路径兜底。增加短行、长行、首渲染和异步刷新测试，并用当前锁定版本做 UI 复现。

### P0-7 统一保存骨架在编辑目标失效时假保存/误新建
- **复核后等级**：P1
- **位置**：`app/ui/forms.py:130-142`
- **问题**：`session.get` 取到 `is_deleted=True` 的行仍照常更新，保存成功但活跃列表不可见；行不存在时通常新建 UUID 记录，是否构成业务重复取决于模型。睡眠编辑的 `resolve` 还可能改写同日另一条活跃记录。
- **修复**：`record_id` 非空时，目标不存在或已软删都明确失败，不 create、不 resolve；`resolve` 仅用于新增。`save_record` 返回结果或抛领域异常，所有调用方在失败时保留弹窗、不标脏、不关闭，并提示用户。日记/笔记自动保存的软删目标检查另行纳入 P0-5。

### P0-8 备份恢复的两处数据安全缺口
- **复核后等级**：P0
- **位置**：`app/db/backup.py:187-211, 226-231, 79-94`
- **问题**：
  1. `_atomic_replace` 在 `os.replace` 失败后退化为 `shutil.copyfile` 原地覆盖目标库。只有 `PermissionError` 会重试三次，其他 `OSError` 首次即进入该分支。`engine.dispose()` 不能强制关闭飞行中的连接；copyfile 会先截断目标，磁盘满、进程终止或写异常可留下半份主库，`finally` 还删除临时源。
  2. restore 的 pre-restore 安全备份会执行 `_prune`。当 source 位于同一备份目录、文件名匹配且新建安全备份后已超出保留窗口时，正在恢复的 source 会被删除，随后复制抛 `FileNotFoundError`；并非每次恢复都会触发。
- **修复**：删除 copyfile 退化分支，原子替换失败保留现有主库并直接报错；恢复前的 `backup_database` 传 `prune=False`（或 `_prune` 显式保护 source）。补替换失败、复制中断和 `backup_keep=1` 恢复旧源测试。

### P0-9 内置月/年循环日号漂移
- **复核后等级**：P1
- **位置**：`app/services/dates.py:184-192,196-219`
- **问题**：内置月/年推进从当前到期日取日号再夹到月末：1/31 → 2/28 → **3/28**，2/29 → 2/28 后也不再恢复；农历月/年推进存在同类问题。自定义规则的 `month_days/year_day` 和周期记账的持久化 `rule.day` 不受此问题影响。
- **修复**：为内置规则持久化原始锚点（可复用 `repeat_config` 保存 `anchor_day`/原始月日），复制下一期时继续传递。当前模型没有可靠的系列链，不能依赖「从系列第一期反推」。存量已漂移数据无法普遍无歧义还原，只能默认当前日号或让用户校正；补连续多期及农历测试。

### P0-10 JSON 导入不钳制 updated_at → 受影响记录无法正常同步
- **复核后等级**：P1
- **位置**：`app/services/data_io.py:142,206-219` + `repo.py:413,426`
- **问题**：导入允许文件决定 `updated_at`。未来版本上传后，受影响记录在本机或其他设备上产生的较早时间编辑会被 LWW 判旧，导致同步拒绝和版本分叉，并可能在全量拉取、游标重置或其他客户端同步时被未来版本覆盖。同步关闭时不会自行回滚，被拒绝的推送也不会立即把服务端内容写回，因此不能概括为「所有编辑都必被回滚」。
- **修复**：导入时解析并校验 `updated_at`，超过「与服务端对齐的当前时间 + 容差（如 5 分钟）」时钳制到当前对齐时间并记录警告/errors。补「未来 JSON 导入 → 上传 → 本地编辑 → 再同步」完整链路测试。

### P0-11 CSV 导出无公式注入防护
- **复核后等级**：P1
- **位置**：`app/services/data_io.py:262-266`
- **问题**：用户可控文本原样写入 CSV；以 `=`、`+`、`-`、`@` 等危险形式开头并被 Excel 等软件打开时，可能被解释为公式/DDE。具体外部调用和交互取决于表格软件及安全设置，不是必然无交互执行命令。
- **修复**：按列类型区分：真正的数值保持数值，文本统一做安全导出，覆盖危险前缀及前导空白、制表符、回车、换行等变体；必要时加 `'` 前缀并说明这会改变文本单元格字面内容。增加各前缀和变体的回归测试。

## 四、原 P1 条目复核与修订（编号保留）

| # | 位置 | 问题 | 修复建议 |
|---|---|---|---|
| P1-1 | `run_server.py:10`、`config.py:18` | 默认明文 HTTP 监听 0.0.0.0，token（唯一身份）可被链路嗅探 | 客户端保存非 https 非 localhost 的 server_url 时显著警告；文档强制反代 TLS |
| P1-2 | `server/main.py:116-117` | push 请求体无条数上限（pull 有 ≤500），大批量及超大单条数据可拖垮服务 | Pydantic 2 可用 `Field(max_length=500)` 限制列表条数；还需在反代/ASGI 层限制请求体字节数，并限制单条记录和字符串字段大小 |
| P1-3 | `sync.py:210-224`、`server/main.py:213-229` | 选择性同步：排除表整页数据每轮被查出传输，`has_more` 永真靠「无进展」兜底退出 | pull 请求带 tables 白名单，服务端只查勾选表 |
| P1-4 | `sync.py:280-282` | `_auto_pause` 仅在 sync_once 入口检查，飞行中的多页 pull 不会中断，可能写进正被恢复替换的库 | `_pull`/`_push` 每页前复查 `_auto_pause`；恢复时等待超时不得继续替换 |
| P1-5 | `scheduler.py:98-106` | 周期同步 worker 裸调 `sync_once()` 无 try/except，线程死亡后顶栏状态永不刷新（打包后完全不可见） | 包 try/except 记日志，finally 回调 `_after_sync` |
| P1-6 | `ui/widgets.py:478-486, 504-506` | `delete_with_confirm` 的 action/accept 无 try：DB 报错时用户看到「点了确定没反应」 | 统一包 try/except + snack 报错 |
| P1-7 | `app/main.py:461-469` | 命令面板 `on_dismiss` 在 `open_dialog` 之后赋值且无 update；Esc 关闭路径下可能未注册，`state["palette"]` 卡 True，Ctrl+K 失效到重启 | 构造弹窗时、首次打开前注册关闭回调；正常关闭和打开异常路径都幂等复位 palette |
| P1-8 | `ui/pomodoro.py:61-65, 146-177` | 停止后 1 秒内重启：新旧 worker 可并发递减同一 `remaining`，可能双 `done()`，旧 worker 还可能把新 run 停止 | per-run 自增 id 配合锁；worker 在循环、sleep 后、收尾及副作用前校验代次，stop 时 bump |
| P1-9 | `ui/budget_dialogs.py:57-61, 133, 138` | 非法文本静默转 0 → `upsert_budget` 对 ≤0 走软删：手误一次整月预算被无提示清空；inf/nan 无上界；「每周」执行日按 1-31 截断 | 解析失败 snack 报错；金额限区间拒绝 inf/nan；按 period clamp（week 1-7） |
| P1-10 | `ui/reports.py:306` | **复核后降为 P3**：`ensure_consent` 收到 list，弹窗显示 Python 列表原文；不改变实际发送范围 | 改传中文描述 |
| P1-11 | `ui/health_ui.py:295`、`services/charts.py:63-75` | **撤回（误报）**：虽然传入 56–66kg，`_resolve_ylim` 会按数据最小/最大值自动扩展，区间外曲线仍可见 | 无需缺陷修复；若要调整视觉留白，可作为独立显示偏好处理 |
| P1-12 | `ui/forms.py:654-662, 712-717, 765-775, 811` | 数值校验不足：金额 `nan/inf` 会被 `money.to_decimal` 静默改写为 0；其他 Float 字段可能接受非有限值；负时长/负克重可入库，体脂无 0–100 范围，时长 `inf` 还可能抛 `OverflowError` | 公共数值校验函数：解析异常显式报错，`math.isfinite` + 字段区间 |
| P1-13 | `ui/diary.py:78-83`、`ui/notes.py:69-78` | 写库前 pending 已清空；日记无异常保护，笔记虽捕获异常但不恢复 pending，失败均可能丢待写内容且无完整重试 | 与 P0-5 合并为版本化自动保存状态机；失败重排队不得覆盖更新版本，增加退避和 UI 提示 |
| P1-14 | `ui/home.py:469-473`、`ui/diary.py:92-97`、`app/main.py:136-141` | 首页点击、日记页加载和启动均可能创建空日记并产生空转同步；同一天重复点击不会无限建行，主要是浏览不同日期留下空行 | 首页仅导航，日记页只查询；首次真实输入/插图时创建，启动预建逻辑同步调整 |
| P1-15 | `services/ai/drafts.py:217, 282-286` | 草稿 `account_id` 不校验存在性（无外键），提示注入可把交易挂到不存在/错误账户，报表静默算错；`due_time` 不校验 HH:MM | 落库前 `session.get(Account)` 校验；正则校验时间 |
| P1-16 | `services/ai/client.py:16-36` | 自定义 `ai_base_url` 无校验，真实 Key + 全部 @引用数据可被诱导发往任意地址 | 校验 scheme，并对非预期主机、私网/回环地址警告；不要把当前 covers URL 校验当作完整 SSRF 防线，网络安全修复见 P1-17/P2 |
| P1-17 | `services/markdown.py:113-115` | 独立图片行 src 不经校验直传 `ft.Image` 发起网络请求；同 URL 在封面处被拦截、在笔记里直连 | 与封面共用修复后的安全网络客户端；仅调用一次 `is_safe_remote_url` 不能解决 DNS 失败放行和 rebinding TOCTOU |
| P1-18 | `services/data_io.py:88` | `%d/%m/%Y` 先于 `%m/%d/%Y`：只有两种格式都能解析且结果不同的斜杠日期会被静默按日/月解释；`11/22/2026` 等单候选输入可正确解析 | 收集两种格式的不同候选；只有一个有效候选时接受，有两个不同候选时要求选择地区格式或报错 |
| P1-19 | `services/money.py:84-91`、`budget.py:106` | **复核后降为 P3**：当前 ORM `SUM` 已经 MoneyCents 转回元，代码和测试正确；错误注释可能诱导未来调用方重复换算。`from_cents` 仍被模型使用，不是死代码 | 注释明确「ORM 聚合已是元，只有绕过类型装饰器的原生整数分聚合才需 `from_cents`」；死导入可另清理 |
| P1-20 | `services/notify.py:49-62`、`scheduler.py:141-146` | Toast 可同步阻塞最长 15 秒且失败后该 `(todo, fire_at)` 不再重试；`notify` 返回 False 时可能已播放提示音，失败项也不会每分钟重复调用 notify。调度器已在后台线程 | 返回 Toast 成功/提示音降级/完全失败的区分状态，只对可重试的完全失败调 forget 并退避；批量提醒用后台队列避免逐条串行阻塞 |

## 五、原 P2 条目（含复核后升降级，择要）

| 位置 | 问题摘要 |
|---|---|
| `repo.py:446-454` vs `sync.py:269-274` | pull 覆盖本地未推送的 pending 编辑零留痕，与 push 侧 superseded 留痕不对称；应与 P0-4 统一设计冲突策略并写 SyncConflict |
| `repo.py:551-571`、`server/main.py:364-377` | **复核后升为 P1**：墓碑 30/90 天物理清理后，离线超窗设备可复活已删记录。普通全量重拉不会恢复已物理清除的删除知识，不能作为完整修复；需长期删除日志/墓碑，或 per-owner 删除高水位 + 设备 last-seen 水位，拒绝过旧设备直接写入并做带缺失记录对账的快照恢复 |
| `migrations.py:125-163` | `_assign_missing_sequences` 客户端每次启动逐行重发序列号；应仅服务端执行 |
| `backup.py:157` | `file:{path}?mode=ro` 未做 URI 编码，路径含 `%`/`#`/中文时完好备份被拒恢复 |
| `config.py:264-271` | **复核后升为 P1**：settings 文件存在但读取/解析失败时回退全默认，后台保存可覆写全部配置和凭据；此时应禁止后台保存、保留原文件并显式报错，只有文件不存在才允许从默认值创建 |
| `sync.py:292` vs 单页 16×200 条 | pull 超时一刀切 10s，首次全量重拉误报离线；应放宽读超时 |
| `sync.py:316-321`、`scheduler.py:57-66` | 同步失败无退避无抖动，断网时每 5 分钟硬敲 + settings.json 高频全量重写（含 DPAPI） |
| `server/main.py:200-235` | pull/check/health 无限流：token 爆破与全量拖库不受速率约束 |
| `server/main.py:63-76` | 静态文件无 nosniff 头，URL 泄漏即永久公开 |
| `server/main.py:416-419` | `_public_base` 回显攻击者可控 Host 头并持久化进用户数据 |
| `server/main.py:461-464` | upload 并发同摘要共享 `.tmp` 路径，Windows 下偶发 PermissionError 500 |
| `sync_detail.py:312-315` | 「全部取消勾选」保存后语义等于「全部同步」，UI 显示与实际行为相反 |
| `services/covers.py:121-160,175-215` | SSRF 校验与实际连接两次独立 DNS 解析（rebinding TOCTOU），解析失败反而放行；应默认拒绝解析失败，把已验证 IP 固定到实际连接并保留 Host/SNI 和证书校验，每个重定向跳重新校验 |
| `services/covers.py:204-229` | 同 URL 并发下载写同一 `.tmp` 非原子，可把损坏文件固化进缓存 |
| `services/reports.py:309-324` | 无总额预算时 `total_budget` 回落长期预算，「剩余」严重失真 |
| `services/budget.py:336-337` | `save_rule` 直接赋值 `next_date` 违反自身 mark_dirty 约定，靠语句顺序侥幸不漏同步 |
| `services/budget.py:256-257`、`data_io.py:353-365`、`ai/drafts.py:270-272` | 三处「兜底取最早/第一条账户」：孤儿交易静默记错账（CSV 导入无排序更不可复现） |
| `services/expense_rules.py:40, 50-51` | 一句话记账兜底取 max 数字：「2025年收入10万」→2025 元；丢负号、丢「N块M」 |
| `services/holidays.py` | 节假日数据只到 2027，之后静默退化为纯双休判断且无提示 |
| `services/reminders.py:109-110` | `fire_at` 本地时间与 `created_at` UTC 混用，prune 窗口偏移 8 小时 |
| `services/finance.py:38-44` | 余额聚合为已删除/孤儿账户生成幽灵键 |
| `services/data_io.py:224` | 导入把 superseded/unchanged 行也 requeue，冗余流量 + 统计失真 |
| `services/data_io.py:84, 472-486` | 导出直写目标路径非原子，中途失败留截断文件 |
| `ui/todo.py:145-155` | 任务页每次刷新全表加载 ORM 后 Python 侧过滤，数千条后卡顿 |
| `ui/home.py:121-139` | 热力图已完成待办全量进内存逐条换算（其余 5 张表均已 GROUP BY 下推） |
| `ui/todo.py:496, 525-532` | 标签筛选悬空：删除标签下最后任务后列表恒空且无可见提示 |
| `ui/diary.py:421-426` | 日记检索裸 LIKE 未转义 `%`/`_`（search 层已用 autoescape） |
| `ui/forms.py:613-614` | `kind` 非规范值直接下标 KeyError，打开编辑弹窗即崩（todo 页直连路径无兜底） |
| `ui/backup_center.py:190-215` | 恢复成功后立即恢复自动同步，全量拉取可用云端当场覆盖刚恢复的数据 |
| `ui/ai_tasks_ui.py:176-220` | 草稿预览截断 200 字符 + 隐藏 tags + content 无长度上限：确认的与落库的不一致 |
| `ai/openai_compat.py:23,41-46` | 非流式 120s × 3 次重试最坏 6 分钟无取消途径，busy 期间无法发新消息 |
| `ai/ai_assistant.py:244-247` | 历史回合（含此前引用数据）在后续无引用提问中持续外发，超出 consent 范围；`[-8:-1]` off-by-one |
| `ui/async_utils.py:99-131` | `background_section` 把所有异常降为 debug，真实渲染 bug 静默不可见 |

## 六、P3 打磨项（一句话清单）

- `config.py:17`：硬编码 `D:\Data\Dingfengbo`，无 D 盘机器静默回退 home。
- `logging_setup.py:13-14`：`if root.handlers: return` 过宽，uvicorn 先注册则日志配置失效；桌面/服务端两进程共用同一日志文件轮转互踩。
- `migrations.py:117`：`next(...)` 无默认值，异常库抛 `StopIteration`。
- `repo.py:259-294, 497-571`：若干 N+1/逐行删除（`list_conflicts`、`purge_deleted`、`list_error_records` 整对象加载）。
- `models.py:95-113`：大量 `Mapped[str]` 未设 `nullable=False`，DDL 可空与类型承诺不符。
- `budget.py:28-30`：遗留 `_cents` 银行家舍入与主口径不一致（无调用方，建议删除）。
- `expense_rules.py:67`：12 月 31 日说「1月1日提醒我」解析为去年。
- `budget.py:236-239`：周规则今天即扣款（delta=0 当次生成今日交易）。
- `recurrence.py:158-166`：`_skip_blocked` 400 步超限静默返回被阻止日期，应 warning + None。
- `focus.py:33-45`：写作连续天数全表加载日记到 Python 集合。
- `budget.py:83-88`：金额 ≤0 静默软删预算（负数也当删除）。
- `timeline.py:345-356`：`record_title` 不过滤软删 + `links_for` N+1；`tag_counts` 窗口口径不一 + `counts[row.name] += 0` 死代码。
- `search.py:233-259`：`since/until` 只对 todos/diaries/transactions 生效，notes/娱乐无日期过滤。
- `system_integration.py:139-168`：托盘就绪检测等待的是失败事件，成功路径固定白等 0.6 秒；慢失败可能先隐藏再恢复窗口，但线程异常路径已有 `_show(page)`，不能笼统断言窗口必唤不回。建议改 setup/ready 事件并在线程退出时统一恢复窗口。
- `life.py:64-97`：三个写函数漏 `mark_pending_write()`，靠 5 分钟周期兜底。
- `covers.py:102-113`：下载不校验魔数，任意字节可缓存为 `.jpg`。
- `data_io.py:48-84`：导出全表物化 + indent=2 整串生成，大库内存放大 3-4 倍。
- `main.py:195-199`：顶栏日期/问候语跨天不更新。
- `home.py:50, 394-396`：`_calendar_card` 的 `set_date` 参数从未接线，纯死代码。
- `notes.py:303-319`：标签输入每击键双重 `page.update()` 全页刷新。
- `command_palette.py:279-297`：关闭面板不取消防抖 Timer，空跑一次全库搜索。
- `state.py:17-28`：`__getitem__` 抛 AttributeError 而非 KeyError；`get()` 对 None 值不回退 default。
- `forms.py:1243-1257`：设置保存一次最多三次整页重建。
- `pomodoro.py:60-65`：`sleep(1)` 累计漂移，25 分钟实际多十几秒。
- `uploads.py:73-77`：上传完成时回调可能作用于已关闭弹窗。
- `timeline.py:178, 362`：关联弹窗 Esc 关闭不置 closed 标记；列表行建关联后角标不刷新。
- `data_io_dialog.py:39-53`：工作线程创建 Tk 文件对话框，官方不支持非主线程。
- `widgets.py:460-497`：`snack` 顶掉前一条；旧版 dialog fallback 从不移除 overlay 控件。
- `theme.py:188-213`：全局 monkey-patch `ft.Text.__init__`，升级 Flet 是隐性雷区（当前实现谨慎，可保留 + 回归测试）。
- `check_sync_sequence.py:44,47`：表名拼 SQL 未转义引号（运维脚本纵深）。
- `sync.py:125-126`：游标类型标注与实际不符；每页保存游标全量重写 settings。
- AI 草稿：JSON 解析失败静默丢弃（`drafts.py:81-101`）；异常原文进 UI/日志可能带 URL query 泄漏（`ai_assistant.py:206-208`）；每次调用新建 httpx.Client + 未设 max_tokens（`openai_compat.py:44`）；撤销只活在 SnackBar 存续期（`ai_tasks_ui.py:233-249`）；busy 复位线程路径不一致（`ai_assistant.py:226-228`）；任务卡弹窗未复用 `strip_json_blocks`（`ai_tasks_ui.py:126-127`）；「消费分析」发最近 30 条而非本月数据（`tasks.py:74-80`）。
- `entertainment.py:31-39`：每次操作整页重建 + 封面缓存 O(glob)。
- `sync.py` LWW/序列等正面项之外：`_same_version` 幂等、`mark_clean` 精确确认等设计已确认正确（见下节）。

## 七、已确认的关键防线（含边界）

- 游标重置的**正常路径**已具备：恢复备份调用 `reset_sync_cursors`，并有手动全量重拉入口；但保存异常目前被吞掉，调用方仍可能提示成功并恢复自动同步，失败处理尚未闭环。应显式传播失败、保持自动同步暂停，必要时在换库前持久化 `needs_full_sync`。
- 序列号原子发号（UPDATE...RETURNING）+ 启动锚定存量 MAX(sync_seq) + 自检脚本。
- SQL 注入面：服务端与搜索全程 ORM/参数绑定，`contains(autoescape=True)`。
- 上传安全：magic-bytes 校验、双重大小限制、摘要文件名免疫路径穿越。
- 凭据落盘：DPAPI 优先、拒绝静默弱加密、解密失败保护原密文、`b64:` 兼容升级；整份 settings 读取失败的覆写风险另见第五节。
- 金额架构：整数分入库 + Decimal 累加 + `PRAGMA user_version` 防迁移重复执行；现有 ORM 聚合测试正确，P1-19 仅是注释维护问题。
- 周期记账幂等：UUID5 确定性 occurrence_id 跨设备防重、补 12 期上限、超限快进。
- 循环边界：规则搜索有 MAX_STEPS，周期补账有 MAX_CATCH_UP 和日期单调推进保证；旧配置 normalize 平滑迁移。不能笼统表述为所有推进循环都使用 MAX_STEPS。
- AI 草稿写库边界：草稿模型输出 JSON、必经预览确认才 `repo.create`、`saved` 防重、软删可撤销；普通聊天和报表叙述不受「仅产 JSON」约束。
- 图片防线：解压炸弹先查像素数再 `load()`；封面失败冷却 LRU、脱敏日志。现有 SSRF 校验的 DNS fail-open/TOCTOU 缺口另见第五节。

## 八、修复清单（复核后执行顺序）

**第一批：数据安全与同步可用性（P0 及关联 P1）**
1. P0-8：恢复流程去 copyfile 退化 + prune 保护，先补故障注入测试。
2. P0-3：客户端 pull 逐条 savepoint + flush、dead-letter 与安全游标推进。
3. settings 读取失败禁止覆写（第五节升为 P1）+ 游标重置失败显式传播 + 恢复时同步未空闲不得继续替换。

**第二批：数据一致性与编辑可靠性（P1）**
4. P0-2：Diary 重建旧表 + 活跃行部分唯一索引 + 墓碑复用；P0-1 虽为 P2，但在同一迁移批清理索引，避免重复施工。
5. P0-5/P1-13：日记/笔记共用版本化快照与串行保存，处理竞态、blur 和失败重排队。
6. P0-7：保存骨架区分新增/编辑，目标失效时让调用方保留弹窗并提示。
7. P0-4 + P0-10 + pull 覆盖 pending 留痕 + 墓碑保留策略：统一设计多端冲突、回执、导入时钟和离线设备水位，避免分散补丁造成协议再次分歧。

**第三批：用户可见问题、输入与网络安全**
8. P0-6 表格行列与首渲染兜底、P0-9 公历/农历锚点、P0-11 CSV 安全导出。
9. P1-5/P1-6 回调兜底，P1-7 命令面板，P1-8 番茄钟代次，P1-9/P1-12 数值校验，P1-14 空日记解耦。
10. P1-15/P1-16/P1-17 AI 草稿校验和统一安全网络客户端；同时修复 covers 的 DNS fail-open/TOCTOU。
11. P1-18 日期歧义、P1-20 通知状态与退避，P1-1/P1-2/P1-3/P1-4 服务端与同步协议加固。

**第四批：P2/P3 打磨**：按第五、六节处理；P1-10 与 P1-19 按 P3 施工，P1-11 已撤回，不安排修复。

**必须补充的回归测试**
- 「老库升级后：本地 5 表无目标复合索引、16 张同步表索引齐全」。
- 「旧 Diary 全表约束已移除；活跃行仍唯一、多墓碑可共存；软删除 → 同日重建 → 成功且重新 pending」。
- 「pull 载荷混入坏记录：每条错误均在 savepoint 内隔离，其余条目提交、游标安全推进、坏条目可查询并重拉」。
- 「同 id、同 updated_at、不同内容在所有端收敛；落败推送同时 accepted + superseded，精确出队且不误清同步期间的新编辑」。
- 「自动保存交错不丢最新快照；写库失败重排队不覆盖更新版本；blur 与 Timer 不互相回写旧值」。
- 「os.replace 失败/复制中断不改主库；`backup_keep=1` 恢复旧备份时 source 仍存在」。
- 「内置月/年及农历连续推进保持原始锚点」。
- 「未来 JSON 导入 → 上传 → 本地编辑 → 再同步，编辑可传播且不被旧未来版本覆盖」。
- 「CSV 危险文本及控制字符变体安全导出，真正负数仍保持数值往返」。
- 「settings 损坏时后台保存不覆写原文件；游标重置落盘失败不显示成功且不恢复自动同步」。
- 「停止番茄钟 1 秒内重启：只有一个有效 run，done 只执行一次」。
- 「体重数据超出 56–66kg 时纵轴自动扩展」，固化 P1-11 的误报纠正。
