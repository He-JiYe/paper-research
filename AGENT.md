# Paper Research

每天定时增量抓取 Arxiv 上指定关键词的预印本，使用 LLM（Deepseek）进行打分初选，支持 Web 交互式审阅、Markdown 笔记生成，后续可扩展为自动综述系统。

## 项目架构

```
.venv/                   # 虚拟环境 (uv 管理)
config/                  # 配置文件（settings.json + keywords.json）
data/                    # SQLite 运行时数据库（papers.db）
docs/                    # 文档和样本笔记
output/                  # 生成产物（HTML summary、notes、PDF）
static/                  # 前端静态资源
templates/               # Jinja2 HTML 模板
src/
├── __init__.py          # 包标识 + 版本号
├── main.py              # CLI 入口（argparse 解析 + 命令分发）
├── commands.py          # CLI 命令实现（fetch/serve/review/mark/note/list/status/notify）
├── config.py            # 配置加载（settings.json → AppConfig dataclass）
├── db.py                # SQLite 数据库层（CRUD、统计、分类）
├── notify.py            # Windows Toast + 邮件通知
├── network/
│   └── arxiv.py         # Arxiv API 客户端（Atom XML 解析、PDF 下载）
├── serve/
│   ├── server.py        # FastAPI Web 服务（路由 + 后台任务）
│   └── renderer.py      # Jinja2 HTML 渲染器
└── agents/
    ├── base.py          # BaseAgent（LLM 调用、JSON 提取、重试、fallback）
    ├── paper_scorer.py  # PaperScorer（标题+摘要 → 评分/评级）
    ├── note_agent.py    # NoteAgent（PDF 解析 → 生成结构化笔记）
    ├── parser.py        # ParserAgent（PyMuPDF 图表/文字提取）
    └── short_info.py    # ShortInfoAgent（笔记短标题 + 一句话描述）
test/
├── conftest.py          # 共享 fixtures（DB 连接、样本数据、Mock 配置）
├── test_main.py         # CLI 参数解析测试
├── test_commands.py     # 命令实现测试
├── test_config.py       # 配置加载测试
├── test_db.py           # 数据库 CRUD 测试
├── test_notify.py       # 通知推送测试
├── network/
│   └── test_arxiv.py    # Arxiv API 客户端测试
├── serve/
│   ├── test_server.py   # FastAPI 路由测试
│   └── test_renderer.py # HTML 渲染器测试
└── agents/
    ├── test_base.py         # BaseAgent 基类测试
    └── test_paper_scorer.py # 评分 Agent 测试
.gitignore
.pre-commit-config.yaml    # pre-commit 钩子（ruff + 通用检查 + uv-lock）
AGENT.md                   # 本文件
README.md
pyproject.toml             # 项目元数据、依赖、工具配置
```

## 核心功能

| 模块 | 职责 | 状态 |
|------|------|------|
| `network/arxiv.py` | Arxiv API 查询（多关键词并发、Atom XML 解析、版本检测、PDF 下载） | ✅ 稳定 |
| `agents/paper_scorer.py` | LLM 对标题+摘要评分（important/useful/browse/skip），含关键词 fallback | ✅ 稳定 |
| `agents/note_agent.py` | 解析 PDF 图表/文字 → LLM 生成结构化 Markdown 笔记 | ⚠️ 待完善 |
| `agents/parser.py` | PyMuPDF 提取图表、表格、段落文字，LLM 过滤分类 | ⚠️ 待完善 |
| `agents/short_info.py` | 生成笔记短标题和一句话描述 | ⚠️ 待完善 |
| `serve/server.py` | FastAPI Web 服务：论文浏览、标记、笔记编辑、分类管理、搜索导入 | ✅ 稳定 |
| `serve/renderer.py` | Jinja2 渲染 HTML summary/notes 页面 | ✅ 稳定 |
| `notify.py` | Windows Toast + SMTP 邮件通知 | ✅ 稳定 |
| `db.py` | SQLite 数据库：论文 CRUD、分类、统计、抓取日志 | ✅ 稳定 |
| `commands.py` | CLI 命令调度：fetch/serve/review/mark/note/list/status/notify | ✅ 稳定 |
| `main.py` | argparse 参数解析，命令分发 | ✅ 稳定 |

## CLI 命令

```bash
# 核心工作流
uv run paper-research fetch              # 增量抓取 Arxiv → LLM 摘要 → 生成 HTML
uv run paper-research fetch -k <keyword> # 仅抓取指定关键词
uv run paper-research fetch --dry-run    # 预览模式，不写入数据库
uv run paper-research serve              # 启动 Web 审阅服务
uv run paper-research review             # 查看待审核论文列表
uv run paper-research mark <arxiv_id> -t <type>  # 标记论文（ignore/skim/deep-read/lurk）
uv run paper-research note <arxiv_id>    # 生成并查看笔记

# 查询与管理
uv run paper-research list               # 列出论文
uv run paper-research list -k <keyword>  # 按关键词筛选
uv run paper-research list --all         # 显示所有论文
uv run paper-research status             # 数据库统计仪表盘
uv run paper-research notify             # 手动触发通知

# 开发工具
uv run ruff check                        # Ruff 代码检查
uv run ruff check --fix                  # 自动修复
uv run ruff format                       # 代码格式化
uv run pytest                            # 运行测试
uv run pytest --cov=src                  # 带覆盖率测试
uv run pytest test/test_db.py -v         # 单独运行某测试文件
uv run pre-commit run --all-files        # 手动运行所有 pre-commit 钩子
uv run pre-commit autoupdate             # 更新钩子版本
```

## 数据流

```
Arxiv API ──→ network/arxiv.py ──→ db.py ──→ agents/paper_scorer.py ──→ serve/renderer.py
  (Atom XML)    (并发 fetch)      (入库)      (LLM 评分/评级)           (HTML summary)
                                              │
                                              └──→ serve/server.py ──→ agents/note_agent.py
                                                   (Web 交互)            (PDF→笔记)
```

## 开发规范

### 环境与依赖
- 使用 **uv** 管理 Python 虚拟环境和依赖（`.venv/`）
- `uv sync` 同步依赖，`uv add <pkg>` 新增依赖
- Python >= 3.11

### 版本管理
- 使用 **git** 进行版本管理
- 每次功能变更后提交，commit message 用中文说明改动内容
- 主要分支：`main`

### 代码质量（Ruff）
- 行宽：100 字符
- 目标 Python 版本：3.13
- 启用的规则集：pycodestyle(E/W), pyflakes(F), isort(I), pep8-naming(N), pyupgrade(UP), bugbear(B), simplify(SIM) 等
- 提交前自动检查（pre-commit hook）

### 测试
- 测试文件需与 `src/` 结构保持对应
- 当前覆盖率目标：核心模块（db/network/config/notify）≥ 98%，整体 ≥ 90%
- `db` / `config` / `notify` 测试无需外部依赖
- `network` 测试使用 `AsyncMock` 模拟 HTTP
- `server` 测试使用 FastAPI `TestClient`
- `agents` 测试使用 `MagicMock` 模拟 LLM 调用
- 当源码新增 / 删除 / 修改功能时，同步更新对应测试

### 代码注释
- 每个模块需要有**中文**模块说明（docstring）
- 公共函数应有清晰的参数 / 返回值说明
- 复杂逻辑应有行内注释

### 敏感信息
- `config/settings.json` 包含 API Key 等敏感信息，已加入 `.gitignore`
- API Key 优先从环境变量 `DEEPSEEK_API_KEY` 读取

## 未来发展

- **优化 Agent**：提高 LLM 对论文的理解能力，生成恰当、简洁、包含重要内容、格式正确的笔记
- **Review System**：自动根据关键词下的论文和笔记生成综述
- **服务化**：支持邮箱订阅、关键词配置、定时推送，供多人使用
- **多模态**：考虑使用 Qwen-VL 系列模型增强图表理解能力，微调并提供个性化样例
