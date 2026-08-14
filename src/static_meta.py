"""前端静态元数据：app/app-meta.json 的读写（单一事实来源）。

- ``app/app-meta.json`` 是唯一事实来源（git 版本化、可手工编辑）；
- 代码默认值仅作首次 seed（``_default_app_meta``），读时不再与代码做"每键合并"双份存储；
"""

import json
from pathlib import Path

from src.paths import APP_DIR

APP_META_PATH = APP_DIR / "app-meta.json"

# 默认评级颜色（app-meta.json 缺失对应键时的兜底；与前端/邮件共享单一来源）
DEFAULT_REMARK_COLORS = {
    "important": "#e74c3c",
    "useful": "#f39c12",
    "browse": "#3498db",
    "skip": "#95a5a6",
    "pending": "#e67e22",
    "total": "#95a5a6",
}

# ─── 默认静态数据（仅首次 seed app-meta.json 使用）─────────────


def _default_app_meta() -> dict:
    return {
        "categories": [
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
        ],
        # 文案与已提交的 app/app-meta.json 保持一致（文件缺失/损坏时的回退须无感）
        "remark_labels": {
            "important": "⭐ 重要",
            "useful": "👍 关注",
            "browse": "📄 浏览",
            "skip": "🗑️ 跳过",
        },
        "remark_colors": dict(DEFAULT_REMARK_COLORS),  # 拷贝，防止调用方就地修改污染常量
        "section_labels": {
            "unmarked": "待审核",
            "marked": "已处理",
            "lurk": "延后处理",
        },
    }


# ─── app-meta 读写 ─────────────────────────────────────────


def load_app_meta(path: Path | None = None) -> dict:
    """加载前端静态元数据（app-meta.json）。

    Args:
        path: 覆盖默认路径（测试注入临时目录用）。

    Returns:
        文件内容 dict；文件缺失/损坏时回退默认值（不做每键合并）。
    """
    p = path or APP_META_PATH
    try:
        loaded = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            return loaded
    except (OSError, json.JSONDecodeError):
        pass
    return _default_app_meta()


def ensure_static_data(path: Path | None = None) -> Path:
    """确保 app-meta.json 存在（不存在时写入默认值 seed）。

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
