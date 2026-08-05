# Paper Research

每天定时增量抓取 Arxiv 上指定关键词的预印本，使用 LLM（Deepseek）进行评分初选，支持 Web 交互式审阅并导入 Zotero。

## 核心流程

```
定时/手动抓取
  → Arxiv API（仅元数据，不下载 PDF）
  → LLM 评分（重要/有用/浏览/跳过）
  → SQLite 数据库存储
  → 生成 Web 审阅页面
  → 审阅决策：
    ├── 忽略（不入 Zotero）
    ├── 待处理（放回等待区）
    └── 导入 Zotero（指定收藏夹 + 短标题）
```

## 项目架构

```
.venv/                   # 虚拟环境 (uv 管理)
config/                  # 配置文件（config.yaml）
data/
├── static/              # 静态数据 JSON（app-meta.json，git 版本化）
└── papers.db            # SQLite 运行时数据库（动态数据）
src/
├── __init__.py          # 包标识 + 版本号
├── main.py              # CLI 入口（argparse 解析 + 命令分发）
├── commands.py          # CLI 命令实现（fetch/serve/status/notify）
├── config.py            # 配置加载
├── db.py                # SQLite 数据库层（PaperDB：论文 CRUD、统计、抓取日志）
├── scorer.py            # PaperScorer（标题+摘要 → LLM 评分/评级）
│
├── network/
│   ├── __init__.py      # 网络包初始化
│   ├── base.py          # 数据源抽象基类
│   ├── arxiv.py         # Arxiv API 客户端（Atom XML 解析、搜索、PDF 下载）
│   ├── factory.py       # 数据源工厂
│   └── fetch_pipeline.py # 抓取管道（协调抓取→评分→存储→通知）
│
├── serve/
│   ├── __init__.py      # Serve 包初始化
│   ├── server.py        # FastAPI Web 服务（JSON API + 前端静态托管）
│   ├── static_data.py   # 静态元数据默认值 + 短标题建议 + app-meta 读写
│   ├── payloads.py      # /api/papers 载荷构建（纯函数）
│   ├── frontend/        # 前端 SPA（index.html + app.js + style.css）
│   └── scheduler.py     # 定时抓取调度器
│
├── zotero/
│   ├── __init__.py      # Zotero 包初始化
│   ├── client.py        # Zotero API 客户端（pyzotero 封装）
│   └── models.py        # 标签常量、Extra 编解码、数据转换、短标题生成
│
└── notify.py            # SMTP 邮件通知
```

## CLI 命令

```bash
# 核心工作流
uv run paper-research fetch              # 增量抓取 Arxiv → LLM 评分 → 写入 DB
uv run paper-research fetch -k <keyword> # 仅抓取指定关键词
uv run paper-research fetch --dry-run    # 预览模式，不写入数据库
uv run paper-research serve              # 启动 Web 审阅服务
uv run paper-research status             # 查看统计信息
uv run paper-research notify             # 手动触发邮件通知

# 开发工具
uv run ruff check                        # Ruff 代码检查
uv run ruff check --fix                  # 自动修复
uv run ruff format                       # 代码格式化
uv run pytest                            # 运行测试
```

## 数据流

```
Arxiv API ──→ network/fetch_pipeline.py ──→ db.py (SQLite) ──→ /api/papers ──→ 前端 SPA
  (Atom XML)   (并发 fetch + LLM 评分 + 写入)  (PaperDB)      (JSON API)     (frontend/app.js 动态渲染)
                                                    │
                                                    └──→ serve/server.py ──→ Zotero
                                                         (Web 交互/标记)      (导入指定收藏夹)
```

## Zotero 收藏夹映射

| 标记类型 | Zotero 收藏夹路径 | 说明 |
|---------|-------------------|------|
| ignore | 不入 Zotero | 仅更新 DB 状态 |
| lurk | Paper Research/Lurk | 延后处理 |
| skim | Paper Research/Archive/Skim | 粗读 |
| deep_read | Paper Research/Archive/Deep Read | 精读 |
| pending | Paper Research/Inbox | 取消标记，放回待审阅 |

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

### 代码注释
- 每个模块需要有**中文**模块说明（docstring）
- 公共函数应有清晰的参数/返回值说明
- 复杂逻辑应有行内注释

### 敏感信息
- `config/config.yaml` 包含 API Key 等敏感信息，避免加入 `.git`
