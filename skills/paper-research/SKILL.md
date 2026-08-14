---
name: paper-research
description: |
  论文自动调研工具 Paper Research 的克隆、环境配置与日常操作。
  当用户要求以下事情时使用本 skill：
  - 克隆项目 / 初始化环境 / 安装全局命令 paper / 安装本 skill / 配置 / 重置 / 清除 工具、数据库、日志或 few-shot 示例
  - 配置 config.yaml（数据源 / 关键词 / LLM / 邮件通知 / 定时调度 / Zotero）
  - 启动 / 运行 / 打开 论文审阅 Web 服务或本地网页（含后台无窗口启动）
  - 注册 / 停止 开机自启（paper autostart）
  - 抓取 / 更新 / 同步 Arxiv 论文
  - 发送 / 查看 结果邮件 / 邮件通知
  - 查看 状态 / 统计
  把「启动」「抓取」「通知」「状态」「自启」等自然语言指令映射为
  全局命令 `paper ...` 或 `uv run --project <项目根> paper-research ...`
  （fetch / serve / notify / status / autostart）执行；
  「克隆 / 初始化 / 配置 / 重置 / 清除」不经过 CLI 子命令，而是按本文第 1/2 节
  直接操作文件（git clone / uv sync / rm / 编辑 config.yaml / 调用 save_config_raw）。

  本 skill 可安装到用户级（~/.claude/skills，见 1.5）：任何目录下对话都能触发，
  无需 cd 进项目目录——项目根定位与任意目录执行方式见第 0 节。
---

# Paper Research Skill

论文自动调研工具：每天定时抓取 Arxiv → LLM 评分（deepseek/ollama）→ SQLite 存储 →
邮件通知 → Web 交互审阅 → 批量导入 Zotero。

> **⚠️ 需要管理员权限的命令（注册/覆盖计划任务、系统级环境变量、系统级安装等）不要自己执行**，
> 也不要尝试 `RunAs`/UAC 提权；直接把要在管理员 PowerShell 里运行的确切命令告诉用户，由用户手动执行。
> 判断标准：报错含 `0x80070005`（拒绝访问）/ `PermissionDenied` / `Register-ScheduledTask` / 写系统级注册表。
> 非提权可完成的（普通文件读写、`uv run` CLI、创建/删除 data/log 文件、用户级 setx）正常直接执行。

## 0. 任意目录调用：项目定位与执行方式

本 skill 有两个安装层级，**两种都能用自然语言驱动**：

- **项目级**（`<项目>/skills/paper-research/`，克隆自带）：在项目目录内对话时生效；
- **用户级**（`~/.claude/skills/paper-research/`，安装见 1.5）：**任何目录**下对话都能触发本 skill。

### 0.1 任意目录执行命令（无需 cd 进项目）

按优先级任选其一（已实测等价）：

| 方式 | 命令形态 | 前提 |
| --- | --- | --- |
| 1. 全局命令（首选） | `paper fetch` / `paper serve` / `paper status` / `paper notify` / `paper autostart ...` | 已安装全局命令（1.4），任何目录任何 shell 直接可用 |
| 2. uv --project | `uv run --project "<项目根>" paper-research fetch` | 已装 uv，`<项目根>` 已知（0.2） |
| 3. venv 直调 | `$env:PYTHONPATH="<项目根>"; & "<项目根>\.venv\Scripts\python.exe" -m src.main fetch` | Windows PowerShell；`src/paths.py` 的 ROOT_DIR 按 `__file__` 定位，无需 cd |

> `uv run` 报 `trampoline failed to canonicalize script path`（Windows + 路径含空格/中文）时，
> 用方式 3（等价 CLI）。方式 2/3 在 Git Bash 下把 Windows 路径写成 `/e/...` 或引号包裹即可。

### 0.2 项目根定位（用户级 skill 不知道项目在哪时，按序探测）

1. 用户在对话里刚提过的路径，或本会话已确认过的路径；
2. 环境变量 `PAPER_RESEARCH_ROOT`（可选：用户级 skill 安装时顺手 `setx PAPER_RESEARCH_ROOT "<项目根>"`，一次设置长期可用）：
   `$env:PAPER_RESEARCH_ROOT`（PowerShell）或 `echo $PAPER_RESEARCH_ROOT`（bash）；
3. 全局命令已安装 → 读 editable 安装的 .pth，第一行即项目根：
   ```powershell
   Get-Content (Get-ChildItem "$env:APPDATA\uv\tools\paper-research\Lib\site-packages\*.pth" | Select-Object -First 1) | Select-Object -First 1
   ```
4. 都不行 → 问用户一次「paper-research 项目根目录在哪」，拿到后按 0.1 执行。

找到项目根后：日常指令用方式 1（`paper ...`，无需项目根）；配置/清理/初始化等文件操作用 `<项目根>` 拼绝对路径。

## 1. 环境与安装

### 1.1 前置：安装 uv（缺失时）

```powershell
winget install --id=astral-sh.uv -e
# 或：powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
uv --version   # 验证；uv 会自动管理 Python（项目要求 >= 3.11）
```

### 1.2 克隆项目

```bash
git clone https://github.com/He-JiYe/paper-research.git <项目根>
```

### 1.3 项目环境（uv）

```bash
cd <项目根>
uv venv .venv                                     # 创建虚拟环境
uv sync                                           # 按 uv.lock 安装依赖
cp config/config.example.yaml config/config.yaml  # 生成实际配置（已 gitignore，不提交）
```

### 1.4 安装全局命令 paper（可选，推荐——任意目录免 cd 的基础）

```powershell
powershell -ExecutionPolicy Bypass -File "<项目根>\scripts\install_paper.ps1"
```

- 脚本做三件事：`uv tool install --editable . --force`（生成 `paper.exe` / `paper-research.exe`）、
  把 uv 工具目录 `%USERPROFILE%\.local\bin` 加入用户 PATH（.NET API，无需管理员）、自检 `paper --help`。
- **editable 安装不复制源码**，`src/paths.py` 的 `ROOT_DIR` 仍指向项目根，config/data/log 位置不变；
  之后改 `src/` 代码**即时生效**，无需重装（新增 entry point 除外）。
- ⚠️ editable 指向项目当前路径：项目移动/删除后需重跑安装脚本；PATH 变更需**新开终端**生效。
- 卸载：`powershell -ExecutionPolicy Bypass -File "<项目根>\scripts\install_paper.ps1" -Uninstall`

### 1.5 安装用户级 skill（可选——安装后任何目录都能自然语言调用）

把 skill 拷到 Claude Code 用户级 skills 目录（项目更新后重拷一次保持同步）：

```powershell
# Windows（Claude Code 用户级 skill 目录）
Copy-Item -Recurse -Force "<项目根>\skills\paper-research" "$env:USERPROFILE\.claude\skills\"
# 顺手记录项目根，方便任意目录定位（需新开终端生效；纯 HKCU 用户级，无需管理员）
setx PAPER_RESEARCH_ROOT "<项目根>"
```

```bash
# macOS / Linux
mkdir -p ~/.claude/skills
cp -r "<项目根>/skills/paper-research" ~/.claude/skills/
export PAPER_RESEARCH_ROOT="<项目根>"   # 建议写入 ~/.bashrc / ~/.zshrc 持久化
```

安装后：在**任何目录**的对话中直接说「抓取论文」「打开审阅页面」等（见第 3 节映射），
Claude Code 会触发本 skill，按第 0 节定位项目并执行，无需 cd。

### 1.6 密钥环境变量（写盘后引导用户设置）

密钥一律在 config.yaml 写 `${ENV_VAR}` **字面量**占位符，运行时解析环境变量。
引导用户在终端设置（用户级 `setx` 只写 HKCU、无需管理员；**新开终端生效**）：

```powershell
setx DEEPSEEK_API_KEY "sk-xxxx"
setx ZOTERO_API_KEY "xxxx"
setx ZOTERO_LIBRARY_ID "12345678"
setx EMAIL_PASSWORD "smtp-auth-code"
```

> 密钥涉及敏感信息，不要让用户把明文粘贴进对话；引导用户自己在终端执行上面命令。

## 2. 交互式配置 config.yaml

### 2.1 提问流程（一步步问，按回答组装；括号内为默认值与可选性）

1. **数据源（必填）**：目前仅注册 `arxiv`，默认 `arxiv`。每源参数：
   `max_results`（默认 `20`）、`sort_by`（默认 `relevance`，可选
   `relevance | lastUpdatedDate | submittedDate`）、`sort_order`（默认 `descending`）、
   `lookback_days`（默认 `0` = 全量）、`delay_seconds`（默认 `3`，arXiv 限流，别调太小）、
   `num_retries`（默认 `3`）、`page_size`（默认自动派生，一般不用写）。
   非法值在加载/构建时直接报错（fail-fast）。排序约束：
   - `relevance` 强制 `lookback_days = 0`（相关性排序无法做时间窗口截断，即全量）；
   - 时间排序（`lastUpdatedDate` / `submittedDate`）必须 `lookback_days > 0`，且**不支持
     `sort_order: ascending`**（升序首篇最旧必触发窗口截断、静默返回 0 篇），违者加载即报错。
   - **增量抓取**需显式配 `sort_by: lastUpdatedDate` + `lookback_days: N`（抓最近 N 天，
     批内再按相关性重排）；否则默认 relevance 全量（≈ historical）。
2. **关键词（必填，至少 1 个）**：问「要追踪哪些关键词（逗号分隔）」。对每个关键词问
   「限定 arXiv 分类？（逗号分隔，如 cs.CV, cs.LG）」，不填为空；`active` 一律 `true`。
3. **LLM 评分（可选，默认 deepseek）**：不配则自动走关键词相关性 fallback，流程不中断。
   - `deepseek`：model 默认 `deepseek-v4-flash`、api_base 默认 `https://api.deepseek.com`、
     api_key 写 `${DEEPSEEK_API_KEY}`；可选 `temperature`（默认 `0.3`）、`max_tokens`（默认 `2000`）、
     `max_concurrent`（批量评分并发数，默认 `1`，远程 API 可调大）。
   - `ollama`：api_key 留空、api_base 默认 `http://localhost:11434`、model 问本地已拉取模型名；
     推理模型（qwen3 等）可加 `think: false` 关思考。
4. **邮件通知（可选，默认不启用）**：`smtp_host`（无默认，留空则不发送，常用 `smtp.qq.com`）、
   `smtp_port`（默认 `465`）、`username`（发信邮箱）、`password`（SMTP 授权码，写 `${EMAIL_PASSWORD}`）、
   `from_addr`（发件人，默认同 username）、`to_addr`（收件人）。
   **YAML 键必须写 `from_addr` / `to_addr`**（加载器不做 `from`/`to` 键名映射，写错会被静默忽略）。
5. **定时调度（可选，默认启用）**：`enabled`、`fetch_time`（HH:MM，默认 `"08:30"`，非法格式/越界值启动即报错）、
   `catch_up_on_start`（默认 `true`，开机错过定时自动补抓）。（「今日」口径用系统本地时间，无 timezone 配置项。）
6. **服务端口（可选）**：默认 `8899`；host 固定 `127.0.0.1`（不要改 0.0.0.0）。
7. **Zotero（可选，默认不配）**：`library_type`（`user | group`，默认 `user`）、
   `library_id` 写 `${ZOTERO_LIBRARY_ID}`、`api_key` 写 `${ZOTERO_API_KEY}`。

### 2.2 写文件方式

**全新创建**用 `save_config_raw()`（`src/config/loader.py`，config.yaml 的唯一官方写入 API）：

```bash
cd <项目根>   # 或按 0.1 方式 2/3 等价执行
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
增量修改（加关键词、改抓取时间）直接编辑 `config/config.yaml` 对应字段，保留现有 `${...}` 占位符。

### 2.3 配置验证与首次建库

```bash
cd <项目根>
uv run python -c "from src.config.loader import load_settings; s=load_settings(); print('OK', len(s.keywords), 'keywords', len(s.fetch.sources), 'sources')"
uv run paper-research status
uv run paper-research fetch --dry-run   # 预览本次将抓到的论文，不写库
uv run paper-research fetch             # 正式增量抓取 → 评分 → 入库（首次自动建表）
```

## 3. 自然语言命令映射

| 用户意图 | 执行命令 | 说明 |
| --- | --- | --- |
| 启动服务 / 打开网页 / 开始审阅 / 打开审阅页面 | `paper serve`（或 `uv run --project <根> paper-research serve`） | 交互式终端（TTY）下启动后自动打开浏览器 `http://127.0.0.1:8899`（端口取 config.yaml）。**后台/计划任务（无 TTY）不会自动开浏览器**，需手动访问打印的 URL。API 文档在同端口 `/docs`。**在 agent 环境启动务必走后台方式（见下方「后台启动 serve」）**，否则命令阻塞 |
| 只打开浏览器（服务已后台/自启运行） | `paper serve --open-browser` | 不启动服务，仅打开浏览器访问服务地址 |
| 抓取数据 / 更新论文 / 抓最新论文 | `paper fetch` | 增量抓取 → LLM 评分 → 入库 |
| 只抓某关键词 | `paper fetch -k <keyword>` | |
| 每关键词结果数覆盖 | `paper fetch -n <N>` | 0/缺省 = 用配置默认值 |
| 预览抓取（不写库） | `paper fetch --dry-run` | |
| 全量回溯抓取 | `paper fetch -m historical` | |
| 通知结果 / 发邮件 / 发送通知 | `paper notify` | 手动发送今日报告邮件；今日无新论文则跳过（reason=no pending papers） |
| 查看状态 / 统计 | `paper status` | |
| 注册开机自启 | `paper autostart`（或 `paper autostart on`） | 等价 `.\scripts\register_task.ps1 -Mode startup` |
| 停止开机自启 / 卸载自启任务 | `paper autostart off` | 等价 `.\scripts\register_task.ps1 -Action unregister` |
| 查看自启状态 | `paper autostart status` | 普通用户可直接查询 |
| 立即运行自启任务 | `paper autostart run-now` | |

**后台启动 serve（agent 环境用，无窗口常驻，返回后服务继续运行）**：

```powershell
$root = "<项目根>"
$pyhome = (Select-String -Path "$root\.venv\pyvenv.cfg" -Pattern '^\s*home\s*=\s*(.+)$').Matches[0].Groups[1].Value.Trim()
Start-Process -FilePath (Join-Path $pyhome "pythonw.exe") -ArgumentList "`"$root\serve_headless.py`" serve" -WorkingDirectory $root
# 服务就绪后：paper serve --open-browser   # 或让用户手动访问 http://127.0.0.1:8899
```

（等价于 `scripts/register_task.ps1` 的任务动作，但不注册计划任务；日志统一在 `<项目根>\log\`。）

**重要**：`serve` 内置调度器（`src/serve/scheduler.py`）按 `scheduler.fetch_time` 每日定时抓取、
错过后按 `catch_up_on_start` 补抓，且抓到新论文自动发邮件。所以「启动服务」已覆盖「定时抓取 + 通知」；
`notify` 主要用于手动补发。

**autostart 权限**：注册/卸载计划任务（on/off/run-now）需管理员权限；非管理员下 `paper autostart`
不自动执行，而是打印需在【管理员 PowerShell】手动运行的确切命令（项目约定：不自提权，见文件头提示）。

## 4. 初始化与清理

用户要求「初始化 / 重置 / 清除」时按此流程执行，**每步删除前先向用户确认**：

1. **数据库** `data/papers.db`（含 `-wal` / `-shm`）
   - 删除即清除全部论文与抓取日志：`rm -f data/papers.db data/papers.db-wal data/papers.db-shm`
   - 重建无需手动：首次 `fetch` 或任何 `PaperDB()` 实例化自动幂等建表（`src/db/connection.py` `_init_db`）。
2. **日志** `log/`（`src/paths.py` 的 `LOG_DIR` = 项目根 `log/`）
   - 删除：`rm -rf log/`
   - 重建：任意命令运行时的 `setup_logging()` 自动生成 `log/daily/YYYY-MM-DD.log` 与 `log/errors.log`。
3. **few-shot 示例** `examples/{keyword}-few-shot.txt`
   - 删除：`rm -f examples/*-few-shot.txt`
   - 重建：复用 `src/scorer/prompt.py` 的 `load_examples(keyword)`（缺失时自动建空文件并建目录，单一来源），对每个将配置的关键词执行：
     ```bash
     cd <项目根>
     uv run python - <<'PY'
     from src.scorer.prompt import load_examples
     for k in ["关键词1", "关键词2"]:
         load_examples(k)   # 自动创建 examples/{k}-few-shot.txt（文件名经安全化）
     PY
     ```
   - 空文件仅为占位，用户可手工补充评分样例；未补充时 prompt 不注入示例段。

## 5. 常见问题与注意

- **任意目录执行**：全局命令 `paper` 直接可用；否则 `uv run --project "<项目根>" ...` 或
  `$env:PYTHONPATH="<项目根>"; & "<项目根>\.venv\Scripts\python.exe" -m src.main ...`（见 0.1）。
- **`uv run` 报 trampoline 错误**：Windows + 路径含空格/中文时用 venv 直调（0.1 方式 3，等价 CLI）。
- **`paper` 命令找不到**：PATH 未刷新（新开终端）或未安装（重跑 `scripts/install_paper.ps1`）；
  editable 指向项目当前路径，项目移动/删除后需重装。
- **`paper serve --open-browser` 打不开页面**：服务需已在运行（开机自启/后台任务），否则浏览器访问无响应。
- **`paper autostart` 提示需要管理员**：on/off/run-now 需管理员，按打印的命令在【管理员 PowerShell】执行；`status` 普通用户可查。
- **未配 LLM key**：评分自动走关键词相关性 fallback（`src/scorer/fallback.py`），流程不中断，`score_source` 标记 `no_api_key`。
- **未配邮件**：`notify` 返回 `sent=false`，调度器不打扰。
- **未配 Zotero**：`serve` 启动打印警告，审阅页面正常，仅「导入 Zotero」不可用。
- **unknown 数据源**：`fetch.sources[*].source` 填了未注册名会在 `load_settings` 抛 ValueError 启动即失败——只填 `arxiv`。
- **scheduler.fetch_time 格式**：必须 `HH:MM`（如 `"08:30"`），非法配置加载即报错。
- **时间排序 + `sort_order: ascending`**：加载即报错（会静默返回 0 篇），时间排序请用默认 `descending`。
- **邮件配置键名**：必须 `from_addr` / `to_addr`（不是 `from` / `to`），写错会被静默忽略导致邮件发不出去。
- **examples 文件**：被 .gitignore 忽略，仅本地占位，不要提交。
- **批量导入移除**：前端批量导入面板每条选中 item 有 ✕ 按钮，可直接从选中列表移除（同步取消卡片勾选）。
- **项目移动后**：重跑 `scripts/install_paper.ps1` + 更新 `PAPER_RESEARCH_ROOT` + 重拷用户级 skill（1.5）。
