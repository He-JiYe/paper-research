# Paper Research

每天定时增量抓取 Arxiv 上指定关键词的预印本，使用 LLM（Deepseek / ollama）进行评分初选，支持 Web 交互式审阅并导入 Zotero。

## 核心要点

1. 不保留向后兼容。过时的直接删，别加兼容层、别写 migration；
   
2. 选能满足当前需求的最简单实现。不要预防性抽象，不要多此一举的配置层；
   
3. 系统分层长。先跑通一个最小的端到端版本，再往上加东西。绝不为了完成未完成的复杂度拆掉能跑的东西。
   
4. 优先用成熟的、有人维护的库。没有明确理由别自己重写。

5. 组件保持模块化，关注点分离；
   
6. 先翻项目里已有的依赖能做什么，再考虑加新包或自己写。别上来就假设库里没有。
   
7. 架构决策往长了做。不接受“先这样以后再换”的临时方案。
   
8. 先看成熟产品怎么解决同一个问题，用已验证的模式，别从零发明。

## 核心流程

```
调度器每天定时抓取（错过补抓一次）
  → 多数据源抓取（各源独立参数，多关键词全源共享）
  → LLM 评分（重要/有用/浏览/跳过 + 0-1 分）
  → SQLite 数据库存储
  → 邮件通知今日抓取结果（只通知今日，不通知历史）
  → Web 审阅（默认总体，可按日期范围/评级筛选）
  → 审阅决策：
    ├── 忽略（不入 Zotero）
    ├── 延后（放入 Lurk 区）
    └── 批量导入 Zotero（指定收藏夹 + 短标题）
```

## 项目架构

```
.venv/                   # 虚拟环境 (uv 管理)
app/                     # 前端 SPA + 邮件模板 + 静态元数据（git 版本化，前后端分离）
├── index.html / app.js / style.css   # 前端 SPA（只做筛选/标记/批量导入/排序；搜索为客户端全文过滤）
├── app-meta.json        # 前端+邮件共享静态元数据（arxiv 分类、评级标签/颜色、分区）——评级颜色单一来源
└── email/               # 邮件模板
config/                  # config.yaml（每源独立参数 + 多关键词全源共享 + llm provider）
examples/                # few-shot 示例（每个关键词 {keyword}-few-shot.txt，评分时注入）
data/                    # SQLite 运行时数据库（动态数据）
src/
├── main.py              # CLI 入口（argparse + dispatch）
├── commands.py          # CLI 命令实现（fetch/serve/status/notify）
├── paths.py             # 路径常量（ROOT/CONFIG/DATA/APP）
├── logging_setup.py     # 统一日志（每日 YYYY-MM-DD.log + errors.log）
├── static_meta.py       # app-meta 静态元数据读写（单一来源）
├── core/                # 纯模型层（零 src 依赖）
│   ├── models.py        # Record / KeywordItem / record_to_row
│   ├── config.py        # Pydantic 配置模型（AppConfig + 各子模型）
│   ├── fetch.py         # FetchOptions 抽象（每源子类化，core 无 network 依赖）
│   ├── score.py         # LLMResult / ScoreSource（评分结果模型）
│   └── text.py          # tokenize / 缩写 / relevance_score / suggest_short_title（短标题单一来源）
├── config/              # 配置加载层
│   ├── loader.py        # config.yaml 读写 + ${ENV} 解析 + Pydantic 构建 + options 动态解析
│   └── settings.py      # get_active_keywords 等派生助手
├── db/                  # SQLite 层
│   ├── enums.py         # PaperStatus/UserMark/FetchLogStatus + PENDING_WHERE（单一来源）
│   ├── schema.py        # 建表 SQL（+score_source、fetch_date 索引）
│   ├── connection.py    # 连接管理（WAL + 写锁）
│   ├── papers.py        # 论文 CRUD（get_pending(fetch_date_from=) 等）
│   └── fetch_logs.py    # 统计 + 日志（has_successful_since 补抓判定）
├── network/             # 数据源抽象 + 注册表
│   ├── base.py          # BaseSource 模板方法（adapt/_fetch/_try_fetch/fetch/source_name）
│   ├── registry.py      # REGISTRY 多层注册表（sources / options）
│   └── source/arxiv.py  # ArxivSource / ArxivOptions
├── scorer/              # 评分层
│   ├── provider.py      # ChatProvider 抽象 + OpenAIProvider(deepseek) + OllamaProvider(本地)
│   ├── llm.py           # PaperScorer（依赖 provider，不可用自动 fallback）
│   ├── fallback.py      # 关键词相关性兜底评分（依赖 core.text，无 network 反向依赖）
│   ├── parse.py         # JSON 解析原语
│   └── prompt.py        # SUMMARIZE_PROMPT + few-shot 加载
├── pipeline/            # 抓取编排
│   ├── fetch.py         # run_fetch_pipeline（多源→评分→入库→日志；写 fetch_date）
│   └── score.py         # score_rows 唯一评分+写回循环
├── notify/              # 邮件通知
│   ├── renderer.py      # 邮件内容生成（颜色读 app-meta）
│   ├── sender.py        # EmailNotifier（SMTP）
│   └── report.py        # send_fetch_report 统一入口（只通知今日）
├── serve/               # FastAPI
│   ├── __init__.py      # app 工厂 + lifespan + run_server（注入 app.state.runtime）
│   ├── runtime.py       # Runtime 可注入上下文（settings/zotero/scheduler，无全局单例）
│   ├── payloads.py      # /api/papers 载荷（range 过滤 + 建议短标题）
│   ├── scheduler.py     # FetchScheduler（每日定时 + 补抓；补抓=正式今日抓取发邮件）
│   └── routes/          # papers.py（papers/static/fetch-status/fetch-events/mark）+ zotero.py（导入闭环）
└── zotero/              # Zotero 集成
    ├── client.py        # ZoteroClient（pyzotero 封装；ensure_collection 公开）
    ├── convert.py       # paper→item / collection 路径解析（split_collection_path 单一来源）
    ├── manager.py       # ZoteroImportManager（单飞批量导入）
    └── utils.py         # 标签/Extra 编解码/响应 key
```

## CLI 命令

```bash
uv run paper-research fetch               # 抓取（多源×多关键词）→ LLM 评分 → 入库
uv run paper-research fetch -k <keyword> # 仅抓取指定关键词
uv run paper-research fetch --dry-run    # 预览模式，不写入数据库
uv run paper-research serve               # 启动 Web 审阅服务（内置调度器每天定时抓取）
uv run paper-research serve --open-browser  # 仅打开浏览器（服务已由后台/开机自启运行，不启动服务）
uv run paper-research status              # 查看统计信息
uv run paper-research notify              # 手动发送今日邮件通知
uv run paper-research autostart           # 注册开机自启（on/off/status/run-now）
```

全局命令 `paper`（`uv tool install --editable .` 安装，见 scripts/install_paper.ps1）为以上命令的别名：
`paper fetch/serve/status/notify` 等价对应子命令，`paper autostart [on|off|status|run-now]` 转发到
`scripts/register_task.ps1`（需管理员，非管理员打印手动命令）。editable 下 src/ 改动即时生效。

## 数据流

```
config.yaml → config/loader (Pydantic) ──→ network (REGISTRY.sources/options)
                                              │ fetch(options)
                                              ▼
                                    pipeline.fetch (score_rows + fetch_date)
                                              │
                              db.papers ──→ /api/papers?range= ──→ 前端 SPA
                                              │
                             notify.report (今日过滤) ──→ SMTP 邮件
                                              │
                        /api/zotero/import-batch ──→ manager ──→ pyzotero
```

## 核心设计约定

- **短标题单一来源**：`core.text.suggest_short_title(paper, *, keyword, prefix)`。
  Web 回填 `未读-{关键词}-{年份}-{缩写}`，Zotero 兜底 `{关键词或Unknown}-{年份}-{缩写}`。
- **评级颜色单一来源**：`app/app-meta.json` 的 `remark_colors`。前端 app.js 注入 CSS 变量
  `--remark-*`，邮件 renderer 直接读 app-meta；style.css 用 `var(--remark-*)` 引用。
- **今日过滤统一**：邮件（`report.send_fetch_report`）与 Web（`/api/papers?range=today`）
  都按 `papers.fetch_date`（抓取运行日，date-only）过滤；今日日期按系统本地时间计算
  （`config.settings.today_str`，不依赖配置时区）。
- **LLM provider 抽象**：`scorer/provider.py` 的 `ChatProvider`（check/chat）。
  deepseek → OpenAI SDK；ollama → 原生 `/api/chat`（httpx）。prompt/few-shot/parse/fallback 与 provider 无关。
- **网络层精简**：BaseSource 只保留 `fetch`（按关键词），`fetch_by_ids`/`search` 已移除；
  前端保留客户端全文搜索（纯前端过滤，不依赖后端）。
- **无反向依赖**：core 零 src 依赖；scorer 不依赖 network（fallback 用 core.text.relevance_score）。

## 开发规范

- 环境：uv 管理（`.venv/`），Python >= 3.11
- 质量：ruff（line-length=100, py311），`uv run ruff check` / `uv run ruff format`
- 测试：pytest（coverage ≥ 90%），`uv run pytest` / `uv run pytest --cov=src`
- 注释：每个模块有中文 docstring，公共函数有参数/返回值说明
- 版本：git 每次功能变更后提交，commit message 用中文；主要分支 `main`
- 敏感信息：`config/config.yaml` 含 API Key 等，避免加入 `.git`
