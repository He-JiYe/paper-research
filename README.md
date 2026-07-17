# Paper Research — 论文自动调研工具

定时增量抓取 **Arxiv** 预印本，调用 **LLM**（Deepseek）自动摘要评分，提供 **Web 交互审阅** 和 **Markdown 笔记生成**。

---

## 功能亮点

- 🔍 **异步并发抓取** — 多关键词同时查询 Arxiv API，自动去重和版本检测
- 🤖 **LLM 自动评分** — 对标题+摘要评分（important / useful / browse / skip）
- 🌐 **Web 交互审阅** — 浏览器中标记论文（精读/粗读/延后/忽略），管理分类
- 📝 **结构化笔记** — 自动解析 PDF 图表和文字，生成 Markdown 笔记
- 📊 **笔记画廊** — 列表/卡片双视图，按标记类型筛选，分类管理
- 🔔 **通知推送** — Windows Toast 通知 + 邮件通知（可选）
- 📅 **定时任务** — 支持 Windows 任务计划，每天自动增量抓取

---

## 快速开始

### 1. 安装

```bash
# 安装依赖
uv sync

# 激活环境（Windows）
.venv\Scripts\activate
```

### 2. 配置 API Key

```bash
# 推荐：写入环境变量（不会存储在磁盘上）
setx DEEPSEEK_API_KEY "sk-xxxxxxxxxxxxxxxx"
```

> 密钥获取: https://platform.deepseek.com/api_keys
>
> **安全建议**：优先使用环境变量而非 `config/settings.json`，防止 API Key 以明文存储。

### 3. 配置关键词

编辑 `config/keywords.json`：

```json
[
    {"keyword": "test-time adaptation",   "arxiv_cats": ["cs.CV", "cs.LG"], "active": true},
    {"keyword": "large language model",    "arxiv_cats": ["cs.CL"],          "active": true}
]
```

| 字段 | 说明 |
|------|------|
| `keyword` | 搜索关键词 |
| `arxiv_cats` | 限定 Arxiv 学科分类（字符串数组，如 `["cs.CV", "cs.LG"]`） |
| `active` | 启用/停用 |

### 4. 首次抓取

```bash
uv run paper-research fetch # 默认增量抓取（抓取 lookback_days 时间段的相关论文）

uv run paper-research fetch -m historical  # 历史抓取（不限时间抓取最相关的论文） 
```

数据抓取共有两种模式 `incremental` 和 `historical`：

  - 默认情况下会启动 `incremental` 模式，该模式下会抓取过去 `lookback_days` (建议设置为 3 天，过大会导致抓取时间过长) 天的 `max_results` (默认 10 篇) 论文，然后自动进行 LLM 评分和生成 HTML 摘要页。
  - 根据需要可以使用 `historical` 模式，该模式不会限制时间，自动抓取最相关的 `max_results` 篇论文（速度比 `incremental` 模式快得多）。


### 5. 打开 Web 审阅

Web 页面支持数据抓取、论文审阅、笔记浏览、笔记修改

```bash
uv run paper-research serve
```

浏览器自动打开 `http://localhost:8899`。

### 6. 标记论文

在 Web 页面中对论文进行标记：

| 标记 | 含义 | 后续操作 |
|------|------|---------|
| 🔬 精读 | 需要详细阅读 | 自动下载 PDF 并生成笔记 |
| 📖 粗读 | 大致浏览 | 自动生成简短笔记 |
| ⏳ 延后 | 以后再说 | 放入延后处理区 |
| 🗑️ 忽略 | 不相关 | 从摘要页隐藏 |
| ⏳ 待审核 | 恢复待审 | 放回待审核区重新评估 |

---

### 7. 参考样例注入（待开发）

再 `docs/examples` 中添加个人笔记样例，修改 agent 提示词，优化笔记质量

## 命令参考

### 核心工作流

```bash
uv run paper-research fetch              # 增量抓取 + LLM 摘要 + 生成 HTML
uv run paper-research fetch -k <keyword> # 仅抓取指定关键词
uv run paper-research fetch --dry-run    # 预览模式，不写入数据库
uv run paper-research fetch --serve      # 抓取后自动启动 Web
uv run paper-research serve              # 启动本地 Web 审阅服务
uv run paper-research review             # 查看待审核论文列表
```

### 论文标记

```bash
uv run paper-research mark <arxiv_id> -t skim       # 标记为粗读
uv run paper-research mark <arxiv_id> -t deep_read   # 标记为精读
uv run paper-research mark <arxiv_id> -t lurk        # 延后处理
uv run paper-research mark <arxiv_id> -t ignore      # 忽略
uv run paper-research note <arxiv_id>                # 生成/查看笔记
```

### 查询与通知

```bash
uv run paper-research list               # 列出论文
uv run paper-research list -k <keyword>  # 按关键词筛选
uv run paper-research list --all         # 显示全部
uv run paper-research status             # 统计仪表盘
uv run paper-research notify             # 手动触发通知
```

---

## 定时任务（可选）

以 **管理员身份** 运行 PowerShell：

```powershell
# 注册每天 08:30 自动抓取
.\setup_task.ps1 -Action register -Time "08:30"

# 立即测试运行
.\setup_task.ps1 -Action run-now

# 查看任务状态
.\setup_task.ps1 -Action status
```

---

## 邮件通知

编辑 `config/settings.json`：

```json
"notification": {
    "windows_toast": true,
    "email": {
        "enabled": true,
        "smtp_host": "smtp.qq.com",
        "smtp_port": 465,
        "username": "your-email@qq.com",
        "password": "your-smtp-password"
    }
}
```

> **安全建议**：密码也可通过环境变量 `EMAIL_PASSWORD` 设置，优先级高于 `settings.json`：
> ```bash
> setx EMAIL_PASSWORD "your-smtp-password"
> ```

---

## 项目结构

```
paper-research/
├── config/                  # 配置文件
│   ├── settings.json        #   - LLM/Arxiv/Server 配置
│   └── keywords.json        #   - 搜索关键词
├── data/                    # SQLite 运行时数据库
├── output/                  # 生成产物
│   ├── summaries/           #   - HTML 摘要页
│   └── notes/               #   - 阅读笔记 + PDF + 图表
├── static/                  # CSS 静态资源
├── templates/               # Jinja2 HTML 模板
│   ├── summary.html         #   - 摘要页
│   ├── notes_index.html     #   - 笔记画廊
│   ├── note_detail.html     #   - 笔记详情
│   └── _fetch_modal.html    #   - 抓取模态框（共享）
├── src/
│   ├── main.py              # CLI 入口
│   ├── commands.py          # CLI 命令实现
│   ├── config.py            # 配置加载（AppConfig dataclass）
│   ├── db.py                # SQLite 数据库层（CRUD / 统计 / 分类）
│   ├── notify.py            # Windows Toast + 邮件通知
│   ├── network/
│   │   └── arxiv.py         # Arxiv API 客户端（Atom XML 解析 / PDF 下载）
│   ├── serve/
│   │   ├── server.py        # FastAPI Web 服务（路由 + SSE + 后台任务）
│   │   └── renderer.py      # Jinja2 HTML 渲染器
│   └── agents/
│       ├── base.py          # LLM 调用基类
│       ├── paper_scorer.py  # 论文评分 Agent
│       ├── note_agent.py    # 阅读笔记生成（待完善）
│       ├── parser.py        # PDF 图表/文字提取（待完善）
│       └── short_info.py    # 短标题生成（待完善）
├── tests/                   # pytest 测试套件
│   ├── conftest.py          # 共享 fixtures
│   ├── test_db.py           # 数据库测试
│   ├── test_config.py       # 配置加载测试
│   ├── test_commands.py     # CLI 命令测试
│   ├── test_commands_coverage.py  # 覆盖率专项测试
│   ├── test_notify.py       # 通知测试
│   ├── test_main.py         # 入口测试
│   ├── network/
│   │   └── test_arxiv.py    # Arxiv API 测试
│   ├── serve/
│   │   ├── test_server.py   # FastAPI 路由测试
│   │   └── test_renderer.py # 渲染器测试
│   └── agents/
│       └── ...              # Agent 单元测试
├── .github/workflows/
│   └── ci.yml               # GitHub Actions CI
└── pyproject.toml            # 项目元数据 + 工具配置
```

---

## 开发

### 环境

```bash
# 同步开发依赖
uv sync --dev

# 安装 pre-commit 钩子
uv run pre-commit install
```

### 代码检查

```bash
uv run ruff check                    # 代码检查
uv run ruff check --fix              # 自动修复
uv run ruff format                   # 格式化
```

### 测试

```bash
uv run pytest                        # 全部测试
uv run pytest --cov=src              # 带覆盖率
uv run pytest tests/test_db.py -v    # 单个测试文件
```

### CI

每次推送到 `main` 或创建 PR 时自动运行：
- Ruff 代码检查 + 格式化检查
- pytest 全量测试 + 覆盖率报告

---

## 技术栈

| 组件 | 技术 |
|------|------|
| 语言 | Python 3.11+ |
| 包管理 | uv |
| Web 框架 | FastAPI + Jinja2 |
| 数据库 | SQLite (sqlite3) |
| 网络请求 | httpx (异步) |
| LLM | Deepseek API / OpenAI SDK |
| PDF 解析 | PyMuPDF |
| 测试 | pytest + coverage + mock |
| 代码检查 | Ruff + pre-commit |

---

## 数据流

```
Arxiv API ──→ network/arxiv.py ──→ db.py ──→ agents/paper_scorer.py ──→ serve/renderer.py
  (Atom XML)    (并发 fetch)      (入库)      (LLM 评分/评级)           (HTML summary)
                                              │
                                              └──→ serve/server.py ──→ agents/note_agent.py
                                                   (Web 交互)            (PDF → 笔记)
```

---

## 安全说明

- **API Key** 优先使用环境变量 `DEEPSEEK_API_KEY`，其次从 `config/settings.json` 读取
- **邮件密码** 优先使用环境变量 `EMAIL_PASSWORD`，其次从 `config/settings.json` 读取
- 所有文件路径已做**路径遍历防护**，恶意 `arxiv_id` 不会被解析到预期目录之外
- 笔记中的 Markdown 内容在执行 HTML 渲染时已做 **XSS 防护**（`html.escape`）
- Arxiv API **无需认证**，但有速率限制（建议 ≤ 1 请求/秒）
- Deepseek 按 token 计费，每篇论文约消耗 ~500 tokens
- 无 API Key 时自动使用关键词规则 fallback（精度较低）
- 首次运行建议只启用 1-2 个关键词，逐步扩展

## 未来开发计划

- 优化 Agent 系统：考虑使用其他多模态模型解析论文，提升 note agent 能力，增加 review agent 对关键词总结综述；
- 扩展数据源：考虑从 Arxiv 单一数据库扩展到多个免费数据库；
- 服务器部署：从本地部署改为服务器部署