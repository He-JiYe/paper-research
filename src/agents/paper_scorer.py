"""PaperScorer Agent：论文初筛 — 对标题+摘要进行 LLM 评级和评分"""

import asyncio
from dataclasses import dataclass

from src.agents.base import BaseAgent


@dataclass
class LLMResult:
    """LLM 输出结果"""

    summary: str
    remark: str  # important / useful / browse / skip
    reason: str
    score: float


SUMMARIZE_PROMPT = """你是一位计算机科学博士生,正在筛选 Arxiv 预印本论文.请根据以下论文的标题和摘要,给出你的判断.

标题:{title}
摘要:{abstract}
分类:{categories}

请用中文回复,严格遵循以下 JSON 格式,不要添加任何其他文字:
```json
{{
  "summary": "用2-3句中文概括本文的核心技术贡献和方法",
  "remark": "从以下4个等级中选择一个",
  "reason": "用1句话说明选择该等级的理由",
  "score": 0.0到1.0之间的相关性评分
}}
```

评级标准:
- "important": 范式突破,理论创新,或可能产生重大影响的 work
- "useful": 有实用价值,solid engineering,可复用的 trick 或方法
- "browse": 有一定参考价值但非核心关注方向,可快速浏览
- "skip": 增量式工作,无明显贡献,或与研究方向无关

评分标准:
- 0.8-1.0: 高度相关,直接命中研究方向
- 0.6-0.8: 有一定相关性,值得了解
- 0.4-0.6: 弱相关,背景参考价值
- 0.0-0.4: 基本不相关
"""


class PaperScorer(BaseAgent):
    """论文初筛 Agent。

    对论文标题+摘要进行 LLM 评级（important/useful/browse/skip）
    和相关性评分（0-1），并生成中文摘要。
    """

    def score(
        self,
        title: str,
        abstract: str,
        categories: str = "",
    ) -> LLMResult | None:
        """单篇论文评分（同步）。

        Returns:
            LLMResult 或 None（失败时）
        """
        if not self.api_key:
            return self._fallback(title, abstract)

        prompt = SUMMARIZE_PROMPT.format(
            title=title,
            abstract=abstract[:2000],
            categories=categories or "未指定",
        )
        content = self._call(
            system_prompt="你是一个专业的学术论文审阅助手.请始终以有效的 JSON 格式回复.",
            user_prompt=prompt,
        )
        if content:
            result = self._extract_json(content)
            if result:
                return LLMResult(
                    summary=result.get("summary", abstract[:200] + "..."),
                    remark=result.get("remark", "browse"),
                    reason=result.get("reason", ""),
                    score=max(0.0, min(1.0, float(result.get("score", 0.5)))),
                )
        return self._fallback(title, abstract)

    async def score_async(
        self,
        title: str,
        abstract: str,
        categories: str = "",
    ) -> LLMResult | None:
        """单篇论文评分（异步）。"""
        if not self.api_key:
            return self._fallback(title, abstract)

        prompt = SUMMARIZE_PROMPT.format(
            title=title,
            abstract=abstract[:2000],
            categories=categories or "未指定",
        )
        content = await self._call_async(
            system_prompt="You are an academic paper review assistant. Always respond in valid JSON format.",
            user_prompt=prompt,
        )
        if content:
            result = self._extract_json(content)
            if result:
                return LLMResult(
                    summary=result.get("summary", abstract[:200] + "..."),
                    remark=result.get("remark", "browse"),
                    reason=result.get("reason", ""),
                    score=max(0.0, min(1.0, float(result.get("score", 0.5)))),
                )
        return self._fallback(title, abstract)

    def score_batch(self, papers: list[dict]) -> list[LLMResult | None]:
        """批量评分（同步串行）。"""
        results = []
        for i, paper in enumerate(papers):
            title = paper.get("title", "")
            abstract = paper.get("abstract", "")
            categories = paper.get("categories", "")
            if len(papers) > 1:
                print(f"    [{i + 1}/{len(papers)}] {title[:60]}...")
            results.append(self.score(title, abstract, categories))
        return results

    async def score_batch_async(
        self,
        papers: list[dict],
        max_concurrent: int = 3,
    ) -> list[LLMResult | None]:
        """批量评分（异步并发，Semaphore 控制并发数）。"""
        if not papers:
            return []

        sem = asyncio.Semaphore(max_concurrent)

        async def _score_one(paper: dict, idx: int) -> LLMResult | None:
            async with sem:
                title = paper.get("title", "")
                abstract = paper.get("abstract", "")
                categories = paper.get("categories", "")
                if len(papers) > 1:
                    print(f"    [{idx + 1}/{len(papers)}] {title[:60]}...")
                return await self.score_async(title, abstract, categories)

        tasks = [_score_one(p, i) for i, p in enumerate(papers)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [
            (
                r
                if isinstance(r, LLMResult)
                else self._fallback(
                    papers[i].get("title", ""), papers[i].get("abstract", "")
                )
            )
            for i, r in enumerate(results)
        ]

    def _fallback(self, title: str, abstract: str) -> LLMResult:
        """无 API Key 时的规则 fallback（基于关键词匹配）。"""
        # 摘要截取前 300 字
        summary = abstract[:300] + "..." if len(abstract) > 300 else abstract

        high_keywords = [
            "novel",
            "state-of-the-art",
            "breakthrough",
            "theoretical",
            "paradigm",
            "framework",
            "fundamental",
            "first",
        ]
        useful_keywords = [
            "effective",
            "efficient",
            "improved",
            "robust",
            "practical",
            "scalable",
            "real-world",
            "empirical",
        ]

        text_lower = (title + " " + abstract).lower()
        high_count = sum(1 for kw in high_keywords if kw in text_lower)
        useful_count = sum(1 for kw in useful_keywords if kw in text_lower)

        if high_count >= 2:
            remark = "useful"
            score = 0.7
            reason = "基于关键词匹配判断有较高参考价值"
        elif useful_count >= 2:
            remark = "browse"
            score = 0.5
            reason = "关键词匹配判断有一定参考价值"
        else:
            remark = "browse"
            score = 0.4
            reason = "规则 fallback,建议人工判断"

        return LLMResult(summary=summary, remark=remark, reason=reason, score=score)
