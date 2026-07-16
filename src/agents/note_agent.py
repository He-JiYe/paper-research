"""NoteAgent：基于解析内容生成笔记

职责：
- 全流程编排：从 arxiv_id 出发，解析 PDF → 生成笔记 → 短信息
- 接收 ParserAgent 解析的结构化内容生成笔记
- 根据模式（skim/deep_read）生成不同详略的笔记
- 输出标准 Markdown 笔记文件
"""

import datetime
from pathlib import Path

from src.agents.base import BaseAgent

# ─── Prompt 模板 ──────────────────────────────────────────

SKIM_NOTE_PROMPT = """You are writing a skim note for a research paper in Chinese.

Title: {title}

Paper content:
{sections_text}

Key Figures:
{figures_text}

Key Tables:
{tables_text}

Write a concise skim note with:
1. **核心要点** (3-5 bullet points covering the core contribution and method)
2. **主要结果** (1-2 sentences on the main experimental finding)
3. **关键图表** (mention 1-2 most important figures/tables)

Respond in JSON:
```json
{{
  "key_points": "3-5 bullet points in Chinese",
  "main_result": "1-2 sentences in Chinese",
  "key_visuals": "mention of important figures/tables"
}}
```
"""

DEEP_NOTE_PROMPT = """You are writing a detailed reading note for a research paper in Chinese.

Title: {title}
Abstract: {abstract}
Categories: {categories}

Paper content by section:
{sections_text}

Key Figures:
{figures_text}

Key Tables:
{tables_text}

Write a comprehensive deep-read note with:
1. **问题与背景**: What problem does this paper solve? What are the limitations of existing methods?
2. **方法与核心思路**: What is the proposed method? What are the key innovations?
3. **实验结果**: What datasets/tasks were used? What are the main results and findings?

Respond in JSON:
```json
{{
  "background": "2-3 sentences in Chinese",
  "method": "3-5 sentences in Chinese",
  "results": "2-3 sentences in Chinese"
}}
```
"""


class NoteAgent(BaseAgent):
    """笔记生成 Agent。

    基于 ParserAgent 解析的结构化内容，生成不同详略的笔记。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.max_tokens < 3000:
            self.max_tokens = 3000

    @classmethod
    async def generate_from_arxiv_id(
        cls,
        arxiv_id: str,
        output_dir: Path | None = None,
        mode: str = "deep_read",
    ) -> Path:
        """全流程：下载 PDF → 解析 → 生成笔记 → 短信息。

        Args:
            arxiv_id: ArXiv ID
            output_dir: 输出根目录
            mode: "deep_read" 或 "skim"

        Returns:
            笔记文件路径
        """
        from src.agents.parser import ParserAgent
        from src.agents.short_info import ShortInfoAgent
        from src.config import OUTPUT_DIR, get_db_path, load_settings
        from src.db import get_connection, get_paper, update_note_short_info

        if output_dir is None:
            output_dir = OUTPUT_DIR

        # 1. 获取论文数据
        db_path = get_db_path()
        conn = get_connection(db_path)
        paper = get_paper(conn, arxiv_id)
        conn.close()

        if not paper:
            raise ValueError(f"Paper not found: {arxiv_id}")

        settings = load_settings()

        # 2. 解析 PDF
        parser = ParserAgent.from_config(settings.llm)
        print(f"  [i] Parsing PDF for {arxiv_id}...")
        parsed_content = await parser.parse(arxiv_id, output_dir)

        # 3. 生成笔记
        agent = cls.from_config(settings.llm)
        note_path = await agent.generate_note(
            paper, parsed_content, output_dir, mode=mode
        )
        print(f"  [i] Note generated ({mode}): {note_path}")

        # 4. 生成短标题
        try:
            note_text = parsed_content.get("text", {}).get("full_text", "")
            short_agent = ShortInfoAgent.from_config(settings.llm)
            short_info = short_agent.generate(
                title=paper.get("title", ""),
                abstract=paper.get("abstract", ""),
                categories=paper.get("categories", ""),
                published=paper.get("published", ""),
                note_content=note_text[:2000] if note_text else "",
            )
            conn2 = get_connection(db_path)
            try:
                update_note_short_info(
                    conn2,
                    arxiv_id,
                    short_info["short_title"],
                    short_info["description"],
                )
                print(f"    [i] Short info: {short_info['short_title'][:50]}...")
            finally:
                conn2.close()
        except Exception as e:
            print(f"    [i] Short info skipped: {e}")

        return note_path

    async def generate_note(
        self,
        paper: dict,
        parsed_content: dict,
        output_dir: Path,
        mode: str = "deep_read",
    ) -> Path:
        """生成笔记。

        Args:
            paper: 论文元数据
            parsed_content: ParserAgent.parse() 的输出
            output_dir: 输出根目录
            mode: "deep_read" 或 "skim"

        Returns:
            笔记文件路径
        """
        paper["arxiv_id"]
        figures = parsed_content.get("figures", [])
        tables = parsed_content.get("tables", [])
        text = parsed_content.get("text", {})

        # 用 LLM 生成笔记内容
        note_content = self._generate_content(paper, text, figures, tables, mode)

        # 写 Markdown 文件
        note_path = self._write_note_md(
            paper, figures, tables, note_content, output_dir, mode
        )
        return note_path

    def _generate_content(
        self,
        paper: dict,
        text: dict,
        figures: list[dict],
        tables: list[dict],
        mode: str,
    ) -> dict:
        """用 LLM 生成笔记内容，失败时返回 fallback。"""
        title = paper.get("title", "")

        # 准备文本摘要
        sections = text.get("sections", {})
        sections_text = "\n\n".join(
            f"=== {sec} ===\n{content[:2000]}" for sec, content in sections.items()
        ) or (text.get("full_text", "")[:5000])

        # 准备图表摘要
        figures_text = (
            "\n".join(
                f"  - {f['filename']} (page {f['page']}, category: {f.get('category', 'unknown')})"
                for f in figures[:10]
            )
            or "(no figures)"
        )
        tables_text = (
            "\n".join(
                f"  - Page {t['page']}, {t['rows']}x{t['cols']}, category: {t.get('category', 'unknown')}"
                for t in tables[:10]
            )
            or "(no tables)"
        )

        if mode == "skim":
            prompt = SKIM_NOTE_PROMPT.format(
                title=title,
                sections_text=sections_text[:5000],
                figures_text=figures_text,
                tables_text=tables_text,
            )
            system = "You are a research paper note-taker. Output valid JSON."
        else:
            prompt = DEEP_NOTE_PROMPT.format(
                title=title,
                abstract=(paper.get("abstract", "") or "")[:1000],
                categories=paper.get("categories", "") or "unspecified",
                sections_text=sections_text[:6000],
                figures_text=figures_text,
                tables_text=tables_text,
            )
            system = "You are a research paper analysis assistant. Output valid JSON."

        if self.api_key:
            content = self._call(system_prompt=system, user_prompt=prompt)
            if content:
                result = self._extract_json(content)
                if result:
                    return result

        return self._fallback(text, mode)

    def _fallback(self, text: dict, mode: str) -> dict:
        """无 LLM 时的回退。"""
        full = text.get("full_text", "")
        sections = text.get("sections", {})
        intro = sections.get("Introduction", sections.get("Abstract", full))[:800]
        method = sections.get("Method", sections.get("Approach", ""))[:800]
        results = sections.get("Experiments", sections.get("Results", ""))[:800]

        if mode == "deep_read":
            return {
                "background": intro[:500],
                "method": method[:500],
                "results": results[:500],
            }
        else:
            return {
                "key_points": f"- {intro[:300]}",
                "main_result": results[:300],
                "key_visuals": "",
            }

    def _write_note_md(
        self,
        paper: dict,
        figures: list[dict],
        tables: list[dict],
        note_content: dict,
        output_dir: Path,
        mode: str,
    ) -> Path:
        """将笔记内容写入 Markdown 文件。"""
        arxiv_id = paper["arxiv_id"]
        note_dir = output_dir / "notes" / arxiv_id
        note_dir.mkdir(parents=True, exist_ok=True)

        version_str = f"v{paper.get('version', 1)}"
        mark_label = {
            "skim": "📖 粗读",
            "deep_read": "🔬 精读",
        }.get(paper.get("user_mark", ""), paper.get("user_mark", mode))
        today = datetime.date.today().isoformat()

        is_deep = mode == "deep_read"

        # — 辅助函数：生成图表和表格的 Markdown —
        def _fig_md(fig: dict, idx: int) -> str:
            fig_name = Path(fig["filename"]).name
            caption = fig.get("caption", "") or ""
            cap_text = f" — {caption}" if caption else " — 请在此处添加你的解读"
            return f"![图表{idx}](figures/{fig_name})\n*（图表{idx}{cap_text}）*\n\n"

        def _table_md(t: dict, idx: int) -> str:
            caption = t.get("caption", "") or ""
            cap_line = f"\n*（表格{idx}: {caption}）*\n" if caption else ""
            md_content = t.get("markdown", "")
            if len(md_content) > 1500:
                md_content = md_content[:1500] + "\n..."
            return f"{cap_line}\n{md_content}\n\n"

        relevant_figs = [f for f in figures if f.get("category") != "decorative"]
        relevant_tables = [
            t for t in tables if t.get("category") not in ("small", None)
        ]

        # — 按类别分类图表 —
        method_figs = [
            f for f in relevant_figs if f.get("category") in ("concept_diagram", None)
        ]
        result_figs = [
            f for f in relevant_figs if f.get("category") in ("result_figure",)
        ]
        appendix_figs = [f for f in relevant_figs if f.get("category") in ("appendix",)]
        other_figs = [
            f
            for f in relevant_figs
            if f not in method_figs + result_figs + appendix_figs
        ]

        # — 构建正文 —
        if is_deep:
            bg = (
                note_content.get("background", "")
                or "\n<!-- Fill in background -->\n\n"
            )
            md = note_content.get("method", "") or "\n<!-- Fill in method -->\n\n"
            rs = note_content.get("results", "") or "\n<!-- Fill in results -->\n\n"

            # 方法图插入方法区域
            for i, fig in enumerate(method_figs[:4], 1):
                md += "\n" + _fig_md(fig, i)

            # 结果图 + 表格插入结果区域
            for i, fig in enumerate(result_figs[:4], 1):
                rs += "\n" + _fig_md(fig, i + len(method_figs))
            for ti, t in enumerate(relevant_tables[:6], 1):
                rs += "\n" + _table_md(t, ti)

            body = f"""## 问题与背景

{bg}
## 方法与核心思路

{md}
## 实验结果

{rs}
"""
        else:
            kp = (
                note_content.get("key_points", "")
                or "\n<!-- Fill in key points -->\n\n"
            )
            mr = note_content.get("main_result", "") or ""
            for i, fig in enumerate(relevant_figs[:6], 1):
                mr += "\n" + _fig_md(fig, i)
            for ti, t in enumerate(relevant_tables[:3], 1):
                mr += "\n" + _table_md(t, ti)
            body = f"""## 核心要点

{kp}
## 主要结果

{mr}
"""

        # — 附录图表 —
        for i, fig in enumerate(appendix_figs + other_figs, 1):
            body += "\n" + _fig_md(fig, i)

        content = f"""# 阅读笔记: {paper.get("title", "")}

## 基本信息

- **ArXiv ID / 版本**: {arxiv_id}{version_str}
- **作者**: {paper.get("authors", "")}
- **URL**: [{arxiv_id}]({paper.get("url", f"https://arxiv.org/abs/{arxiv_id}")})
- **发表时间**: {paper.get("published", "")}
- **分类**: {paper.get("categories", "")}
- **匹配关键词**: {paper.get("keyword_match", "")}
- **阅读状态**: {mark_label}
- **标记日期**: {today}

## AI 初筛意见

- **评级**: {paper.get("llm_remark", "")}
- **理由**: {paper.get("llm_reason", "")}
- **相关度评分**: {paper.get("llm_score", 0):.2f}
- **AI 摘要**: {paper.get("llm_summary", "")}

---

{body}
## 个人思考

<!-- 对本文的评价、可借鉴的点、与其他工作的关联 -->


## 相关论文追踪

<!-- 格式: [arxiv_id] 标题 — 为什么值得跟进 -->
"""
        note_path = note_dir / "note.md"
        with open(note_path, "w", encoding="utf-8") as f:
            f.write(content)
        return note_path
