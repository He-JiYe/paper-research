# Paper Research — Web API 接口文档

serve（FastAPI）监听 `127.0.0.1:8899`（config.yaml `server.port`），前端 SPA 由同一端口静态托管。
无鉴权；表单端口用 `application/x-www-form-urlencoded`，JSON 端口用 `application/json`。

> 前端只做筛选（评级 + 日期范围 + 客户端全文搜索）、标记、批量导入 Zotero、排序；抓取由 serve
> 内置调度器每天定时完成，前端 SSE 订阅 `/api/fetch/events` 即时感知新抓取并自动刷新一次
> （连接/重连时单次检查 `/api/fetch/status` 补基线，另设 60s 轮询兜底防 SSE 断流）。

---

## 端口总表（9 条）

| # | 方法 + 路径 | 入参 | 返回 | 委托模块 | 前端调用点 |
|---|---|---|---|---|---|
| 1 | `GET /api/papers` | `?range=all\|today\|7d\|30d`（默认 all，非法值 400） | `{update_time, range, stats, sections:{unmarked,marked,lurk}}` | `payloads.build_papers_payload(db, range, today)` | `app.js` init / loadPapers / applyRange |
| 2 | `GET /api/static` | — | `{categories, remark_labels, remark_colors, section_labels}` | `static_meta.load_app_meta`（app/app-meta.json） | `app.js` init |
| 3 | `GET /api/fetch/status` | — | `{last_success, papers_new}`（无成功记录时 last_success=None） | `db.get_last_success` | `app.js` startAutoRefresh（连接/重连时单次检查 + 60s 轮询兜底） |
| 4 | `POST /mark` | form：`source`/`source_id`/`mark_type`(ignore\|lurk\|pending)/`short_title`(可省，前端不发送) | `{status, source, source_id, mark_type}` | `db.update_mark` | `app.js` markPaper |
| 5 | `GET /api/zotero/collections` | — | `{collections:[{name,key,path,depth,parentCollection}]}` | `ZoteroClient.list_collections` | `app.js` openBatchImport |
| 6 | `POST /api/zotero/import-batch` | JSON：`{items:[{source,source_id,short_title,collection_key}]}`（items 须为列表，非法 400） | `{status:"submitted", job_id, busy:true, items:<提交条数>}`；busy 409 | `ZoteroImportManager.submit`（批量，单篇是其特例） | `app.js` confirmBatchImport |
| 7 | `GET /api/zotero/import/status` | — | `{busy, job:{id,step,log[],items_status,status,result,error,...}}` | `ZoteroImportManager.status` | `app.js` pollImportStatus |
| 8 | `GET /api/import/events` | SSE（`text/event-stream`） | 连接即推当前状态；完成推 `import-done`/`error`；期间 15s 心跳注释行；2h 整体超时 | `runtime.sse_event_stream` / `ZoteroImportManager` on_done | `app.js` pollImportStatus |
| 9 | `GET /api/fetch/events` | SSE（`text/event-stream`） | 调度器抓取完成时推 `fetch-done`；期间 15s 心跳注释行；2h 整体超时 | `runtime.sse_event_stream` / `FetchScheduler` on_fetch_done | `app.js` startAutoRefresh |

> 路由文件：`src/serve/routes/{papers,zotero}.py`；运行时上下文 `app.state.runtime`（src/serve/runtime.py）。

---

## 关键字段约定

### papers 行（来自 SQLite `papers` 表）

`source, source_id, title, authors, abstract, url, pdf_url, categories, published, updated,
keyword_match, raw_data(JSON), llm_summary, llm_remark, llm_reason, llm_score, score_source,
user_mark, short_title, zotero_key, status, fetch_date, marked_date`

前端额外消费：无 `short_title` 的论文由 `build_papers_payload` 注入 `suggested_short_title`（回填短标题输入框）。

### 日期范围（`/api/papers?range=`）

| range | 语义 | 起始日 |
|---|---|---|
| `all`（默认） | 全部 | 不限 |
| `today` | 今日抓取 | 今天 |
| `7d` | 近 7 天 | 今天 - 6 |
| `30d` | 近 30 天 | 今天 - 29 |

过滤依据 `papers.fetch_date >= 起始日`（fetch_date 为抓取运行日，date-only）。

### stats（`/api/papers`）

`{total, important, useful, browse, unmarked}` —— 均按当前 range 过滤（`db.get_stats(fetch_date_from)` 派生）。

---

## 变更记录（重构后）

- **精简**：移除抓取/推送/搜索/关键词设置相关端口（`/api/fetch`、`/api/keyword-fetch`、`/api/search-preview`、
  `/api/search-import`、`/api/push`、`/api/keywords`、`/api/fetch-status-stream`(SSE)、`/api/scheduler/status`）；
  移除 `/api/pdf/{source}/{source_id}`（前端不再消费 PDF 重定向）。抓取改由 serve 内置调度器每天定时完成。
- **新增**：`/api/fetch/status` 供前端轮询，检测到新抓取后自动刷新一次；`/api/import/events`（SSE）
  导入完成信号，前端订阅即收，无需高频轮询。
- **变化**：`/api/papers` 新增 `range` 参数（日期范围筛选），返回 `{update_time, range, stats, sections}`；
  「今日」按系统本地时间计算（与邮件今日过滤、调度判重同一口径，见 `config.settings.today_str`）。
