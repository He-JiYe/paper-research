"""API 载荷构建：从 PaperDB 组装 /api/papers 的响应 JSON（纯函数，可单测）

原 renderer.generate_summary_html 的分组 + stats + 建议短标题逻辑迁至此，
去掉 Jinja2 渲染部分，仅产出 JSON，由前端动态渲染。
"""

import datetime

from src.serve.static_data import suggest_short_title


def build_papers_payload(db) -> dict:
    """构建 /api/papers 响应。

    Args:
        db: PaperDB 实例（可传 mock 以便单测）。

    Returns:
        {
            "update_time", "stats", "fetch_config",
            "sections": {"unmarked": [...], "marked": [...], "lurk": [...]}
        }
    """
    unmarked = db.get_pending()
    marked = db.get_marked()
    lurk = db.get_lurk()

    # 为无 short_title 的论文补建议短标题（前端短标题输入框回填用）
    for group in (unmarked, marked, lurk):
        for p in group:
            if not p.get("short_title"):
                p["suggested_short_title"] = suggest_short_title(p)

    all_papers = unmarked + marked + lurk
    stats = {
        "total": len(all_papers),
        "important": sum(1 for p in all_papers if p.get("llm_remark") == "important"),
        "useful": sum(1 for p in all_papers if p.get("llm_remark") == "useful"),
        "browse": sum(1 for p in all_papers if p.get("llm_remark") == "browse"),
        "unmarked": len(unmarked),
    }

    from src.config import load_settings

    cfg = load_settings()
    return {
        "update_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "stats": stats,
        "fetch_config": {
            "max_results": cfg.fetch.max_results,
            "lookback_days": cfg.fetch.lookback_days,
        },
        "sections": {"unmarked": unmarked, "marked": marked, "lurk": lurk},
    }
