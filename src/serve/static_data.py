"""静态元数据 + 短标题建议：前端 JSON 化数据的唯一事实来源

- 原硬编码于 renderer.py / _fetch_modal.html 的常量（arxiv 分类、评级标签/颜色、
  分区标题）下沉为默认值。
- `data/static/app-meta.json` 是运行时覆盖层（git 版本化，可直接手工编辑）；
  缺失/损坏/缺键时回退本模块默认值，并由 `ensure_static_data()` 在服务启动时落盘。

动静分离：本模块对应"静态数据"；动态数据（论文）存 SQLite，经 /api/papers 返回。
"""

import json
import re
from pathlib import Path

from src.config import DATA_DIR

APP_META_PATH = DATA_DIR / "static" / "app-meta.json"

# ─── 默认静态数据（原 renderer.py:20-38 + _fetch_modal.html:17-27） ──────

DEFAULT_ARXIV_CATS = [
    "cs.AI",
    "cs.CL",
    "cs.CV",
    "cs.LG",
    "cs.NE",
    "cs.IR",
    "cs.SE",
    "cs.PL",
    "cs.CR",
    "stat.ML",
]

DEFAULT_REMARK_LABELS = {
    "important": "⭐ 重要",
    "useful": "👍 值得关注",
    "browse": "📄 可浏览",
    "skip": "🗑️ 跳过",
}

DEFAULT_REMARK_COLORS = {
    "important": "#e74c3c",
    "useful": "#f39c12",
    "browse": "#3498db",
    "skip": "#95a5a6",
}

DEFAULT_SECTION_LABELS = {
    "unmarked": "待审核",
    "marked": "已处理",
    "lurk": "延后处理",
}


def _default_app_meta() -> dict:
    """默认元数据（app-meta.json 的文件形态）"""
    return {
        "arxiv_cats": DEFAULT_ARXIV_CATS,
        "remark_labels": DEFAULT_REMARK_LABELS,
        "remark_colors": DEFAULT_REMARK_COLORS,
        "section_labels": DEFAULT_SECTION_LABELS,
    }


# ─── 短标题建议（原 renderer._suggest_short_title） ────────────────────


def suggest_short_title(paper: dict) -> str:
    """为论文建议一个短标题（未读-关键词-年份-缩写）。

    Args:
        paper: 论文 dict（需含 published / keyword_match / title）

    Returns:
        建议短标题字符串
    """
    year = (paper.get("published", "") or "")[:4]
    if not year or not year.isdigit():
        year = "????"
    kw = paper.get("keyword_match", "") or "Unknown"
    title = paper.get("title", "") or ""

    # 提取缩写：取前两个非停用词的首字母 + 最后一个词的前几个字符
    stop_words = {
        "a", "an", "the", "for", "of", "in", "on", "at", "to",
        "by", "and", "or", "is", "with",
    }
    words = re.sub(r"[^\w\s-]", "", title).split()
    content_words = [w for w in words if w.lower() not in stop_words and len(w) > 1]

    if len(content_words) >= 3:
        abbrev = content_words[0][:4] + content_words[-1][:6]
    elif content_words:
        abbrev = "".join(w[:4] for w in content_words[:3])
    else:
        abbrev = words[0][:8] if words else "Paper"

    return f"未读-{kw}-{year}-{abbrev[:30]}"


# ─── app-meta 读写 ─────────────────────────────────────────────────────


def load_app_meta(path: Path | None = None) -> dict:
    """加载前端静态元数据。

    Args:
        path: 覆盖默认路径（测试注入临时目录用）。

    Returns:
        合并后的元数据 dict：文件缺失/损坏/缺键时用默认值补齐。
    """
    meta = _default_app_meta()
    p = path or APP_META_PATH
    try:
        loaded = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            for key in meta:
                if key in loaded and loaded[key] is not None:
                    meta[key] = loaded[key]
    except (OSError, json.JSONDecodeError):
        pass
    return meta


def ensure_static_data(path: Path | None = None) -> Path:
    """确保 app-meta.json 存在（不存在时写入默认值）。

    Args:
        path: 覆盖默认路径（测试注入临时目录用）。

    Returns:
        最终的 app-meta.json 路径。
    """
    p = path or APP_META_PATH
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(_default_app_meta(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return p
