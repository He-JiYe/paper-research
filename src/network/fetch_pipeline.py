"""共享的抓取管道：供 CLI (cmd_fetch) 和 Web API (_do_fetch) 复用

职责：
1. 从 Arxiv 抓取论文（不下载 PDF）
2. LLM 初筛评分
3. 存入 SQLite 数据库
4. 记录抓取日志

注意：本管道不发送邮件。邮件只由调度器（每日定时抓取后）或
用户手动触发（CLI notify / 页面推送按钮）发送。
页面渲染已改为前端 SPA 动态渲染（/api/papers），不再生成 HTML。
"""

import asyncio
import logging

from src.scorer import PaperScorer

logger = logging.getLogger(__name__)


async def run_fetch_pipeline(
    settings,
    keywords,
    max_results,
    db=None,
    mode="incremental",
    dry_run=False,
):
    """核心抓取管道（数据写入 SQLite DB）

    执行顺序: 构建 skip_ids → 抓取 → 去重 → LLM 评分 → 写入 DB → 生成 HTML

    Args:
        settings:     配置对象
        keywords:     关键词列表
        max_results:  每关键词最大结果数
        db:           PaperDB 实例（None 时自动创建）
        mode:         "incremental"（增量）| "historical"（全量）
        dry_run:      True 时只抓取不写入

    Returns:
        dict: 统计数据
    """
    from src.network.factory import get_source

    if db is None:
        from src.db import PaperDB
        db = PaperDB()

    is_historical = mode == "historical"

    # ── 1. 通过工厂获取数据源 ──────────────────────────────
    source = get_source(settings)

    # ── 2. 构建 skip_ids ──────────────────────────────────
    # 同时检查 DB 和 Zotero
    from src.zotero import ZoteroClient

    skip_ids: set[str] = set()

    # 从 DB 获取已有 ID
    existing_db = db.get_existing_arxiv_ids()
    skip_ids |= existing_db

    # 从 Zotero 获取已有 ID
    try:
        zotero = ZoteroClient(
            settings.zotero.api_key,
            settings.zotero.library_id,
            settings.zotero.library_type,
        )
        existing_zotero = zotero.get_existing_arxiv_ids()
        skip_ids |= existing_zotero
        for kw in keywords:
            print(f"    [{kw.keyword}]: Zotero 已有 {len(existing_zotero)} 篇 + DB {len(existing_db)} 篇")
    except Exception as e:
        print(f"  [!] 连接 Zotero 失败（仅用 DB 去重）: {e}")

    # ── 3. 抓取 ───────────────────────────────────────────
    print(f"  📡 开始抓取 ({mode}模式，跳过 {len(skip_ids)} 篇已有)...")
    all_papers, _, _ = await source.fetch_all(
        keywords,
        settings,
        max_results=max_results,
        historical=is_historical,
        skip_ids=skip_ids,
    )

    if not all_papers:
        print("  📭 无新论文")
        return {"fetched": 0, "new": 0, "summarized": 0, "papers_fetched": []}

    # dry-run
    if dry_run:
        print(f"  📄 共获取 {len(all_papers)} 篇论文")
        for p in all_papers[:10]:
            print(f"    - [{p['arxiv_id']}] {p['title'][:80]}")
        if len(all_papers) > 10:
            print(f"    ... 还有 {len(all_papers) - 10} 篇")
        return {"fetched": len(all_papers), "new": 0, "papers_fetched": all_papers}

    print(f"  📊 获取: {len(all_papers)} 篇新论文")

    # ── 4. LLM 评分 ───────────────────────────────────────
    to_score = all_papers
    summarized_count = 0
    if to_score:
        print(f"  [*] 正在 LLM 初筛 {len(to_score)} 篇论文...")
        api_key = settings.llm.api_key
        scorer = PaperScorer(
            api_key=api_key,
            api_base=settings.llm.api_base,
            model=settings.llm.model,
            temperature=settings.llm.temperature,
            max_tokens=settings.llm.max_tokens,
        )

        results: list = []
        if api_key:
            try:
                results = await scorer.score_batch_async(to_score)
            except Exception as e:
                print(f"  [!] 异步评分失败，回退到串行: {e}")
                results = scorer.score_batch(to_score)
        else:
            loop = asyncio.get_running_loop()
            results = []
            for paper in to_score:
                result = await loop.run_in_executor(
                    None,
                    scorer.score,
                    paper.get("title", ""),
                    paper.get("abstract", ""),
                    paper.get("categories", ""),
                    paper.get("keyword_match", ""),
                )
                results.append(result)

        # 将 LLM 结果写入 paper dict
        for paper, result in zip(to_score, results, strict=False):
            if result:
                paper["llm_summary"] = result.summary
                paper["llm_remark"] = result.remark
                paper["llm_reason"] = result.reason
                paper["llm_score"] = result.score
                paper["status"] = "summarized"
            else:
                paper["status"] = "new"

        summarized_count = sum(1 for r in results if r)
        print(f"  [OK] 初筛完成: {summarized_count}/{len(to_score)} 篇")

    # ── 5. 写入数据库 ────────────────────────────────────
    new_count = db.add_papers(to_score)
    print(f"  [OK] 已写入数据库: {new_count}/{len(to_score)} 篇")

    # ── 6. 记录抓取日志 ──────────────────────────────────
    db.add_fetch_log(
        keywords_used=len(keywords),
        papers_fetched=len(all_papers),
        papers_new=new_count,
        papers_summarized=summarized_count,
    )

    return {
        "fetched": len(all_papers),
        "new": new_count,
        "summarized": summarized_count,
        "papers_fetched": all_papers,
    }
