# Paper Research — 论文自动调研工具

定时抓取 **Arxiv**（预留多数据源）预印本，调用 **LLM**（Deepseek / ollama）自动评分初筛，邮件只通知今日抓取结果，提供 **Web 交互审阅**（默认总体，可按日期范围/评级筛选），并可将选中的论文批量导入 **Zotero**（指定收藏夹 + 短标题）。

---

## 界面预览
<div align="center">
<img src="asset/webshot.png" alt="webshot" width="90%" height="auto">
<p> Web 审阅主界面：论文列表按 LLM 评分排序，支持评级/日期范围筛选、搜索、批量导入 Zotero </p>
</div>

---

## 核心流程

```
调度器每天定时抓取（错过补抓一次）
  → Arxiv API（仅元数据，不下载 PDF）
  → LLM 评分（important / useful / browse / skip + 0-1 分）
  → SQLite 数据库存储
  → 邮件通知今日抓取结果（只通知今日，不通知历史累积）
  → Web 审阅（默认总体，可按 今日/近7天/近30天/全部 筛选）
  → 审阅决策：
    ├── 忽略（不入 Zotero）
    ├── 延后（放入 Lurk 区）
    └── 批量导入 Zotero（指定收藏夹 + 短标题）
```


## Claude Code Skill

项目自带一个 Claude Code Skill（`skills/paper-research/SKILL.md`），
在 **Claude Code** 里可以直接用中文自然语言下达指令，无需记忆 CLI 命令：

| 你说 | 自动映射为 |
|------|-----------|
| 「启动 / 打开审阅页面」 | `uv run paper-research serve` |
| 「抓取 / 更新论文」 | `uv run paper-research fetch` |
| 「只抓某关键词」 | `uv run paper-research fetch -k <关键词>` |
| 「全量回溯抓取」 | `uv run paper-research fetch -m historical` |
| 「预览抓取（不写库）」 | `uv run paper-research fetch --dry-run` |
| 「发送今日结果邮件」 | `uv run paper-research notify` |
| 「查看状态 / 统计」 | `uv run paper-research status` |
| 「初始化 / 重置 / 清除」 | 直接操作文件（删库 / 日志 / 示例，保留 `config`） |

Skill 还内置**交互式配置流程**（一步步问答生成 / 更新 `config.yaml`）与
常见问题排查指引。在 Claude Code 中直接说明需求即可，其余交给 Skill 处理。

---

## 快速开始

### 1. 安装

```bash
git clone https://github.com/He-JiYe/paper-research.git
cd paper-research
uv venv .venv
uv sync
```

### 2. 配置

复制示例配置并填写：

```bash
cp config/config.example.yaml config/config.yaml
```

**安全建议**：所有密钥支持 `${ENV_VAR}` 占位符，优先用环境变量，避免明文落盘：

```powershell
setx DEEPSEEK_API_KEY "sk-xxxx"          # 或 ollama 本地模型则无需
setx ZOTERO_API_KEY "xxxx"
setx ZOTERO_LIBRARY_ID "12345678"
setx EMAIL_PASSWORD "smtp-auth-code"     # QQ 邮箱 SMTP 授权码（非 QQ 密码）
```

> ⚠️ `config/config.yaml` 已加入 `.gitignore`，不会被提交。请勿将其移出忽略列表。

主要配置项（`config/config.yaml`）：

| 字段 | 说明 |
|------|------|
| `llm.provider` | `deepseek`（OpenAI SDK）或 `ollama`（本地模型，原生 `/api/chat`，无需 api_key） |
| `llm.model / api_base / api_key` | LLM 模型与端点；ollama 时 api_base 默认 `http://localhost:11434` |
| `fetch.sources[]` | **每数据源独立参数**：`source` / `max_results` / `lookback_days` / `sort_by` |
| `keywords` | 搜索关键词列表（全源共享）：`keyword` / `categories` / `active` |
| `scheduler.fetch_time` | 每日定时抓取时间（内置调度器，如 `"08:30"`） |
| `scheduler.catch_up_on_start` | serve 启动时若今日错过抓取则自动补抓（补抓=正式今日抓取，同样发邮件） |
| `notification.email` | 邮件通知（只发今日抓取结果） |
| `zotero` | Zotero API 配置（api_key / library_id / library_type） |

### 3. 首次抓取

```bash
uv run paper-research fetch               # 增量抓取 + LLM 评分 + 写入 DB
uv run paper-research fetch -m historical # 历史抓取（不限时间，按相关性排序）
uv run paper-research fetch --dry-run     # 预览模式，不写入数据库
```

### 4. 打开 Web 审阅

```bash
uv run paper-research serve
```

交互式终端下浏览器自动打开 `http://127.0.0.1:8899`（后台/计划任务启动无 TTY，不自动打开，需手动访问）。serve 启动后：

- 内置调度器每天 `scheduler.fetch_time` 自动增量抓取
- 若启动时今天已过抓取时间但尚未抓取，自动补抓一次
- 有新论文时自动发送**今日结果**邮件
- 前端开着页面时，调度器抓取完成后自动刷新一次（SSE 订阅 `/api/fetch/events` 即时感知，60s 轮询兜底）

### 5. 审阅与导入 Zotero

| 操作 | 效果 |
|------|------|
| 评级筛选（全部/重要/值得关注/可浏览） | 按 LLM 评级过滤论文 |
| 日期范围筛选（全部/今日/近7天/近30天） | 按抓取日期过滤，默认全部 |
| 🗑️ 忽略 | 仅更新 DB 状态，不入 Zotero |
| ⏳ 延后 | 放入 Lurk 区 |
| ⏳ 待审核 | 取消标记，放回待审阅 |
| 📚 批量导入 | 勾选多篇 → 「导入 Zotero」→ 逐条改短标题/分类，一次导入 |

---

## LLM 评分后端（OpenAI 兼容 / Ollama）

评分器通过统一的 `ChatProvider` 抽象调用 LLM，支持两类后端：

### 1. OpenAI 兼容接口（OpenAI SDK）

`provider` 填 `deepseek`（默认示例）或任意名称——只要 `api_base` 指向任意
**OpenAI 兼容端点**即可，OpenAI 官方、Deepseek、Moonshot、通义千问、Kimi 等均可：

```yaml
llm:
  provider: deepseek                 # 非 ollama 即走 OpenAI SDK
  model: deepseek-v4-flash
  api_key: ${DEEPSEEK_API_KEY}       # 必填，环境变量注入
  api_base: https://api.deepseek.com # 可换成任意 OpenAI 兼容端点
```

- 需要 `api_key`（`${ENV_VAR}` 占位符，或环境变量 `DEEPSEEK_API_KEY`）
- `api_base` 默认 `https://api.deepseek.com`；用 OpenAI 官方填 `https://api.openai.com/v1`

### 2. Ollama 本地模型

```yaml
llm:
  provider: ollama
  model: qwen3:8b                    # 本地已拉取的模型名
  api_key: ""                        # 无需 key
  api_base: http://localhost:11434
```

- 原生 `/api/chat` 接口，**无需 API Key**，数据不出本地
- `api_base` 默认 `http://localhost:11434`

> 未配置或 LLM 不可用（无 key / 连接失败）时，评分自动降级为关键词相关性规则
> （`score_source` 标记 `no_api_key` / 连接失败），流程不中断。

---

## 命令参考

```bash
uv run paper-research fetch               # 抓取 + LLM 评分 + 写入 DB
uv run paper-research fetch -k <keyword> # 仅抓取指定关键词
uv run paper-research fetch -n 30        # 覆盖每关键词最大结果数
uv run paper-research fetch --dry-run    # 预览模式
uv run paper-research serve              # 启动 Web 审阅服务（含内置调度器）
uv run paper-research status             # 统计仪表盘（DB 统计 + 抓取日志）
uv run paper-research notify             # 手动发送今日邮件通知
```

Web API（serve 运行时，前后端分离）：完整接口文档见 **[docs/api.md](docs/api.md)**（9 条端口）。

---

## 持续化部署（本地）

项目自带 `scripts/register_task.ps1`（Windows 任务计划注册脚本，用 base `pythonw.exe` 跑
`serve_headless.py`，无控制台弹窗）：

```powershell
# 开机登录后自动后台启动 serve（内置调度器每天定时抓取）——推荐
.\scripts\register_task.ps1 -Mode startup

# 或：每天 08:30 定时执行一次 fetch（不常驻 serve）
.\scripts\register_task.ps1 -Mode daily -Time "08:30"

# 查看 / 立即运行 / 卸载
.\scripts\register_task.ps1 -Action status
.\scripts\register_task.ps1 -Action run-now
.\scripts\register_task.ps1 -Action unregister
```

> 日志统一写入 `log/`（项目根）：每日 `daily/YYYY-MM-DD.log`（INFO+）+
> 单一 `errors.log`（WARNING+ 关键信息）。console handler 仅在真实交互终端（isatty）
> 挂载，结构化日志不重复；无控制台启动（pythonw/计划任务）时另有
> `serve-stdout.log` 承接重定向后的裸 stdio（print / traceback），见 `src/logging_setup.py`。

---

## 项目结构

```
paper-research/
├── app/                        # 前端 SPA + 邮件模板 + 静态元数据（git 版本化，前后端分离）
│   ├── index.html              # 前端 SPA 入口（筛选/标记/批量导入）
│   ├── app.js / style.css      # 前端脚本与样式（评级颜色走 var(--remark-*)）
│   ├── app-meta.json           # 前端+邮件共享静态元数据（评级颜色单一来源）
│   └── email/                  # 邮件模板
├── config/
│   ├── config.yaml             # 实际配置（含密钥，已 gitignore）
│   └── config.example.yaml     # 配置模板（可提交）
├── examples/                   # few-shot 示例（每个关键词 {keyword}-few-shot.txt）
├── data/
│   └── papers.db               # SQLite 运行时数据库（动态数据，gitignore）
├── docs/api.md                 # Web API 接口文档（9 条端口）
├── scripts/                    # 部署脚本（register_task.ps1）
├── src/
│   ├── main.py                 # CLI 入口（argparse）
│   ├── commands.py             # CLI 命令实现
│   ├── paths.py                # 路径常量
│   ├── logging_setup.py        # 统一日志
│   ├── static_meta.py          # app-meta 静态元数据读写（单一来源）
│   ├── core/                   # 纯模型层（Record/KeywordItem/Pydantic 配置/FetchOptions/文本原语）
│   ├── config/                 # 配置加载（loader + settings）
│   ├── db/                     # SQLite 层（schema/enums/papers/fetch_logs）
│   ├── network/                # 数据源抽象 + REGISTRY
│   ├── scorer/                 # 评分层（provider/llm/fallback/parse/prompt）
│   ├── pipeline/               # 抓取编排（fetch/score）
│   ├── notify/                 # 邮件通知（renderer/sender/report）
│   ├── serve/                  # FastAPI（runtime/payloads/scheduler/routes）
│   └── zotero/                 # Zotero 集成（client/convert/manager）
└── serve_headless.py           # Windows 无窗口启动入口
```

---

## 开发

```bash
uv sync --dev                  # 同步开发依赖
uv run ruff check              # 代码检查
uv run ruff format             # 格式化
uv run pytest                  # 全部测试
uv run pytest --cov=src        # 带覆盖率
```

## 技术栈

| 组件 | 技术 |
|------|------|
| 语言 | Python 3.11+（uv 管理） |
| 后端 | FastAPI（JSON API + 静态托管） |
| 前端 | 原生 JS SPA（无构建工具链，动态渲染） |
| 数据库 | SQLite（WAL 模式） |
| 静态数据 | JSON 文件（app/app-meta.json） |
| 数据源 | arxiv 官方库（预留多数据源，REGISTRY 注册） |
| LLM | OpenAI 兼容 API（OpenAI SDK）或 ollama 本地模型 |
| Zotero | pyzotero |
| 测试 | pytest + coverage |

---

## 安全说明

- **密钥管理**：`config/config.yaml` 已 gitignore；推荐 `${ENV_VAR}` 占位符从环境变量读取
- **XSS 防护**：前端 `app.js` 对标题/作者/摘要/LLM 输出等不可信内容统一 `escapeHtml()` 转义；邮件模板同样在 `notify/renderer.py` 统一转义后再渲染
- **绑定地址**：server 默认仅监听 `127.0.0.1`，不要改为 `0.0.0.0`
- **Arxiv 限流**：抓取速率由 `fetch.sources[]` 的 `delay_seconds` 控制（默认 3 秒/请求，符合官方建议 1 请求/3 秒）
- **无 LLM 降级**：LLM 不可用（无 key/连接失败）时自动使用关键词规则评分（精度较低）

## 未来开发计划

- 扩展数据源：从 Arxiv 扩展到多个免费数据库（PubMed / DBLP 等，REGISTRY 已预留）
- 笔记能力：PDF 解析生成结构化阅读笔记，接入 Obsidian
- 综述生成：review agent 按关键词聚合已有论文生成综述
