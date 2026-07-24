"""Jinja2 HTML 渲染器：负责所有 HTML 页面的渲染和写入

简化版 —— 数据来源仅限 SQLite DB + Zotero。
"""

import datetime
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from src.config import OUTPUT_DIR, ROOT_DIR

TEMPLATE_DIR = ROOT_DIR / "templates"
# autoescape=True：防止论文标题/摘要/LLM 输出中的 HTML 被直接注入页面（XSS）
_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)

# ─── 常量 ──────────────────────────────────────────────

REMARK_LABELS = {
    "important": "⭐ 重要",
    "useful": "👍 值得关注",
    "browse": "📄 可浏览",
    "skip": "🗑️ 跳过",
}

REMARK_COLORS = {
    "important": "#e74c3c",
    "useful": "#f39c12",
    "browse": "#3498db",
    "skip": "#95a5a6",
}

SECTION_LABELS = {
    "unmarked": "待审核",
    "marked": "已处理",
    "lurk": "延后处理",
}


# ─── 短标题生成 ────────────────────────────────────────


def _suggest_short_title(paper: dict) -> str:
    """为论文建议一个短标题。

    格式: 未读-关键词-年份-缩写
    """
    year = (paper.get("published", "") or "")[:4]
    if not year or not year.isdigit():
        year = "????"
    kw = paper.get("keyword_match", "") or "Unknown"
    title = paper.get("title", "") or ""

    # 提取缩写：取前两个非停用词的首字母 + 最后一个词的前几个字符
    stop_words = {"a", "an", "the", "for", "of", "in", "on", "at", "to", "by", "and", "or", "is", "with"}
    words = re.sub(r"[^\w\s-]", "", title).split()
    content_words = [w for w in words if w.lower() not in stop_words and len(w) > 1]

    if len(content_words) >= 3:
        abbrev = content_words[0][:4] + content_words[-1][:6]
    elif content_words:
        abbrev = "".join(w[:4] for w in content_words[:3])
    else:
        abbrev = words[0][:8] if words else "Paper"

    return f"未读-{kw}-{year}-{abbrev[:30]}"


# ─── 摘要/审阅页面 ────────────────────────────────────


def generate_summary_html(grouped: dict, output_dir: Path | None = None) -> Path:
    """生成审阅首页 HTML。"""
    from src.config import load_settings

    if output_dir is None:
        output_dir = OUTPUT_DIR

    unmarked = grouped.get("unmarked", [])
    marked = grouped.get("marked", [])
    lurk = grouped.get("lurk", [])
    total = len(unmarked) + len(marked) + len(lurk)

    # 为每篇论文添加建议的 shortTitle
    for p in unmarked + marked + lurk:
        if not p.get("short_title"):
            p["_suggested_short_title"] = _suggest_short_title(p)

    all_papers = unmarked + marked + lurk

    stats = {
        "total": total,
        "important": sum(1 for p in all_papers if p.get("llm_remark") == "important"),
        "useful": sum(1 for p in all_papers if p.get("llm_remark") == "useful"),
        "browse": sum(1 for p in all_papers if p.get("llm_remark") == "browse"),
        "unmarked": len(unmarked),
    }

    # 从配置文件读取 fetch 参数，传给前端模板
    cfg = load_settings()
    fetch_config = {
        "max_results": cfg.fetch.max_results,
        "lookback_days": cfg.fetch.lookback_days,
    }

    template = _env.get_template("summary.html")
    html = template.render(
        update_time=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        stats=stats,
        sections={"unmarked": unmarked, "marked": marked, "lurk": lurk},
        REMARK_LABELS=REMARK_LABELS,
        REMARK_COLORS=REMARK_COLORS,
        SECTION_LABELS=SECTION_LABELS,
        fetch_config=fetch_config,
    )

    summaries_dir = output_dir / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)
    filepath = summaries_dir / "index.html"
    filepath.write_text(html, encoding="utf-8")
    return filepath


