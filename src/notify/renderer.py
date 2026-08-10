"""邮件内容生成：把统计数据/论文/关键词渲染为邮件 HTML、纯文本与主题。

纯函数模块，不含任何 SMTP 发送逻辑——发送由 ``sender.EmailNotifier`` 负责。

模板与静态数据（评级颜色）统一从顶层 ``app/`` 目录读取，与前端资源同源：
评级颜色来自 ``app/app-meta.json`` 的 ``remark_colors``（经 ``load_app_meta()``），
保证邮件与前端评级颜色始终一致。
"""

import datetime
import html
from dataclasses import dataclass
from functools import cache

from src.paths import APP_DIR
from src.static_meta import DEFAULT_REMARK_COLORS, load_app_meta

EMAIL_TEMPLATE_DIR = APP_DIR / "email"

_TITLE_MAX = 80  # 邮件论文标题显示截断长度


@dataclass(frozen=True)
class EmailContent:
    """邮件内容：HTML 正文 + 纯文本正文 + 主题。"""

    html: str
    text: str
    subject: str


@cache
def _load_template(name: str) -> str:
    """读取邮件模板（带缓存，避免每次发送重复读盘）。"""
    return (EMAIL_TEMPLATE_DIR / name).read_text(encoding="utf-8")


def _render(template_name: str, **kwargs: str) -> str:
    """用 ``str.format`` 渲染模板（``{name}`` 占位符）。"""
    return _load_template(template_name).format(**kwargs)


def _escape(s) -> str:
    """HTML 转义不可信内容（论文标题/URL/评级标签等），防邮件注入。"""
    return html.escape("" if s is None else str(s), quote=True)


def _render_papers_table(top: list[dict], colors: dict[str, str]) -> str:
    """渲染 Top N 论文表格；无论文时返回空串（正文中该片段自动隐藏）。"""
    if not top:
        return ""
    rows = "\n".join(_render_paper_row(p, colors) for p in top)
    return _render("papers_table.html", rows=rows, top_n=len(top))


def _fmt_score(score) -> str:
    """把 llm_score 安全格式化为两位小数（历史数据可能为 None/字符串）。"""
    try:
        return f"{float(score):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _render_paper_row(p: dict, colors: dict[str, str]) -> str:
    remark = p.get("llm_remark", "")
    return _render(
        "paper_row.html",
        url=_escape(p.get("url", "#")),
        title=_escape(p.get("title", "")[:_TITLE_MAX]),  # 先截断再转义，避免切断 HTML 实体
        remark=_escape(remark),
        color=_escape(colors.get(remark, DEFAULT_REMARK_COLORS["skip"])),
        score=_fmt_score(p.get("llm_score")),
    )


def render_email_report(
    stats: dict,
    new_papers: list[dict],
    keywords: list[str],
    server_url: str,
    top_n: int = 3,
) -> EmailContent:
    """生成报告邮件内容（HTML + 纯文本 + 主题），不含任何发送动作。

    Args:
        stats: 统计信息
        new_papers: 新论文列表（取 Top n）
        keywords: 关键词列表
        server_url: Web 审阅服务地址
        top_n: 展示前 n 条

    Returns:
        EmailContent（html / text / subject）
    """
    today = datetime.date.today().isoformat()
    run_time = datetime.datetime.now().strftime("%H:%M")

    total = stats.get("total", 0)
    pending = stats.get("pending", 0)
    by_remark = stats.get("by_remark", {})
    by_remark = by_remark if isinstance(by_remark, dict) else {}
    important = by_remark.get("important", 0)
    useful = by_remark.get("useful", 0)

    # 评级标签/颜色单一来源：app/app-meta.json（与前端共享；整封邮件只读一次）
    meta = load_app_meta()
    remark_labels = meta.get("remark_labels", {})
    remark_colors = meta.get("remark_colors", {})
    section_labels = meta.get("section_labels", {})
    labels = {
        "important": remark_labels.get("important", "重要"),
        "useful": remark_labels.get("useful", "值得关注"),
        "pending": section_labels.get("unmarked", "待审核"),
        "total": "总计",
    }

    # Top N 论文（按 llm_score 降序；历史数据 llm_score 可能为 None，用 0 兜底避免排序崩溃）
    top = sorted(new_papers, key=lambda p: p.get("llm_score") or 0, reverse=True)[:top_n]

    keywords_str = ", ".join(keywords) if keywords else "未指定"

    html = _render(
        "email_report.html",
        today=today,
        run_time=run_time,
        important=important,
        useful=useful,
        pending=pending,
        total=total,
        important_color=_escape(remark_colors.get("important", DEFAULT_REMARK_COLORS["important"])),
        useful_color=_escape(remark_colors.get("useful", DEFAULT_REMARK_COLORS["useful"])),
        pending_color=_escape(remark_colors.get("pending", DEFAULT_REMARK_COLORS["pending"])),
        total_color=_escape(remark_colors.get("total", DEFAULT_REMARK_COLORS["total"])),
        important_label=_escape(labels["important"]),
        useful_label=_escape(labels["useful"]),
        pending_label=_escape(labels["pending"]),
        total_label=_escape(labels["total"]),
        papers_table=_render_papers_table(top, remark_colors),
        server_url=_escape(server_url),
        keywords_str=_escape(keywords_str),
    )

    text = _render(
        "email_report.txt",
        today=today,
        keywords_str=keywords_str,
        important=important,
        useful=useful,
        pending=pending,
        total=total,
        important_label=labels["important"],
        useful_label=labels["useful"],
        pending_label=labels["pending"],
        total_label=labels["total"],
        server_url=server_url,
    )

    return EmailContent(
        html=html,
        text=text,
        subject=f"Paper Research - {today} [{keywords_str}]",
    )
