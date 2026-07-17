"""Jinja2 HTML 渲染器：负责所有 HTML 页面的渲染和写入

职责：
- 加载 Jinja2 模板并渲染为 HTML 字符串
- 提供与旧 generator.py 一致的对外 API（generate_summary_html, generate_notes_index_html）
- 包含模板渲染所需的所有常量和辅助数据
"""

import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from src.config import OUTPUT_DIR, ROOT_DIR

TEMPLATE_DIR = ROOT_DIR / "templates"
_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))

# ─── 常量（从 generator.py 迁入）───────────────────────────

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


# ─── 摘要页面 ──────────────────────────────────────────────


def generate_summary_html(grouped: dict, output_dir: Path | None = None) -> Path:
    """生成交互式 HTML summary 文件。"""
    if output_dir is None:
        output_dir = OUTPUT_DIR

    # 准备模板上下文
    unmarked = grouped.get("unmarked", [])
    marked = grouped.get("marked", [])
    lurk = grouped.get("lurk", [])
    total = len(unmarked) + len(marked) + len(lurk)

    all_papers = unmarked + marked + lurk

    # 统计
    stats = {
        "total": total,
        "important": sum(1 for p in all_papers if p.get("llm_remark") == "important"),
        "useful": sum(1 for p in all_papers if p.get("llm_remark") == "useful"),
        "browse": sum(1 for p in all_papers if p.get("llm_remark") == "browse"),
        "skip": sum(1 for p in all_papers if p.get("llm_remark") == "skip"),
        "skim": len([p for p in marked if p.get("user_mark") == "skim"]),
        "deep_read": len([p for p in marked if p.get("user_mark") == "deep_read"]),
        "lurk": len(lurk),
        "unmarked": len(unmarked),
    }

    # 关键词分类信息
    category_info = {}
    for p in all_papers:
        raw = p.get("keyword_match", "")
        if not raw:
            continue
        cat_id = raw.lower().replace(" ", "-")
        if cat_id not in category_info:
            category_info[cat_id] = {"name": raw, "total": 0, "unmarked": 0}
        category_info[cat_id]["total"] += 1
        if not p.get("user_mark"):
            category_info[cat_id]["unmarked"] += 1

    template = _env.get_template("summary.html")
    html = template.render(
        update_time=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        stats=stats,
        category_info=category_info,
        sections={"unmarked": unmarked, "marked": marked, "lurk": lurk},
        REMARK_LABELS=REMARK_LABELS,
        REMARK_COLORS=REMARK_COLORS,
        SECTION_LABELS=SECTION_LABELS,
    )

    summaries_dir = output_dir / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)
    filepath = summaries_dir / "index.html"
    filepath.write_text(html, encoding="utf-8")
    return filepath


# ─── 笔记画廊 ──────────────────────────────────────────────


def generate_notes_index_html(
    papers: list[dict],
    output_dir: Path | None = None,
    conn=None,
) -> Path:
    """生成笔记管理画廊页 output/notes/index.html。"""
    if output_dir is None:
        output_dir = OUTPUT_DIR

    notes_dir = output_dir / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)

    # 筛选有笔记的论文
    note_papers = [
        p
        for p in papers
        if p.get("user_mark") in ("skim", "deep_read")
        or (notes_dir / p["arxiv_id"] / "note.md").exists()
    ]
    seen = set()
    unique = []
    for p in note_papers:
        aid = p.get("arxiv_id", "")
        if aid not in seen:
            seen.add(aid)
            unique.append(p)
    note_papers = unique

    deep_read_count = sum(1 for p in note_papers if p.get("user_mark") == "deep_read")
    skim_count = sum(1 for p in note_papers if p.get("user_mark") == "skim")
    lurk_count = sum(1 for p in note_papers if p.get("user_mark") == "lurk")
    ignore_count = sum(1 for p in note_papers if p.get("user_mark") == "ignore")
    pending_count = sum(1 for p in note_papers if not p.get("user_mark"))

    # 加载分类
    categories_list = []
    note_categories_map = {}
    _owned_conn = False
    if conn is None:
        from src.config import get_db_path
        from src.db import get_connection

        try:
            _db_path = get_db_path()
            conn = get_connection(_db_path)
            _owned_conn = True
        except Exception:
            conn = None
    try:
        if conn:
            from src.db import get_all_categories, get_note_categories

            categories_list = [
                c for c in get_all_categories(conn) if c["name"] not in ("精读", "粗读")
            ]
            for p in note_papers:
                aid = p.get("arxiv_id", "")
                cats = get_note_categories(conn, aid)
                if cats:
                    note_categories_map[aid] = cats
    finally:
        if _owned_conn and conn:
            conn.close()

    # 为每个笔记卡片准备缩略图
    card_data = []
    for p in note_papers:
        aid = p["arxiv_id"]
        fig_dir = notes_dir / aid / "figures"
        thumb_name = ""
        if fig_dir.exists():
            figs = sorted(fig_dir.iterdir())
            if figs:
                thumb_name = figs[0].name

        note_cats = note_categories_map.get(aid, [])
        cat_ids = (
            [c["id"] for c in note_cats]
            if isinstance(note_cats, list)
            else ([note_cats["id"]] if note_cats else [])
        )

        card_data.append(
            {
                "arxiv_id": aid,
                "title": p.get("title", ""),
                "url": p.get("url", f"https://arxiv.org/abs/{aid}"),
                "published": p.get("published", ""),
                "remark": p.get("llm_remark", "browse"),
                "score": p.get("llm_score", 0),
                "keyword": p.get("keyword_match", ""),
                "user_mark": p.get("user_mark", ""),
                "authors": (p.get("authors", "") or "")[:60],
                "short_title": (
                    p.get("note_short_title")
                    or p.get("title", "")[:50]
                    + ("..." if len(p.get("title", "")) > 50 else "")
                ),
                "short_desc": (
                    p.get("note_description")
                    or p.get("abstract", "")[:100]
                    + ("..." if len(p.get("abstract", "")) > 100 else "")
                ),
                "category_id": (
                    p.get("keyword_match", "").lower().replace(" ", "-")
                    if p.get("keyword_match")
                    else ""
                ),
                "cat_ids": cat_ids,
                "thumb_name": thumb_name,
            }
        )

    template = _env.get_template("notes_index.html")
    html = template.render(
        note_papers=card_data,
        deep_read_count=deep_read_count,
        skim_count=skim_count,
        lurk_count=lurk_count,
        ignore_count=ignore_count,
        pending_count=pending_count,
        total=len(note_papers),
        categories_list=categories_list,
        REMARK_LABELS=REMARK_LABELS,
        REMARK_COLORS=REMARK_COLORS,
    )

    filepath = notes_dir / "index.html"
    filepath.write_text(html, encoding="utf-8")
    return filepath


# ─── 笔记详情页 ────────────────────────────────────────────


def render_note_detail_html(
    arxiv_id: str,
    title: str,
    body_html: str,
    paper: dict | None = None,
) -> str:
    """渲染单篇笔记详情页面（左右分栏）。

    Args:
        arxiv_id: ArXiv ID
        title: 页面标题
        body_html: Markdown 渲染后的 HTML 内容
        paper: 论文信息（可选）

    Returns:
        HTML 字符串
    """
    mark_badge = ""
    short_title = ""
    short_desc = ""
    if paper:
        um = paper.get("user_mark", "")
        mark_badge = {"skim": "📖 粗读", "deep_read": "🔬 精读", "lurk": "⏳ 延后"}.get(
            um, ""
        )
        short_title = paper.get("note_short_title", "") or ""
        short_desc = paper.get("note_description", "") or ""

    template = _env.get_template("note_detail.html")
    return template.render(
        arxiv_id=arxiv_id,
        title=title or short_title or arxiv_id,
        body_html=body_html,
        short_title=short_title,
        short_desc=short_desc,
        mark_badge=mark_badge,
    )
