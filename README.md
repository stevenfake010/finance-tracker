# finance-tracker skill

把这个仓库的 finance-tracker 后端封装成一个 OpenClaw / Claude Code skill,
让你在手机 IM 端通过自然语言查询资产、导入截图、修改持仓。

## 架构

```
[ 你的手机 IM ]
       ↓ 自然语言
[ openclaw 服务器(无 GPU) ]
       ↓ 选择 skill,执行 uv run scripts/*.py
[ skill (本目录,~/Desktop/finance-tracker/skill) ]
       ↓ HTTP localhost
[ FastAPI 后端 (sidecar 进程) ]
       ↓ SQLite 文件
[ portfolio.db ]
```

skill 自己**不连数据库**,所有读写都走 FastAPI。这意味着:
- 部署时**后端必须先于 openclaw 起来并保持常驻**
- 后端崩了,skill 会立刻报"无法连接后端",不会静默失败
- Web 端 + IM 端看到的永远是同一份数据

## 一次性部署(服务器侧)

### 1. 把 finance-tracker 整个仓库 rsync 到服务器

```bash
rsync -avz --exclude=node_modules --exclude=.venv \
  ~/Desktop/finance-tracker/ user@server:/srv/finance-tracker/
```

### 2. 起后端(常驻)

```bash
ssh user@server
cd /srv/finance-tracker/backend
uv sync
cp .env.example .env  # 填 MINIMAX_API_KEY
```

挑一种保活方式:

**A. systemd(推荐)**
```ini
# /etc/systemd/system/finance-tracker.service
[Unit]
Description=Finance Tracker FastAPI
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/srv/finance-tracker/backend
ExecStart=/home/youruser/.local/bin/uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now finance-tracker
```

**B. tmux/screen + uv run**
```bash
tmux new -s ft 'cd /srv/finance-tracker/backend && uv run uvicorn app.main:app --port 8000'
# Ctrl-B D 离开
```

**C. docker-compose** —— 留作后续可选增强,当前不必要。

### 3. 准备字体(必要,服务器一般没装中文)

```bash
cd /srv/finance-tracker/skill/fonts
# 下载 Noto Sans CJK SC Regular ~17MB
curl -LO https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf
```

### 4. 把 skill 装进 openclaw

OpenClaw 默认从两个位置加载 skill:

- 全局:`~/.openclaw/skills/`
- 工作区:`<project>/.openclaw/skills/`

软链接进去即可(也可以 cp,但软链下次更新只 `git pull` skill 仓库一处):

```bash
mkdir -p ~/.openclaw/skills
ln -s /srv/finance-tracker/skill ~/.openclaw/skills/finance-tracker
```

然后在 openclaw 配置里(通常是 `~/.openclaw/config.yaml` 或 web 设置面板)
为这个 skill 注入两个环境变量:

```yaml
skills:
  finance-tracker:
    env:
      FINANCE_TRACKER_BASE_URL: "http://127.0.0.1:8000"
      FINANCE_TRACKER_TIMEOUT: "60"
```

### 5. 验证

在你的 IM 里发一条消息:

> 我现在总资产多少钱?

期望 openclaw:
1. 命中 finance-tracker skill
2. 跑 `uv run scripts/summary.py`
3. 解析 JSON 总额 + change
4. 把 `CHART:/tmp/...png` 当作图片附件回传给你
5. 文字回复一句话总结

## 调试

### skill 完全没被调起

OpenClaw 的 skill 触发由 frontmatter 的 `description` 决定 —— 把 description
写得越具体、越和用户的措辞对齐,命中率越高。如果用户问"我的钱"没命中,
在 SKILL.md 顶部 description 加一句"net worth / 我的钱 / 资产"。

### 中文显示成方框

`fonts/` 目录里没有合规字体。按 §3 装一个 Noto。也可以临时在 SKILL.md 里
跑 `uv run python scripts/_common.py` 看 stderr 报告字体加载情况。

### 后端连不上

```bash
curl http://127.0.0.1:8000/api/health
# 期望 {"ok":true,"minimax_key_set":true}
```

如果 `minimax_key_set: false`,截图导入会失败但其他功能正常 —— 改 `.env` 后
重启服务即可。

### 二次确认 token 不匹配

如果 agent 复述用户消息时改写了参数(比如把份额从 1234.5 写成 1234),token
就对不上,脚本会拒绝执行。这是设计的安全网,不是 bug —— 让 agent 重新跑
不带 `--confirm` 的预览,生成新 token。

## 文件清单

| 路径 | 作用 |
|---|---|
| `SKILL.md` | skill 入口,frontmatter + 自然语言指令 |
| `pyproject.toml` | uv 依赖(httpx + matplotlib + pillow) |
| `scripts/_common.py` | HTTP client + matplotlib 字体注册 + 二次确认 |
| `scripts/summary.py` | 总资产 + 分类饼图 |
| `scripts/trend.py` | 趋势图(堆积/折线双模式) |
| `scripts/holdings.py` | 持仓 list/add/patch/delete |
| `scripts/accounts.py` | 账户 list/add/delete |
| `scripts/refresh.py` | 触发刷新并轮询 |
| `scripts/ingest.py` | 截图解析 + 二阶段保存 |
| `fonts/` | 中文字体落盘位置(.gitignore) |

## 不在范围内

- 渲染 React 前端 —— 服务器无 GPU/无浏览器
- 直接写 SQLite —— 永远走 HTTP
- 把数据同步到第三方云 —— 本地优先
