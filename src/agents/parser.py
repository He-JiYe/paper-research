"""ParserAgent：解析 PDF，提取图表、表格、正文内容

使用多模态模型（可配置）对提取的内容进行筛选和分类。

职责：
- 下载 PDF 一次
- 提取图片（嵌入图）和表格（结构化数据）
- 提取正文文本并识别章节段落
- 用 LLM 筛选：图片分类、表格筛选、段落识别
- 返回结构化数据供 NoteAgent 使用
"""

import asyncio
import re
from pathlib import Path

from src.agents.base import BaseAgent

# 最小图片大小（字节）
MIN_IMAGE_SIZE = 5000


FIGURE_FILTER_PROMPT = """You are analyzing a research paper's figures. Classify each extracted figure.

Paper title: {title}

Figures found (filename, approximate size):
{figures_list}

Respond in JSON format:
```json
{{
  "figures": [
    {{"index": 0, "category": "concept_diagram|result_figure|appendix|decorative", "reason": "brief reason"}}
  ]
}}
```

Categories:
- concept_diagram: Model architecture, framework overview, pipeline, algorithm illustration
- result_figure: Experimental results, charts, graphs, comparisons, ablation studies
- appendix: Figures in appendix/supplementary material
- decorative: Icons, logos, headers, decorative elements (exclude these)
"""

TABLE_FILTER_PROMPT = """You are analyzing a research paper's tables. Classify each extracted table.

Paper title: {title}

Tables found (page, size):
{tables_list}

Respond in JSON format:
```json
{{
  "tables": [
    {{"index": 0, "category": "important|experiment_result|appendix|small", "reason": "brief reason"}}
  ]
}}
```

Categories:
- important: Core method comparison, key architecture details
- experiment_result: Experimental results, benchmarks, ablation studies
- appendix: Supplementary tables
- small: Small auxiliary tables (can be excluded)
"""


# ─── PDF 同步提取函数 ─────────────────────────────────────


def _extract_figures_sync(pdf_path: Path, figures_dir: Path) -> list[dict]:
    """提取 PDF 中的嵌入图片，返回带元数据的列表。"""
    import fitz

    figures_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf_path))
    results = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        images = page.get_images(full=True)

        for img_idx, img in enumerate(images):
            xref = img[0]
            try:
                base_image = doc.extract_image(xref)
                img_bytes = base_image["image"]
                ext = base_image.get("ext", "png")

                if len(img_bytes) < MIN_IMAGE_SIZE:
                    continue

                filename = f"fig_{page_num + 1:03d}_{img_idx:02d}.{ext}"
                filepath = figures_dir / filename
                with open(filepath, "wb") as f:
                    f.write(img_bytes)

                # 尝试提取题注（从页面文本中找 Figure/Fig. 附近文字）
                caption = _find_caption(doc, page_num, xref, "figure")

                results.append(
                    {
                        "filename": f"figures/{filename}",
                        "page": page_num + 1,
                        "size_bytes": len(img_bytes),
                        "ext": ext,
                        "category": None,
                        "caption": caption,
                        "local_path": str(filepath),
                    }
                )
            except Exception as e:
                print(
                    f"      ⚠️ 图片提取失败 (page {page_num + 1}, img {img_idx}): {e}"
                )

    doc.close()
    print(f"    🖼️ 提取了 {len(results)} 张图片")
    return results


def _extract_tables_sync(pdf_path: Path, tables_dir: Path) -> list[dict]:
    """提取 PDF 中的表格，返回结构化数据。"""
    import fitz

    tables_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf_path))
    results = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        try:
            tables = page.find_tables()
            for t_idx, table in enumerate(tables):
                try:
                    df = table.to_pandas()
                    markdown = df.to_markdown()
                    filename = f"table_{page_num + 1:03d}_{t_idx:02d}.csv"
                    filepath = tables_dir / filename
                    df.to_csv(filepath, index=False)
                    caption = _find_caption(doc, page_num, None, "table")
                    results.append(
                        {
                            "filename": f"tables/{filename}",
                            "page": page_num + 1,
                            "rows": len(df),
                            "cols": len(df.columns),
                            "markdown": markdown[:2000],
                            "category": None,
                            "caption": caption,
                            "local_path": str(filepath),
                        }
                    )
                except Exception as e:
                    print(
                        f"      ⚠️ 表格转换失败 (page {page_num + 1}, tbl {t_idx}): {e}"
                    )
        except Exception as e:
            print(f"      ⚠️ 页面表格检测失败 (page {page_num + 1}): {e}")

    doc.close()
    if results:
        print(f"    📊 提取了 {len(results)} 个表格")
    return results


def _extract_text_sync(pdf_path: Path, max_pages: int = 30) -> dict:
    """提取 PDF 文本，返回按章节组织的结构化内容。"""
    import fitz

    doc = fitz.open(str(pdf_path))
    pages_text = []
    total_pages = min(len(doc), max_pages)

    for i in range(total_pages):
        text = doc[i].get_text()
        if len(text.strip()) > 100:
            pages_text.append({"page": i + 1, "text": text})

    doc.close()

    full_text = "\n\n".join(p["text"] for p in pages_text)

    # 章节识别
    section_patterns = [
        (
            r"(?i)^\s*(?:\d+\.?\s*|[IVX]+\.?\s*|[a-z]\.\s*|[一-鿿]+[、.])?\s*(introduction|background|preliminaries)\b.*$",
            "Introduction",
        ),
        (
            r"(?i)^\s*(?:\d+\.?\s*|[IVX]+\.?\s*|[a-z]\.\s*|[一-鿿]+[、.])?\s*(related\s*work|literature\s*review|prior\s*work)\b.*$",
            "Related Work",
        ),
        (
            r"(?i)^\s*(?:\d+\.?\s*|[IVX]+\.?\s*|[a-z]\.\s*|[一-鿿]+[、.])?\s*(method|methodology|approach|proposed|model|framework|system|algorithm|formulation)\b.*$",
            "Method",
        ),
        (
            r"(?i)^\s*(?:\d+\.?\s*|[IVX]+\.?\s*|[a-z]\.\s*|[一-鿿]+[、.])?\s*(experiment|evaluation|result|empirical|study|analysis|implementation|setup)\b.*$",
            "Experiments",
        ),
        (
            r"(?i)^\s*(?:\d+\.?\s*|[IVX]+\.?\s*|[a-z]\.\s*|[一-鿿]+[、.])?\s*(discussion|conclusion|summary|future\s*work|limitation|discussion\s*and\s*conclusion)\b.*$",
            "Conclusion",
        ),
    ]

    sections = {}
    lines = full_text.split("\n")
    current_section = "Abstract"
    section_texts = {current_section: []}

    for line in lines:
        for pattern, label in section_patterns:
            if re.match(pattern, line.strip()):
                current_section = label
                if current_section not in section_texts:
                    section_texts[current_section] = []
                break
        section_texts[current_section].append(line)

    for sec, sec_lines in section_texts.items():
        text = "\n".join(sec_lines).strip()
        if len(text) > 100:
            sections[sec] = text[:5000]  # 每个章节最多 5000 字符

    if not sections:
        sections["Full Text"] = full_text[:8000]

    print(f"    📝 提取了 {len(pages_text)} 页文本, {len(sections)} 个章节")
    return {
        "sections": sections,
        "full_text": full_text[:15000],
        "page_count": len(pages_text),
    }


def _find_caption(doc, page_num: int, xref, elem_type: str) -> str:
    """从页面文本中提取图片或表格的题注。"""
    page = doc[page_num]
    text = page.get_text()

    patterns = {
        "figure": [
            r"(?i)(Figure|Fig\.?)\s*\d+[\.:].*?(?=\n\n|\n\S)",
            r"(?i)(图\s*\d+[\.:：]).*?(?=\n\n|\n\S)",
        ],
        "table": [
            r"(?i)(Table|Tab\.?)\s*\d+[\.:].*?(?=\n\n|\n\S)",
            r"(?i)(表\s*\d+[\.:：]).*?(?=\n\n|\n\S)",
        ],
    }

    for pat in patterns.get(elem_type, []):
        import re

        match = re.search(pat, text)
        if match:
            caption = match.group(0).strip()
            # 截断过长题注
            if len(caption) > 300:
                caption = caption[:297] + "..."
            return caption
    return ""


class ParserAgent(BaseAgent):
    """PDF 解析 Agent。

    下载 PDF 后提取图表、表格和正文，再用 LLM 筛选分类。
    返回结构化数据供 NoteAgent 使用。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.max_tokens < 2000:
            self.max_tokens = 2000

    async def parse(self, arxiv_id: str, output_dir: Path) -> dict:
        """解析一篇论文的 PDF，返回结构化内容。

        Returns:
            {
                "figures": [{"filename", "page", "size_bytes", "category", ...}],
                "tables": [{"filename", "page", "rows", "cols", "markdown", "category", ...}],
                "text": {"sections": {...}, "full_text": str, "page_count": int},
                "paper": {"arxiv_id", "title", ...}  # 基本元信息
            }
        """
        from src.network.arxiv import download_pdf

        # 1. 下载 PDF（仅一次）
        pdf_path = await download_pdf(arxiv_id, output_dir)
        note_dir = output_dir / "notes" / arxiv_id
        figures_dir = note_dir / "figures"
        tables_dir = note_dir / "tables"

        loop = asyncio.get_running_loop()

        # 2. 并行提取图表、表格、文本
        figures, tables, text = await asyncio.gather(
            loop.run_in_executor(None, _extract_figures_sync, pdf_path, figures_dir),
            loop.run_in_executor(None, _extract_tables_sync, pdf_path, tables_dir),
            loop.run_in_executor(None, _extract_text_sync, pdf_path, 30),
        )

        # 3. 用 LLM 筛选图片
        if figures and self.api_key:
            figures = await self._filter_figures(figures, arxiv_id)

        # 4. 用 LLM 筛选表格
        if tables and self.api_key:
            tables = await self._filter_tables(tables, arxiv_id)

        return {
            "figures": figures,
            "tables": tables,
            "text": text,
        }

    async def _filter_figures(self, figures: list[dict], arxiv_id: str) -> list[dict]:
        """用 LLM 筛选和分类图片。"""
        lines = []
        for i, fig in enumerate(figures):
            size_kb = fig["size_bytes"] / 1024
            lines.append(
                f"  [{i}] Page {fig['page']}, {size_kb:.0f}KB, {fig['filename']}"
            )

        prompt = FIGURE_FILTER_PROMPT.format(
            title=f"arxiv:{arxiv_id}",
            figures_list="\n".join(lines),
        )
        content = self._call(
            system_prompt="You are a paper analysis assistant. Output valid JSON only.",
            user_prompt=prompt,
        )
        if content:
            result = self._extract_json(content)
            if result and "figures" in result:
                cat_map = {f["index"]: f for f in result["figures"]}
                for i, fig in enumerate(figures):
                    if i in cat_map:
                        fig["category"] = cat_map[i]["category"]

        # 过滤掉 decorative 类图片
        filtered = [f for f in figures if f.get("category") != "decorative"]
        if len(filtered) != len(figures):
            print(f"    🎯 LLM 筛选: {len(figures)}→{len(filtered)} 张图片")
        return filtered

    async def _filter_tables(self, tables: list[dict], arxiv_id: str) -> list[dict]:
        """用 LLM 筛选和分类表格。"""
        lines = []
        for i, t in enumerate(tables):
            lines.append(
                f"  [{i}] Page {t['page']}, {t['rows']}x{t['cols']}, content:\n{t['markdown'][:300]}"
            )

        prompt = TABLE_FILTER_PROMPT.format(
            title=f"arxiv:{arxiv_id}",
            tables_list="\n".join(lines),
        )
        content = self._call(
            system_prompt="You are a paper analysis assistant. Output valid JSON only.",
            user_prompt=prompt,
        )
        if content:
            result = self._extract_json(content)
            if result and "tables" in result:
                cat_map = {t["index"]: t for t in result["tables"]}
                for i, t in enumerate(tables):
                    if i in cat_map:
                        t["category"] = cat_map[i]["category"]

        # 过滤掉 small 类表格
        filtered = [t for t in tables if t.get("category") != "small"]
        if len(filtered) != len(tables):
            print(f"    🎯 LLM 筛选: {len(tables)}→{len(filtered)} 个表格")
        return filtered

    def _fallback(self, *args, **kwargs):
        return {
            "figures": [],
            "tables": [],
            "text": {"sections": {}, "full_text": "", "page_count": 0},
        }
