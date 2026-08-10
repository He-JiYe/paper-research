---
name: paper-research
description: |
  论文自动调研工具 Paper Research 的初始化配置与日常操作。
  当用户要求以下事情时使用本 skill：
  - 初始化 / 配置 / 重置 / 清除 论文调研工具、数据库、日志或 few-shot 示例
  - 配置 config.yaml（数据源 / 关键词 / LLM / 邮件通知 / 定时调度 / Zotero）
  - 启动 / 运行 / 打开 论文审阅 Web 服务或本地网页
  - 抓取 / 更新 / 同步 Arxiv 论文
  - 发送 / 查看 结果邮件 / 邮件通知
  - 查看 状态 / 统计
  把「启动」「抓取」「通知」「状态」等自然语言指令映射为
  `uv run paper-research fetch / serve / notify / status` 命令执行；
  「初始化 / 配置 / 重置 / 清除」不经过 CLI 子命令，而是按本文第 1/2 节
  直接操作文件（rm / 编辑 config.yaml / 调用 save_config_raw）。
---

# Paper Research Skill

论文自动调研工具：每天定时抓取 Arxiv → LLM 评分（deepseek/ollama）→ SQLite 存储 →
邮件通知 → Web 交互审阅 → 批量导入 Zotero。

> 命令在项目根目录（bash）下执行。优先用 `uv run paper-research ...`；
> 若 `uv run` 报 `trampoline failed to canonicalize script path`（Windows + 路径含空格/中文），
> 改用 venv 直调：`.venv/Scripts/python.exe -m src.main ...`（等价 CLI）。

> **⚠️ 需要管理员权限的命令（注册/覆盖计划任务、`setx` 持久化环境变量、系统级安装等）不要自己执行**，
> 也不要尝试 `RunAs`/UAC 提权；直接把要在管理员 PowerShell 里运行的确切命令告诉用户，由用户手动执行。
> 判断标准：报错含 `0x80070005`（拒绝访问）/ `PermissionDenied` / `Register-ScheduledTask` / `setx` / 写系统级注册表。
> 非提权可完成的（普通文件读写、`uv run` CLI、创建/删除 data/log 文件）正常直接执行。

## 0. 命令速查

| 命令 | 作用 |
| --- | --- |
| `uv run paper-research fetch` | 增量抓取全部活跃关键词 → LLM 评分 → 入库 |
| `uv run paper-research fetch -k <kw>` | 只抓指定关键词 |
| `uv run paper-research fetch --dry-run` | 预览模式，不写库 |
| `uv run paper-research fetch -m historical` | 全量回溯抓取 |
| `uv run paper-research serve` | 启动 Web 审阅服务（内置每日调度器） |
| `uv run paper-research notify` | 手动发送今日结果邮件 |
| `uv run paper-research status` | 查看统计仪表盘 |

## 1. 初始化与清理

用户要求「初始化 / 重置 / 清除」时按此流程执行，**每步删除前先向用户确认**：

1. **数据库** `data/papers.db`（含 `-wal` / `-shm`）
   - 删除即清除全部论文与抓取日志：`rm -f data/papers.db data/papers.db-wal data/papers.db-shm`
   - 重建无需手动：首次 `fetch` 或任何 `PaperDB()` 实例化自动幂等建表（`src/db/connection.py` `_init_db`）。
2. **日志** `log/`（`src/paths.py` 的 `LOG_DIR` = 项目根 `log/`）
   - 删除：`rm -rf log/`
   - 重建：任意命令运行时的 `setup_logging()` 自动生成 `log/daily/YYYY-MM-DD.log` 与 `log/errors.log`。
3. **few-shot 示例** `examples/{keyword}-few-shot.txt`
   - 删除：`rm -f examples/*-few-shot.txt`
   - 重建：复用 `src/scorer/prompt.py` 的 `load_examples(keyword)`（缺失时自动 touch 空文件并建目录，单一来源），对每个将配置的关键词执行：
     ```bash
     uv run python - <<'PY'
     from src.scorer.prompt import load_examples
     for k in ["关键词1", "关键词2"]:
         load_examples(k)   # 自动创建 examples/{k}-few-shot.txt
     PY
     ```
   - 空文件仅为占位，用户可手工补充评分样例；未补充时 prompt 不注入示例段。

## 2. 交互式配置 config.yaml

### 2.1 提问流程（一步步问，按回答组装；括号内为默认值与可选性）

1. **数据源（必填）**：目前仅注册 `arxiv`，默认 `arxiv`。可选自定义每源参数：
   `max_results`（默认 `20`）、`sort_by`（默认 `relevance`，可选
   `relevance | lastUpdatedDate | submittedDate`）、`lookback_days`（默认 `0` = 全量）。
   非法值在加载/构建时直接报错（fail-fast）。
   排序约束：`relevance` 强制 `lookback_days = 0`（相关性排序无法做时间窗口截断，即全量）；
   时间排序（`lastUpdatedDate` / `submittedDate`）必须 `lookback_days > 0`，否则加载即报错。
   **增量抓取**需显式配 `sort_by: lastUpdatedDate` + `lookback_days: N`（抓最近 N 天，
   批内再按相关性重排）；否则默认 relevance 全量（≈ historical）。
2. **关键词（必填，至少 1 个）**：问「要追踪哪些关键词（逗号分隔）」。对每个关键词问
   「限定 arXiv 分类？（逗号分隔，如 cs.CV, cs.LG）」，不填为空；`active` 一律 `true`。
3. **LLM 评分（可选，默认 deepseek）**：不配则自动走关键词相关性 fallback，流程不中断。
   - `deepseek`：model 默认 `deepseek-v4-flash`、api_base 默认 `https://api.deepseek.com`、
     api_key 写 `${DEEPSEEK_API_KEY}`。
   - `ollama`：api_key 留空、api_base 默认 `http://localhost:11434`、model 问本地已拉取模型名。
4. **邮件通知（可选，默认不启用）**：`smtp_host`（无默认，留空则不发送，常用 `smtp.qq.com`）、`smtp_port`（默认 `465`）、
   `username`（发信邮箱）、`password`（SMTP 授权码，写 `${EMAIL_PASSWORD}`）、`from_addr`（默认同 username）、
   `to_addr`（收件人）。字段名与 `EmailConfig` 一致，config.yaml 按规范书写。
5. **定时调度（可选，默认启用）**：`enabled`、`fetch_time`（HH:MM，默认 `"08:30"`，非法格式/越界值启动即报错）、
   `catch_up_on_start`（默认 `true`，开机错过定时自动补抓）。（「今日」口径用系统本地时间，无 timezone 配置项。）
6. **服务端口（可选）**：默认 `8899`；host 固定 `127.0.0.1`（不要改 0.0.0.0）。
7. **Zotero（可选，默认不配）**：`library_type`（`user | group`，默认 `user`）、
   `library_id` 写 `${ZOTERO_LIBRARY_ID}`、`api_key` 写 `${ZOTERO_API_KEY}`。

### 2.2 写文件方式

**全新创建**用 `save_config_raw()`（`src/config/loader.py`，config.yaml 的唯一官方写入 API）：

```bash
uv run python - <<'PY'
import json
from src.config.loader import save_config_raw
cfg = json.loads(r'''<在此放按答案组装的 JSON>''')
save_config_raw(cfg)
print("config.yaml written")
PY
```

- 七个顶层段对齐 `config/config.example.yaml`：`llm` / `fetch.sources[]` / `keywords[]` /
  `notification.email` / `scheduler` / `server` / `zotero`。
- 密钥一律写 `${ENV_VAR}` **字面量**（如 `"${DEEPSEEK_API_KEY}"`）而非明文；加载器读取时自动解析环境变量。
- 用带引号定界符的 heredoc 防止 shell 变量展开；中文/列表交给 `json` 转义。

**⚠️ 改已有配置时**：不要「`load_config_raw()` 读 → 改 → `save_config_raw()` 写」——
`load_config_raw()` 会把 `${ENV_VAR}` 占位符解析成明文密钥，回写会把明文落盘。
增量修改（加关键词、改抓取时间）直接用 Write 工具编辑 `config/config.yaml` 对应字段，保留现有 `${...}` 占位符。

**密钥安全**：config.yaml 已 gitignore，但密钥仍用 `${ENV_VAR}` 占位符。写盘后引导用户在 PowerShell 设置
（`setx` 持久化，需新开终端生效）：
```powershell
setx DEEPSEEK_API_KEY "sk-xxxx"
setx ZOTERO_API_KEY "xxxx"
setx ZOTERO_LIBRARY_ID "12345678"
setx EMAIL_PASSWORD "smtp-auth-code"
```

### 2.3 配置验证与首次建库

```bash
uv run python -c "from src.config.loader import load_settings; s=load_settings(); print('OK', len(s.keywords), 'keywords', len(s.fetch.sources), 'sources')"
uv run paper-research status
uv run paper-research fetch --dry-run   # 预览本次将抓到的论文，不写库
uv run paper-research fetch             # 正式增量抓取 → 评分 → 入库（首次自动建表）
```

## 3. 自然语言命令映射

| 用户意图 | 执行命令 | 说明 |
| --- | --- | --- |
| 启动服务 / 打开网页 / 开始审阅 / 打开审阅页面 | `uv run paper-research serve` | 交互式终端（TTY）下启动后自动打开浏览器 `http://127.0.0.1:8899`（端口取 config.yaml）。**后台/计划任务（无 TTY）不会自动开浏览器**，需手动访问打印的 URL。API 文档在同端口 `/docs` |
| 抓取数据 / 更新论文 / 抓最新论文 | `uv run paper-research fetch` | 增量抓取 → LLM 评分 → 入库 |
| 只抓某关键词 | `uv run paper-research fetch -k <keyword>` | |
| 预览抓取（不写库） | `uv run paper-research fetch --dry-run` | |
| 全量回溯抓取 | `uv run paper-research fetch -m historical` | |
| 通知结果 / 发邮件 / 发送通知 | `uv run paper-research notify` | 手动发送今日报告邮件；今日无新论文则跳过 |
| 查看状态 / 统计 | `uv run paper-research status` | |

**重要**：`serve` 内置调度器（`src/serve/scheduler.py`）按 `scheduler.fetch_time` 每日定时抓取、
错过后按 `catch_up_on_start` 补抓，且抓到新论文自动发邮件。所以「启动服务」已覆盖「定时抓取 + 通知」；
`notify` 主要用于手动补发。

## 4. 常见问题与注意

- **未配 LLM key**：评分自动走关键词相关性 fallback（`src/scorer/fallback.py`），流程不中断，`score_source` 标记 `no_api_key`。
- **未配邮件**：`notify` 返回 `sent=false`，调度器不打扰。
- **未配 Zotero**：`serve` 启动打印警告，审阅页面正常，仅「导入 Zotero」不可用。
- **unknown 数据源**：`fetch.sources[*].source` 填了未注册名会在 `load_settings` 抛 ValueError 启动即失败——只填 `arxiv`。
- **scheduler.fetch_time 格式**：必须 `HH:MM`（如 `"08:30"`），非法配置加载即报错。
- **examples 文件**：被 .gitignore 忽略，仅本地占位，不要提交。
- **批量导入移除**：前端批量导入面板每条选中 item 有 ✕ 按钮，可直接从选中列表移除（同步取消卡片勾选）。
