"""ShortInfoAgent：基于笔记和正文生成短标题和简介"""

from src.agents.base import BaseAgent

SHORT_INFO_PROMPT = """You are reading an academic paper and need to create a concise entry for your notes gallery.

Title: {title}
Abstract: {abstract}
Categories: {categories}
Published: {published}

Key content from the paper:
{note_content}

Generate:
1. A short title in the format "年份-方法缩写", e.g., "2018-Transformer", "2020-CLIP", "2022-Llama". Extract the year from Published and use an abbreviation of the core method/contribution.
2. A brief description (1-2 sentences) explaining why this paper is worth noting

Respond in JSON format only:
```json
{{
  "short_title": "年份-方法缩写, e.g. 2018-Transformer",
  "description": "1-2 sentence brief description here"
}}
```
"""


class ShortInfoAgent(BaseAgent):
    """短信息 Agent：基于笔记和全文内容生成短标题和简介。"""

    def generate(
        self,
        title: str,
        abstract: str,
        categories: str = "",
        published: str = "",
        note_content: str = "",
    ) -> dict:
        """生成短标题和简要说明。"""
        fallback = self._fallback(title, abstract, published)

        if not self.api_key:
            return fallback

        prompt = SHORT_INFO_PROMPT.format(
            title=title,
            abstract=abstract[:1500],
            categories=categories or "unspecified",
            published=published[:4] if published else "unknown",
            note_content=(
                note_content[:2000] if note_content else "(no content available)"
            ),
        )
        content = self._call(
            system_prompt="You are an academic paper note-taker. Always respond in valid JSON.",
            user_prompt=prompt,
        )
        if content:
            result = self._extract_json(content)
            if result:
                st = result.get("short_title", "").strip()
                desc = result.get("description", "").strip()
                if st or desc:
                    return {"short_title": st, "description": desc}
        return fallback

    @staticmethod
    def _fallback(title: str, abstract: str, published: str = "") -> dict:
        """无 LLM 时的 fallback。"""
        year = published[:4] if len(published) >= 4 else ""
        words = title.split()
        abbrev = " ".join(words[:2])
        short_title = f"{year}-{abbrev}" if year else abbrev
        clean = abstract.strip()
        desc = clean[:147] + "..." if len(clean) > 150 else clean
        return {"short_title": short_title, "description": desc}
