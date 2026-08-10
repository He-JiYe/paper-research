"""抓取管道：供 CLI (cmd_fetch) 与调度器 (FetchScheduler) 复用。

职责：
1. 从数据源抓取论文（network 层契约：REGISTRY.sources/options + BaseSource.fetch）
2. LLM 初筛评分（pipeline.score.score_rows，唯一评分循环）
3. 存入 SQLite 数据库（写行时显式置 fetch_date = 抓取运行日）
4. 记录抓取日志

注意：本管道不发送邮件。邮件只由调度器（每日定时抓取后）或用户手动触发
（CLI notify）发送。
"""

import asyncio
import dataclasses
import logging

from src.config.settings import now_str, today_str
from src.core.models import record_to_row
from src.network import REGISTRY
from src.pipeline.score import score_rows
from src.scorer import PaperScorer

logger = logging.getLogger(__name__)

_DRY_RUN_PREVIEW = 10  # dry-run 预览最多打印的论文数

def _result(
    fetched: int,
    new: int,
    summarized: int,
    papers_fetched: list,
    today: str,
) -> dict:
    """组装管道返回契约（各返回路径统一形状）。"""
    return {
        "fetched": fetched,
        "new": new,
        "summarized": summarized,
        "papers_fetched": papers_fetched,
        "fetch_date": today,
    }


async def run_fetch_pipeline(
    settings,
    keywords,
    max_results,
    db=None,
    mode="incremental",
    dry_run=False,
):
    """核心抓取管道（数据写入 SQLite DB）。

    执行顺序: 构建 skip_ids → 抓取 → 去重 → LLM 评分 → 写入 DB → 记录日志

    Args:
        settings:     配置对象（Pydantic AppConfig）
        keywords:     关键词列表（全源共享）
        max_results:  每关键词最大结果数（>0 时覆盖各源配置）
        db:           PaperDB 实例（None 时自动创建）
        mode:         "incremental"（增量）| "historical"（全量）
        dry_run:      True 时只抓取不写入

    Returns:
        dict: 统计数据（含 fetch_date）
    """
    if db is None:
        from src.db import PaperDB

        db = PaperDB()

    is_historical = mode == "historical"
    today = today_str()  # fetch_date 与邮件/Web 的"今日"同口径（系统本地时间）

    if not settings.fetch.sources:
        logger.warning("未配置任何数据源（fetch.sources 为空）")
        return _result(0, 0, 0, [], today)

    # ── 1. 遍历多数据源抓取（共享 keywords，各源独立 options/skip）──
    records_all: list = []
    for source_cfg in settings.fetch.sources:
        source_name = source_cfg.source
        source_cls = REGISTRY.sources.get(source_name)
        source = source_cls()

        # ── 1.1 构建 skip_ids（按 source 作用域：DB 已有）────
        skip_ids: set[str] = db.get_existing_ids(source_name)
        logger.info("[%s]: DB 已有 %s 篇", source_name, len(skip_ids))
        logger.info("[%s] 开始抓取 (%s模式，跳过 %s 篇已有)...", source_name, mode, len(skip_ids))

        # ── 1.2 构建该源 Options：源配置 asdict + 注入共享 keywords ──
        # loader 恒把 options 解析为 FetchOptions 实例（registry 解析），此处直接 asdict。
        opts_dict = dataclasses.asdict(source_cfg.options)
        # page_size 由 max_results/lookback_days 经 __post_init__ 派生；剥离避免 config 历史值
        # 泄漏到本次模式重建（如增量 lookback=3 会算成 400，历史模式重建若携带则仍请求 400）。
        opts_dict.pop("page_size", None)
        opts_dict.update(
            {
                "keywords": keywords,
                "skip_ids": skip_ids,
                "sort_by": "relevance"
                if is_historical
                else opts_dict.get("sort_by", "relevance"),
                "lookback_days": 0
                if is_historical
                else opts_dict.get("lookback_days", 0),
            }
        )
        if max_results and max_results > 0:
            opts_dict["max_results"] = max_results  # 调用方覆盖（CLI/调度器）
        opt_cls = REGISTRY.options.get(source_name)  # 未知源名 → 已抛 ValueError
        options = opt_cls.from_dict(opts_dict)

        records = await source.fetch(options)
        records_all.extend(records)

    # Record → DB 行 dict（含 source/source_id/raw_data/pdf_url），统一补 fetch_date
    to_score = [record_to_row(r) for r in records_all]
    for p in to_score:
        p["fetch_date"] = today

    # 同一篇（source, source_id）可能被多个关键词命中而重复出现：评分前按主键去重，
    # 避免重复 LLM 调用，也避免 INSERT OR IGNORE 静默丢弃第二行。
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for p in to_score:
        key = (p.get("source", ""), p.get("source_id", ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)
    to_score = deduped

    # dry-run 早退：无论抓取到几篇都不写库、不评分、不写 fetch_log
    if dry_run:
        logger.info("共获取 %s 篇论文", len(to_score))
        for p in to_score[:_DRY_RUN_PREVIEW]:
            logger.info("- [%s:%s] %s", p["source"], p["source_id"], p["title"][:80])
        if len(to_score) > _DRY_RUN_PREVIEW:
            logger.info("... 还有 %s 篇", len(to_score) - _DRY_RUN_PREVIEW)
        return _result(len(to_score), 0, 0, to_score, today)

    if not to_score:
        logger.info("无新论文")
        # 即便 0 篇新论文也记录一条成功日志，保证补抓判定正确
        # （否则调度器重启会重复补抓、/api/fetch/status 误报 last_success=None）。
        db.add_fetch_log(
            keywords_used=len(keywords),
            papers_fetched=0,
            papers_new=0,
            papers_summarized=0,
            run_time=now_str(),
        )
        return _result(0, 0, 0, [], today)

    logger.info("获取: %s 篇新论文", len(to_score))

    # ── 2. LLM 评分（不可用自动 fallback，无 API Key 无需分支）──
    # 构造内含 provider.check() 同步网络检测（最长数秒），放线程执行避免阻塞 serve 事件循环
    scorer = await asyncio.to_thread(PaperScorer.from_settings, settings)
    summarized_count, to_score = await score_rows(scorer, to_score)

    # ── 3. 写入数据库 ────────────────────────────────────
    new_count = db.add_papers(to_score)
    logger.info("已写入数据库: %s/%s 篇", new_count, len(to_score))

    # ── 4. 记录抓取日志 ──────────────────────────────────
    db.add_fetch_log(
        keywords_used=len(keywords),
        papers_fetched=len(to_score),
        papers_new=new_count,
        papers_summarized=summarized_count,
        run_time=now_str(),  # 与补抓判定的 run_time 同口径（系统本地时间）
    )

    return _result(len(to_score), new_count, summarized_count, to_score, today)
