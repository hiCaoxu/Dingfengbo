# 定风波 同步服务 API 文档

同步服务端（`server/main.py`）提供**数据同步**与**图片存储**两组 HTTP 接口，供桌面端
（`app/services/sync.py`、`app/services/images.py`）调用。

- 框架：FastAPI（Python 3.11），数据库 SQLite（`cloud.db`，WAL 模式）
- 默认监听：`0.0.0.0:8000`（`DINGFENGBO_HOST` / `DINGFENGBO_PORT` 可覆盖）
- 在线调试：服务启动后可直接访问 `/docs`（Swagger UI）、`/redoc`、`/openapi.json`
- 完整机读契约：[`openapi.json`](openapi.json)（由 `python scripts/export_openapi.py` 重新生成）

---

## 1. 认证

除 `GET /api/health` 外，所有接口都要求带同步令牌：

```http
Authorization: Bearer <token>
```

也接受不带 `Bearer ` 前缀的裸令牌（旧客户端兼容）。

- **令牌不是账号**：服务端用它推导「数据归属命名空间」——
  `owner_id = sha256("dingfengbo-v1\0" + token)`。同一令牌的多台设备共享一份数据；
  不同令牌互相不可见。
- 数据库里只存这个摘要，**不存令牌本身**，库文件泄露不等于凭证泄露。
- 缺失或空白令牌 → `401 {"detail": "缺少同步令牌"}` / `{"detail": "同步令牌无效"}`。

---

## 2. 通用约定

| 项 | 约定 |
|---|---|
| 请求体 | `application/json`（上传接口为 `multipart/form-data`） |
| 时间 | ISO 8601、**naive UTC**，如 `2026-11-22T07:58:49.123456`；库内 `created_at`/`updated_at` 同为 UTC |
| 金额 | 同步载荷里是**元**（浮点，如 `12.34`）；服务端库内存**整数分**（类型装饰器换算）。跨版本不会因为单位变化把 12.34 读成 0.12 |
| 分页 | 游标式：`cursors` + `limit`（1–500，默认 200），响应 `has_more` 表示还有下一页 |
| 限流 | 每个令牌 60 秒窗口内：`push` ≤ 60 次、`upload` ≤ 30 次；超限 `429 {"detail": "请求过于频繁，请稍后再试"}` |
| 错误体 | 业务错误 `{"detail": "<中文原因>"}`；参数校验失败为 FastAPI 标准 `422 HTTPValidationError` |
| 幂等 | `pull` 天然幂等；`push` 对同一版本重复推送返回 `accepted` + `changed=false`，不会重复落库 |

### 错误码

| 状态码 | 含义 | 典型 `detail` |
|---|---|---|
| 400 | 请求参数语义错误 | `since 格式应为 ISO 时间`、`文件为空`、`只支持 JPG/PNG/GIF/WEBP/BMP 图片` |
| 401 | 缺少/空白令牌 | `缺少同步令牌` |
| 413 | 上传超过 12MB | `图片过大（超过 12MB）` |
| 422 | 请求体结构不合法（含未知表名） | `未知数据表: ['foo']`、`记录缺少 id` |
| 429 | 触发限流 | `请求过于频繁，请稍后再试` |
| 500 | 服务端异常（日志见 `logs/dingfengbo.log`） | — |

---

## 3. 数据模型

### 3.1 记录通用字段

所有可同步记录都带这 7 个字段（`serialize()` 输出的 `data` 里一定有）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string(36) | UUID 主键。业务键合并（日记按日期、打卡按习惯+日期）时，服务端保留本地主键并在回执里用 `record_id` 指明 |
| `owner_id` | string(64) | 归属 = 令牌摘要；服务端写入时以令牌为准，忽略载荷里的值 |
| `created_at` | datetime | 创建时间（UTC） |
| `updated_at` | datetime | 修改时间（UTC），**LWW 的唯一依据** |
| `is_deleted` | bool | 软删除标记（墓碑）。删除 = `is_deleted=true` 的一次更新，照常参与同步 |
| `sync_status` | string(16) | `pending`（待上传）/ `clean`（已确认）/ `error`（被永久拒收） |
| `sync_seq` | int | 服务端分配的单调整体序列号，拉取游标就基于它 |

### 3.2 同步表（16 张）

`table` 字段只能是下列之一（写错 → 422）：

| 表 | 业务字段 |
|---|---|
| `todos` | title, note, due_date, due_time, tags, lunar_reminder, repeat_rule, repeat_config, priority, quadrant, remind_days, remind_days_before, remind_time, pomodoro_count, completed, completed_at, is_template |
| `diaries` | date, title, content |
| `notes` | title, content, tags |
| `transactions` | kind, amount, category, account_id, to_account_id, date, note |
| `accounts` | name, kind, initial_balance |
| `budgets` | month, category, amount, note |
| `recurring_rules` | name, kind, amount, category, account_id, period, day, next_date, last_generated, active, note |
| `habits` | name, target_per_week, target_minutes, note, archived |
| `habit_logs` | habit_id, date, done, note |
| `exercises` | date, kind, minutes, distance_km, calories, note |
| `diets` | date, meal, food, grams, calories, note |
| `sleeps` | date, bedtime, wake_time, hours, quality |
| `weights` | date, kg, body_fat |
| `entertainments` | title, kind, status, cover, rating, note |
| `life_events` | title, note, target_date, done, done_at, sort_order |
| `record_links` | from_table, from_id, to_table, to_id, label |

---

## 4. 接口

### 4.1 `GET /api/health`

健康检查，**不需要令牌**。用于部署脚本与探活。

**响应 200**

```json
{ "status": "ok", "time": "2026-11-22T07:58:49.610471" }
```

---

### 4.2 `POST /api/sync/pull`

按游标拉取本令牌下的变更。客户端反复调用直到 `has_more=false`。

**请求**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `cursors` | object | 否 | `{表名: {"sequence": int}}`。缺省该表表示从头发（配合 `since` 可限定时间窗） |
| `limit` | int | 否 | 1–500，默认 200（每张表每页最多这么多条） |
| `since` | string | 否 | ISO 时间。**仅在对应表没有游标时生效**：返回 `updated_at > since` 的记录（旧客户端兼容路径） |
| `tables` | string[] | 否 | 选择性同步。`null`/缺省 = 全部；**空数组 = 一个都不选**（返回空页）；最多 64 个，未知表名 → 422 |

```json
{
  "cursors": { "todos": { "sequence": 128 }, "notes": { "sequence": 77 } },
  "limit": 200,
  "tables": ["todos", "notes"]
}
```

**响应 200**

| 字段 | 类型 | 说明 |
|---|---|---|
| `changes` | array | `[{"table": "todos", "data": {...}}]`，按 `sync_seq` 升序 |
| `cursors` | object | 每张有数据的表返回**本页最后一条的 `sequence`**，客户端应整表覆盖保存 |
| `has_more` | bool | 任一张表还有下一页 |
| `server_time` | string | 服务端当前时间，客户端据此校准本地时钟偏移（`updated_at` 对齐到服务端时间轴） |

```json
{
  "changes": [
    { "table": "todos", "data": { "id": "6f1c…", "title": "写周报", "updated_at": "2026-11-22T06:20:00", "sync_seq": 129 } }
  ],
  "cursors": { "todos": { "sequence": 129 } },
  "has_more": false,
  "server_time": "2026-11-22T07:59:01.123456"
}
```

**注意**

- 服务端按「任意表还有下一页」置 `has_more`。若客户端只勾选了部分表，未勾选表的游标不会推进，
  客户端需要按「本页游标无进展」自行退出（客户端已实现，见 `SyncClient._pull`）。
- 单条记录损坏不会影响整页：服务端逐条 savepoint 应用，坏记录进同步问题表。

---

### 4.3 `POST /api/sync/push`

上传本地待同步变更（最后写入获胜）。

**请求**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `changes` | array | 是 | 最多 **500** 条，超出 → 422 |

每条 `change`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `table` | string | 必须是 16 张表之一 |
| `data` | object | 完整的记录载荷，必须含 `id` 与合法的 `updated_at`，否则该条被**拒收**（不影响同批其他条） |

```json
{
  "changes": [
    {
      "table": "todos",
      "data": {
        "id": "6f1c9b8e-…",
        "title": "写周报",
        "note": "",
        "is_deleted": false,
        "updated_at": "2026-11-22T06:20:00",
        "sync_seq": 0
      }
    }
  ]
}
```

**响应 200**

| 字段 | 类型 | 说明 |
|---|---|---|
| `accepted` | array | 被接受的条目；`data.sync_seq` 已回填服务端分配的序列号，`changed=false` 表示内容与云端一致（幂等重推） |
| `rejected` | array | `[{table, id, reason}]`：结构不合法、或「记录已过期清理」 |
| `superseded` | array | `[{table, id, reason, local_updated_at, remote_updated_at}]`：云端版本更新（或同时间戳按载荷摘要决胜），本次推送被丢弃 |
| `server_time` | string | 服务端时间 |

**冲突与胜负规则（重要）**

1. **LWW**：比较 `updated_at`，晚者胜；`updated_at` 相同则比较业务载荷的 sha256 摘要（去掉 id/归属/同步元数据），摘要大者胜——两端算出的胜者必然一致。
2. 云端胜出 → 返回 `superseded`，客户端必须把这条移出待同步队列，否则会永远重推。
3. `is_deleted=true` 是普通更新，同样参与 LWW；墓碑在被物理清理前一直可传播。
4. **墓碑高水位**：云端清理过期墓碑后会把「清理截止时间」记下来。若客户端推送的记录在云端**已不存在**、且 `updated_at` 早于该截止时间，服务端拒收并提示：

   ```
   该记录已在云端过期清理（墓碑保留窗口已过），本次推送被拒；请先在「同步明细」里点「重新拉取云端全量」再继续
   ```

   这是为了避免离线很久的设备把已删除的记录「复活」。

**响应示例**

```json
{
  "accepted": [
    { "table": "todos", "data": { "id": "6f1c…", "title": "写周报", "sync_seq": 130 }, "changed": true, "record_id": "6f1c…" }
  ],
  "rejected": [
    { "table": "todos", "id": "bad-id", "reason": "记录缺少 updated_at" }
  ],
  "superseded": [
    { "table": "notes", "id": "9a2b…", "reason": "云端版本胜出（更晚，或同时间戳按内容摘要决胜），本次推送已丢弃",
      "local_updated_at": "2026-11-22T06:00:00", "remote_updated_at": "2026-11-22T06:30:00" }
  ],
  "server_time": "2026-11-22T07:59:02.000000"
}
```

---

### 4.4 `GET /api/sync/check`

轻量查询「云端最新修改时间」，用于判断是否需要拉取。

**响应 200**

```json
{ "latest_updated_at": "2026-11-22T06:30:00", "server_time": "2026-11-22T07:59:03.000000" }
```

`latest_updated_at` 为该令牌下所有记录 `MAX(updated_at)`，无数据时为 `null`。

---

### 4.5 `POST /api/upload`

上传图片（客户端会先压缩），按内容摘要去重存储。

**请求**：`multipart/form-data`，字段名 `file`。

- 大小上限 **12MB**：先看 `Content-Length` 快速拒绝；chunked 请求边读边累计，超限立即中止。
- 只接受**真实图片**：按文件头魔数判断 JPG / PNG / GIF / WEBP / BMP（后缀可伪造，不作依据）。
- 文件名 = `sha256(内容)` 前 24 位 + 真实后缀，同一张图重复上传只占一份空间，URL 稳定。

**响应 200**

```json
{ "url": "https://your-host/files/abc123def456-9f8e7d6c5b4a3210.png", "name": "abc123def456-9f8e7d6c5b4a3210.png" }
```

`url` 的前缀由服务端 `DINGFENGBO_SERVER_PUBLIC_URL` 决定；未配置时只用本机地址，
不会回显请求里客户端可控的 `Host`。

---

### 4.6 `GET /files/{name}`

静态图片。响应头固定：

| 头 | 值 | 原因 |
|---|---|---|
| `Cache-Control` | `public, max-age=31536000, immutable` | 文件名是内容摘要，内容永不改变 |
| `X-Content-Type-Options` | `nosniff` | 防止把上传内容当脚本执行 |
| `Content-Security-Policy` | `default-src 'none'` | 同上 |

---

## 5. 一次完整同步的时序

```
客户端                                        服务端
  │  GET /api/sync/check（可选，快速判断）        │
  │ ─────────────────────────────────────────▶ │
  │  POST /api/sync/pull × N（直到 has_more=false）│
  │ ─────────────────────────────────────────▶ │ 按 owner + sync_seq 游标查
  │ ◀───────────────────────────────────────── │ changes / cursors / has_more
  │  POST /api/sync/push（≤500 条/次）            │
  │ ─────────────────────────────────────────▶ │ 逐条 savepoint + LWW + 序列号
  │ ◀───────────────────────────────────────── │ accepted / rejected / superseded
  │  本地清理已确认墓碑（30 天窗口）               │  服务端清理墓碑（90 天窗口）
```

- 一次同步的客户端日志都带 `[sync xxxxxxxx]` 前缀，可直接按 id 串起 pull/push/清理。
- 备份恢复期间客户端会自动暂停同步（库被整库替换），恢复完成后自动继续。

---

## 6. 兼容性

- 路径固定为 `/api/...`，破坏性变更会另开新路径，不原地改语义。
- 未知字段忽略；旧客户端不带 `tables` 时等价于「全部表」。
- `since` 仅在没有游标时生效，保证老版本（只带 `since`）仍可用。
- `amount` 等金额字段在协议层始终是「元」，服务端内部存分；跨版本混用不会读错。

---

## 附录 A：客户端调用示例

```python
import hashlib, httpx

BASE = "https://sync.example.com"
TOKEN = "<同步令牌>"
headers = {"Authorization": f"Bearer {TOKEN}"}

with httpx.Client(timeout=10.0) as client:
    cursors = {}
    while True:
        resp = client.post(f"{BASE}/api/sync/pull",
                           json={"cursors": cursors, "limit": 200,
                                 "tables": ["todos", "notes"]},
                           headers=headers)
        resp.raise_for_status()
        page = resp.json()
        for change in page["changes"]:
            apply_remote(change)          # 本地落库
        cursors.update(page["cursors"])   # 整表覆盖
        if not page["has_more"]:
            break

    pending = collect_pending_changes()   # 本地 sync_status == "pending"
    if pending:
        resp = client.post(f"{BASE}/api/sync/push",
                           json={"changes": pending[:500]},
                           headers=headers)
        resp.raise_for_status()
        receipt = resp.json()             # accepted / rejected / superseded 分别处理
```

## 附录 B：AI 出站接口（第三方，非本服务提供）

客户端不经过同步服务端访问大模型，而是直连各厂商的 **OpenAI 兼容**接口
（`app/services/ai/openai_compat.py`）：

| 项 | 约定 |
|---|---|
| 地址 | `<base_url>/chat/completions`（各厂商 base_url 见 `app/config.py` 的 `AI_PROVIDERS`） |
| 认证 | `Authorization: Bearer <api_key>` |
| 请求体 | `{"model": …, "messages": [{"role": "system|user|assistant", "content": "…"}], "temperature": 0.7, "max_tokens": 2048, "stream": false}` |
| 超时 | 120 秒，连接池复用（同一 `httpx.Client`） |
| 重试 | 429 / 5xx 最多 3 次，退避 0.6s、1.5s |
| 读回 | `choices[0].message.content` |
| 隐私 | 首次把本地数据发往第三方前会弹一次知情确认；日志不记录提示词与回复正文 |
