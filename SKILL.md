---
name: finance-tracker
description: 个人金融资产管理 | Query and manage a local-first personal finance portfolio (holdings, allocation by asset class, NAV trends, screenshot import). Use whenever the user asks about their net worth, asset distribution, recent change, holding list, or wants to import an Alipay/broker/bank screenshot. Also apply the rules below for screenshot import. Trigger phrases: 我的钱/总资产/我的资产/资产多少/我现在多少钱/净值/持仓/刷净值/刷新净值/调仓/刷持仓/更新持仓/截图刷新/调仓刷新
metadata:
  openclaw:
    requires:
      env: [FINANCE_TRACKER_BASE_URL]
      bins: [uv]
    primaryEnv: FINANCE_TRACKER_BASE_URL
    envVars:
      - name: FINANCE_TRACKER_BASE_URL
        required: true
        description: HTTP base URL of the FastAPI backend (e.g. http://127.0.0.1:8000). The backend must be running as a sidecar.
      - name: FINANCE_TRACKER_TIMEOUT
        required: false
        description: Per-request timeout seconds, default 30. Bump for slow refresh calls.
    install:
      - kind: uv
    homepage: https://github.com/your-org/finance-tracker
    emoji: 💰
---

# Finance Tracker Skill

Talks to the user's FastAPI backend (default `http://127.0.0.1:8000`) over HTTP. The backend owns a SQLite database with the user's holdings, NAV history, and daily snapshots; this skill is a thin CLI on top.

**Render charts as PNG** — the host has no GPU and no browser. All plots use matplotlib's `Agg` backend and write a file path to stdout for the host to attach to the IM reply.

## Setup once per environment

```bash
cd <skill-dir>
uv sync          # creates .venv with httpx, matplotlib, pillow
```

If `FINANCE_TRACKER_BASE_URL` is unset the scripts default to `http://127.0.0.1:8000`. Override in shell or in the openclaw skill config.

## How to choose a script

Match the user's intent to **one** script. Pass `--help` to any script to see flags. All scripts print one of:
- a single PNG path on the last line (charts), or
- a JSON object on stdout (data queries), or
- `CONFIRM_REQUIRED:<token>` (write operations awaiting a second turn).

| User intent (examples) | Script |
|---|---|
| "我现在多少钱 / 总资产 / 资产分布 / 最近怎么样" | `scripts/summary.py` |
| "趋势 / 走势 / 近 N 天 / 历年" | `scripts/trend.py [--days 90] [--mode stacked\|line]` |
| "我有哪些基金 / 持仓列表 / 美股部分都有什么" | `scripts/holdings.py list [--category us_stock]` |
| "把这张支付宝截图导入" / 用户给了一个图片路径 | `scripts/ingest.py parse <path>` then on confirm `scripts/ingest.py save <preview-id>` |
| "帮我加一只 110011 / 改某只的分类 / 删除某条" | `scripts/holdings.py add\|patch\|delete ...` |
| "刷一下今天的净值" | `scripts/refresh.py` |
| "今天的资产多少/总资产/涨跌" | `scripts/daily_report.py` |
| "我的账户都有哪些 / 加一个招行" | `scripts/accounts.py list\|add` |

Always run with `uv run --project <skill-dir>` if the skill's venv is not the active one.

## Output contract

**Charts** print exactly one trailing line:

```
CHART:/abs/path/to/file.png
```

When you see this, attach the file as an image in your IM reply and write a short caption based on the JSON metadata printed before it.

**Data queries** return a single JSON object on stdout. Read it, then summarize in natural language for the user. Don't paste raw JSON.

**Write operations** require two turns:

1. First turn — call without `--confirm`. The script will print a preview block ending in:
   ```
   CONFIRM_REQUIRED:<token>
   ```
   Show the preview to the user and ask "确认执行吗?回复 yes / no"。
2. After user replies "yes" (or "确认" / "ok") — call again with `--confirm <token>`. The token is a one-shot hash of the operation; tampering will be rejected.

If the user replies anything other than yes, do NOT call with `--confirm`.

## Common patterns

### Asking for the dashboard
```bash
uv run scripts/summary.py
```
Prints JSON with `total_cny`, `change_30d`, `top_categories`, plus `CHART:<png>` of the allocation pie. Compose a one-paragraph reply: "你目前总资产 X 万,近 30 天 +Y%,主要由 ... 构成。" then attach the PNG.

### Trend chart
```bash
uv run scripts/trend.py --days 365 --mode stacked
```
Two modes: `stacked` (堆积区域,看类别演变) and `line` (总额折线,看大势)。Default stacked. If user says "总额走势" or "线",use `line`.

### Daily report (资产日报)

**报告由 `scripts/daily_report.py` 100% 代码生成。AI 禁止自行计算涨跌。**

调用链：
1. `/api/analytics/allocation` — 当前配置（从 holdings.market_value 汇总）
2. `/api/analytics/daily_change` — 较昨日涨跌（从 snapshots 表前后两行做差，同源对比）
3. `/api/analytics/trend` — 找到最早快照作为长周期基准

输出格式（写入 `finance_refresh_done.txt` 并 stdout）：
```
💰 资产日报 · 2026-05-23 · 较昨日 +5,356 (+0.18%)
总资产：299.94 万
  债券：133.21 万 (44.4%)
  现金/货币：74.30 万 (24.8%)
  ...
近9天涨跌（自2026-05-14）：-2.14 万 (-0.71%)
```

**日报由 cron 自动推送：** 工作日 23:00 触发，isolated agent 执行 `refresh.py` → `daily_report.py` → announce 到微信。

**AI 行为规范：**
- 用户问"总资产/涨跌" → 直接跑 `daily_report.py`，把 stdout 发给用户
- 绝对不要手动查 snapshots 做减法，涨跌数据必须来自脚本输出
- 如果脚本报错 → 报告错误，不要自行补充数字

### Importing a screenshot

**⚠️ CRITICAL: Always use MiniMax MCP for image understanding first.**

When the user sends a screenshot (any financial app screenshot: 支付宝/天天基金/招行/雪球/etc.):

1. Call MiniMax MCP to parse the screenshot:
   ```bash
   mcporter call minimax.understand_image prompt="请仔细识别图中所有文字，以JSON数组格式返回，每项为识别到的文字内容" image_source=<path>
   ```
   Do NOT use the backend's `/api/ingest/screenshot` endpoint — it uses a different MiniMax endpoint that does not support images.

2. For each detected fund/asset, **联网查询其真实类型** to determine the correct `category`:
   - Search the fund name to confirm whether it's 股票型/债券型/指数型/ETF/etc.
   - Map to category: a_share / us_stock / hk_stock / bond / gold / commodity / cash / other
   - Write down the confirmed category; do NOT rely on the OCR result alone.

3. Present parsed results to user with confirmed categories, and explicitly ask:
   - "想存到哪个账户？（逐条指定或统一说一个）"
   - Do NOT assume the account — always ask the user to specify.

4. Only after user confirms, run the save command.

**⚠️ CRITICAL: Every holding MUST have a 6-digit fund/ETF code before saving.**

For every fund/ETF the user imports, you MUST:
1. Search the fund name to find its 6-digit code (e.g. "东方添益 基金代码" → 400030)
2. Write the code into `symbol` field — never leave it null for fund/ETF holdings
3. Cash, deposits, foreign exchange products do NOT need codes
4. If the fund has both A and C shares, both entries get the same symbol (the underlying ETF/fund code)

**This is non-negotiable**: a holding without a symbol cannot be refreshed by `refresh.py`. Always verify the symbol before confirming the save.

### Initial state snapshot

Before any import, always save a CSV snapshot first:
```bash
python3 -c "
import sqlite3, csv
conn = sqlite3.connect('/root/.openclaw/workspace/personal-finance-tracker/backend/app/data/portfolio.db')
with open('/root/.openclaw/workspace/finance-tracker/snapshots/initial_state_$(date +%Y-%m-%d).csv', 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['id','account_id','name','symbol','asset_kind','category','currency','market_value','created_at'])
    cursor = conn.execute('SELECT id,account_id,name,symbol,asset_kind,category,currency,market_value,created_at FROM holdings ORDER BY account_id, id')
    for row in cursor: writer.writerow(row)
conn.close()
print('Snapshot saved')
"
```

Name the snapshot with today's date. This gives you a recovery point if something goes wrong during a subsequent import.

### Manual holdings sync after position adjustment (调仓刷新)

> **触发词：调仓/刷持仓/更新持仓/截图刷新/调仓刷新**

When the user manually adjusts positions and wants to refresh all holdings from screenshots, follow this protocol:

**Phase 0 — 账号确认**
1. User says "调仓" → AI asks "哪个账户？（支付宝/招行/中金财富）"
2. User specifies account → AI asks user to send all screenshots for that account
3. User can send multiple screenshots (flip pages) — AI tracks the batch
4. User says "发完了" → move to Phase 1

**Phase 1 — 去重 + MiniMax 提取**
1. For each screenshot, call MiniMax MCP:
   ```bash
   mcporter call minimax.understand_image prompt="请列出截图中所有基金持仓，包括名称和当前市值。不要遗漏任何一条。" image_source=<path>
   ```
2. Deduplicate across screenshots by (name, market_value) pair — if two entries have the same name and market_value within ±1 CNY, keep only one.
3. Build a JSON array: `[{"name": "...", "market_value": 12345.67}, ...]`

**Phase 2 — 预览**
1. Run preview:
   ```bash
   uv run scripts/sync_holdings.py preview <account_id> '<json_items>'
   ```
2. For any 🆕 **新增** items: **联网搜索基金代码** (6-digit symbol). This is non-negotiable — a new holding without a symbol cannot be refreshed.
   - Search: "<基金名> 基金代码" → confirm the 6-digit code
   - Example: "南方原油 基金代码" → 501018
3. Present three sections to user:
   - ✅ **已匹配** (matched): will auto-update — show old→new market_value diff
   - 🆕 **新增** (new): not in DB, need user to confirm category manually
   - ❓ **截图无但DB有** (orphaned): ask user whether these were sold or missed in screenshots
3. If user finds missing items, return to Phase 0 to add more screenshots. **Do NOT proceed to Phase 3 until user confirms.**

**Phase 3 — 应用**
1. For "new" items: user must specify category for each. Ask one by one or batch.
2. For "orphaned" items: user confirms whether to delete. If user says "漏截了", they send additional screenshot and we go back to Phase 0 for just the missing ones.
3. Run apply (with symbol and category overrides for new items):
   ```bash
   uv run scripts/sync_holdings.py apply <account_id> '<json_items>' \
     --override 0:category=bond --override 0:symbol=400030
   ```
4. Show before/after summary with total change.

**⚠️ Key rules:**
- Screenshots only contain market_value, NOT shares. The backend reverse-calculates shares = market_value / latest_nav from eastmoney.
- Name matching uses fuzzy logic (exact → substring → Levenshtein>0.85). A/C/E share class suffixes are treated as different holdings.
- Per-account isolation: sync on account 4 (Alipay) does NOT touch account 5 (CMB).
- MiniMax may truncate long fund names or misread amounts. Always cross-check the matched names and flag mismatches for user review.
- If user says "发完了" but later discovers a missing screenshot, support appending to the current batch without restarting.

### Write paths summary

| Operation | Confirm needed | Reversible |
|---|---|---|
| `holdings.py add` | yes | yes (delete) |
| `holdings.py patch` | yes | yes (re-patch) |
| `holdings.py delete` | yes | **no** — also drops nav_history references |
| `ingest.py save` | yes | yes (delete each row) |
| `accounts.py add` | yes | yes (only if no holdings reference it) |
| `refresh.py` | no | n/a (idempotent) |

Always show the *exact* SQL-ish summary in the preview ("将删除持仓 #5 易方达蓝筹 (份额 1234.5)") so the user knows what they're greenlighting.

## Failure modes

- `connection refused` — backend not running. First check `curl http://127.0.0.1:8000/api/health` (should return `{"ok":true}`). If unreachable: "后端服务 (FastAPI) 没在跑,你需要在服务器上 `cd backend && uv run uvicorn app.main:app --port 8000` 起一下。"
- `MINIMAX_API_KEY not set` (returned by ingest) — tell the user to put it in `backend/.env`.
- eastmoney API fetch failures during refresh (network timeout / rate-limit) — partial success is OK; the script lists which symbols failed and the next refresh retries them. QDII funds (纳斯达克/标普500 etc.) typically have NAV data delayed by 1 trading day — this is expected, not a bug.

## What this skill does NOT do

- Render the React frontend (no GPU, no browser).
- Mutate the database directly (always goes through HTTP).
- Send/post anything outside the user's own server (no telemetry, no cloud sync).

---

## 架构与排障

本节记录 system-level 的工程决策、数据流和已知问题，供后续维护者参考。

### 1. 系统架构（三层）

```
数据层：holdings / nav_history / fx_rates / snapshots / accounts
       ↑
刷新层：market.refresh_all()  → 写 nav_history + fx_rates
       market.update_holdings_market_value() → 同步 holdings.market_value
       snapshot.compute_today() → 写入 snapshots
       ↑
推送层：finance_refresh.sh（cron，每工作日 23:00）
       → curl /api/analytics/allocation → finance_refresh_done.txt
       → HEARTBEAT 检测文件 → 分拆推送用户
```

**每个环节的职责必须清晰，不能混淆。刷新层只负责生成数据，推送层只负责读文件下发。两者完全独立，互不调用。**

### 2. 各层职责与数据来源

#### 2.1 数据层（holdings 表）

| 字段 | 含义 | 更新频率 |
|------|------|---------|
| `shares` | 份额 | 仅用户手动修改时更新 |
| `market_value` | 持仓市值（CNY） | **每次 refresh 后由 update_holdings_market_value() 同步为 nav×shares×fx** |
| `nav_history` | 每只基金的每日净值 | 每次 refresh 由 eastmoney API 写入 |
| `fx_rates` | 美元/港币汇率 | 每次 refresh 由 BOC API 写入 |
| `snapshots` | 每日组合快照（total_cny + breakdown） | 每次 refresh 由 compute_today() 实时计算写入 |

**关键约束：holdings.market_value 的唯一合法来源是 update_holdings_market_value()。禁止手动修改或覆盖。**

#### 2.2 刷新层（refresh.py）

调用链：`/api/refresh/run` → `_do_refresh()` →
1. `market.refresh_all()` — 拉取 nav_history 和 fx_rates（耗时最长）
2. `market.update_holdings_market_value()` — **将 holdings.market_value 同步为 nav×shares×fx**（本次新增）
3. `snapshot.compute_today()` — 写入今日 snapshots

`update_holdings_market_value()` 的 SQL 逻辑（CTE 版本，不依赖应用层）：

```sql
WITH latest_nav AS (
    SELECT symbol, nav,
           ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) as rn
    FROM nav_history
),
latest_fx AS (
    SELECT currency, rate_to_cny,
           ROW_NUMBER() OVER (PARTITION BY currency ORDER BY date DESC) as rn
    FROM fx_rates
)
SELECT
    h.id, h.symbol, h.shares, h.currency,
    ln.nav, COALESCE(lf.rate_to_cny, 1.0) as fx_rate
FROM holdings h
LEFT JOIN latest_nav ln ON ln.symbol = h.symbol AND ln.rn = 1
LEFT JOIN latest_fx lf ON lf.currency = h.currency AND lf.rn = 1
WHERE h.symbol IS NOT NULL
  AND h.shares IS NOT NULL
  AND ln.nav IS NOT NULL;
-- 每个结果行：new_mv = nav × shares × fx_rate
```

#### 2.3 推送层（finance_refresh.sh）

```bash
# 刷新（由 cron 触发，工作日 23:00）
$SKILL_DIR/.venv/bin/python3 scripts/refresh.py  # 触发 /api/refresh/run，等待完成

# 生成推送内容（curl analytics/allocation，写入 finance_refresh_done.txt）
curl -s http://127.0.0.1:8000/api/analytics/allocation | python3 -c "..."

# 推送（由 HEARTBEAT 检测文件触发，推送完成后更新 pushed_finance.json）
# 推送条件：finance_refresh_done.txt 存在且 pushed_finance.json.last_pushed != 今天
```

**推送后不清空 finance_refresh_done.txt；下次刷新会直接覆盖内容。**

### 3. 已知行为特性

#### 3.1 QDII 基金净值晚 1 天

纳斯达克、标普 500 等 QDII 基金的净值由 eastmoney 返回的时间比 A 股晚一个交易日。例如：
- 周一到周四刷新：QDII 净值更新到前一天
- 周五刷新：QDII 净值更新到周四（周末休市）

这是 eastmoney API 的实际限制，**不是 bug**，无需修复。snapshots.compute_today() 在计算时对无 nav 的持仓会 fallback 到已知的最新净值。

#### 3.2 现金类持仓（category=cash）无实时机制

活期存款、朝朝宝、余额宝、外汇、可用资金等无 symbol 字段，**不通过 nav 计算**，其 market_value 即为用户录入的账户余额。

如果用户做了存取操作，需要手动 patch 该条 holdings.market_value，或者重新录入该账户的所有持仓。

#### 3.3 allocation 接口的数据来源

`/api/analytics/allocation` **读的是 holdings.market_value 加总**（修复前有 bug），不是 snapshots。修复后 holdings.market_value 已经和实时计算结果一致，所以 allocation 的结果就是正确的。

`snapshots` 表的 breakdown_json 主要用于 trend 趋势图和历史对比，不直接参与实时日报。

#### 3.4 currency 字段的含义

| currency 值 | 含义 |
|-------------|------|
| CNY | 人民币计价产品 |
| USD | 美元产品（如招银理财-美元天添金）|
| HKD | 港币产品 |

**注意**：招银理财美元天添金名称含"美元"，但其实际 currency 值已在录入时设为 CNY，说明它是**以人民币计价的美元理财产品**（购入时已换汇），而非 USD 资产，无需额外换算。外汇（¥3,559）同理，是人民币余额。

#### 3.5 没有 symbol 的持仓

现金类、存款类、可用资金类持仓 **没有 symbol 字段**（symbol = NULL）。这是正确的，它们不依赖 nav_history 刷新，数据来源是账户余额的录入值。

### 4. 常见故障排查

| 症状 | 排查路径 |
|------|---------|
| 日报总资产和实际不符 | 检查 holdings.market_value 是否已同步：`SELECT name, shares, market_value, (SELECT nav FROM nav_history WHERE symbol=holdings.symbol ORDER BY date DESC LIMIT 1) as nav FROM holdings WHERE symbol IS NOT NULL` 对比 nav×shares |
| QDII 基金净值停在上一天 | 正常行为，eastmoney QDII 公布时间晚 |
| refresh 耗时很长（>60s）| 37 只基金 × 0.4s 间隔 ≈ 15s，加上网络波动可能到 40s+，refresh.py 默认 max_wait=60s 足够 |
| analytics/allocation 返回旧数据 | holdings.market_value 未更新，执行一次 `POST /api/refresh/run` 即可 |
| snapshots 和 allocation 总和不一致 | 检查是否有持仓无 nav 但 market_value 仍是旧值；或 compute_today 和 update_holdings_market_value 执行顺序问题 |

**排查第一条**：直接跑一次 refresh，看 backend 日志或 `POST /api/refresh/run` 返回的 state 是否为 done、error 是否为 null。

### 5. 工程原则（来自 2026-05-19 故障复盘）

1. **数字必须从数据库拉取，不凭记忆补充** — 任何资产数字必须读取实时数据，不跳过计算步骤
2. **refresh 后必须验证 holdings.market_value 已更新** — 用 `nav×shares` 的计算结果和表内值对比，差值必须 < 1 元
3. **snapshots 是实时计算层，holdings.market_value 是缓存层** — 前者永远正确，后者靠 refresh 同步
4. **推送链路和刷新链路完全独立** — 刷新层不推送，推送层只读文件
5. **出现数据疑问时，先用 SQL 直接查原始数据** — 不经过任何应用层逻辑
