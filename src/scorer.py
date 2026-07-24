"""PaperScorer：论文初筛 — 对标题+摘要进行 LLM 评级和评分"""

import asyncio
import json
import os
import re
from dataclasses import dataclass

from openai import AsyncOpenAI, OpenAI


@dataclass
class LLMResult:
    """LLM 输出结果"""

    summary: str
    remark: str  # important / useful / browse / skip
    reason: str
    score: float
    score_distribution: dict[str, float] | None = None  # 概率分布 {0: p0, 1: p1, ..., 5: p5}


SUMMARIZE_PROMPT = """你是一位计算机科学博士生,正在筛选 Arxiv 预印本论文.请根据以下论文的标题和摘要,给出你的判断.

搜索关键词:{keyword}
标题:{title}
摘要:{abstract}
分类:{categories}

请用中文回复,严格遵循以下 JSON 格式,不要添加任何其他文字:
```json
{{
  "summary": "用2-3句中文概括本文的核心技术贡献和方法",
  "remark": "从以下4个等级中选择一个",
  "reason": "用1句话说明选择该等级的理由",
  "score_distribution": {{"0": 概率0, "1": 概率1, "2": 概率2, "3": 概率3, "4": 概率4, "5": 概率5}}
}}
```

评级标准:
- "important": 范式突破,理论创新,或可能产生重大影响的 work
- "useful": 有实用价值,solid engineering,可复用的 trick 或方法
- "browse": 有一定参考价值但非核心关注方向,可快速浏览
- "skip": 增量式工作,无明显贡献,或与研究方向无关

评分标准（请结合「搜索关键词」判断相关性,输出各分数的概率分布）:
- 5分: 高度相关,标题/摘要直接命中搜索关键词,是该方向核心工作
- 4分: 有一定相关性,涉及关键词方向但不完全聚焦
- 3分: 弱相关,有一定背景参考价值
- 2分: 非常弱的相关性,仅边缘触及
- 1分: 基本不相关,与搜索关键词方向偏离较远
- 0分: 完全不相关,与研究方向无关

请为每个分数给出合理的概率(0.0-1.0),所有概率之和应为1.0。score_distribution 的 key 必须为字符串形式的 "0"、"1"、"2"、"3"、"4"、"5"。最终得分由系统根据概率分布的期望值自动计算,请同时填写 score 字段。
"""


def compute_keyword_relevance(title: str, abstract: str, keyword: str) -> float:
    """计算论文标题/摘要与搜索关键词的相关性得分 (0-1)。

    纯文本处理，无 API 调用。用于预排序 fallback 和 _fallback 评分。
    标题匹配的权重是摘要的 2 倍。
    """
    if not keyword:
        return 0.5

    def _tokenize(text: str) -> list[str]:
        return re.findall(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", text.lower())

    kw_tokens = set(_tokenize(keyword))
    if not kw_tokens:
        return 0.5

    title_tokens = _tokenize(title)
    abstract_tokens = _tokenize(abstract)

    title_matches = sum(1 for t in kw_tokens if t in title_tokens)
    abstract_matches = sum(1 for t in kw_tokens if t in abstract_tokens)

    n = len(kw_tokens)
    # 标题匹配权重 ×2，摘要匹配权重 ×1
    effective = title_matches * 2 + abstract_matches
    max_effective = n * 3  # 全部命中 title(2) + abstract(1)

    if max_effective == 0:
        return 0.5

    ratio = effective / max_effective
    # 映射到 [0.10, 0.95]：ratio=0 → 0.10, ratio=1 → 0.95
    score = 0.10 + ratio * 0.85
    return round(min(0.95, score), 2)


class PaperScorer:
    """论文初筛评分器。

    对论文标题+摘要进行 LLM 评级（important/useful/browse/skip）
    和相关性评分（0-1），并生成中文摘要。

    不再继承 BaseAgent，所有功能内联实现。
    """

    def __init__(
        self,
        api_key: str = "",
        api_base: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
        temperature: float = 0.3,
        max_tokens: int = 2000,
    ):
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.api_base = api_base
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def _call(self, system_prompt: str, user_prompt: str) -> str | None:
        """同步调用 LLM API。"""
        if not self.api_key:
            return None
        try:
            client = OpenAI(api_key=self.api_key, base_url=self.api_base)
            resp = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            return resp.choices[0].message.content
        except Exception as e:
            print(f"  [!] LLM API call failed: {e}")
            return None

    async def _call_async(self, system_prompt: str, user_prompt: str) -> str | None:
        """异步调用 LLM API。"""
        if not self.api_key:
            return None
        try:
            client = AsyncOpenAI(api_key=self.api_key, base_url=self.api_base)
            resp = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            return resp.choices[0].message.content
        except Exception as e:
            print(f"  [!] LLM API call failed: {e}")
            return None

    @staticmethod
    def _compute_score_from_distribution(
        dist: dict, fallback_score: float = 0.5
    ) -> tuple[float, dict[str, float] | None]:
        """从概率分布计算期望得分（归一化到 0~1）。

        期望 = Σ(p_i * i) / 5, 其中 i ∈ {0,1,2,3,4,5}

        Returns:
            (score_0_1, normalized_distribution) 或 (fallback_score, None)
        """
        try:
            if not isinstance(dist, dict) or len(dist) == 0:
                return fallback_score, None

            probs: dict[str, float] = {}
            total_prob = 0.0
            expected = 0.0

            for k, v in dist.items():
                key = str(k)
                prob = float(v)
                if prob < 0:
                    return fallback_score, None
                probs[key] = prob
                total_prob += prob
                score_val = float(key)
                expected += score_val * prob

            # 概率和应接近 1.0，允许小误差
            if total_prob < 0.9 or total_prob > 1.1:
                # 归一化
                if total_prob > 0:
                    expected /= total_prob
                    probs = {k: v / total_prob for k, v in probs.items()}

            score_0_1 = max(0.0, min(1.0, expected / 5.0))
            return score_0_1, probs
        except (ValueError, TypeError, KeyError):
            return fallback_score, None

    @staticmethod
    def _extract_json(content: str) -> dict | None:
        """从 LLM 响应中提取 JSON 对象。

        支持纯 JSON 和 ```json ... ``` 包裹两种格式。
        """
        if not content:
            return None
        # 尝试从 markdown 代码块中提取
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
        if match:
            content = match.group(1).strip()
        # 尝试直接解析
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        # 尝试从内容中查找第一个 { 到最后一个 }
        try:
            start = content.index("{")
            end = content.rindex("}")
            return json.loads(content[start : end + 1])
        except (ValueError, json.JSONDecodeError):
            return None

    def score(
        self,
        title: str,
        abstract: str,
        categories: str = "",
        keyword: str = "",
    ) -> LLMResult | None:
        """单篇论文评分（同步）。

        Args:
            title: 论文标题
            abstract: 论文摘要
            categories: 论文分类
            keyword: 搜索关键词（用于相关性判断）

        Returns:
            LLMResult 或 None（失败时）
        """
        if not self.api_key:
            return self._fallback(title, abstract, keyword)

        prompt = SUMMARIZE_PROMPT.format(
            title=title,
            abstract=abstract[:2000],
            categories=categories or "未指定",
            keyword=keyword or "未指定",
        )
        content = self._call(
            system_prompt="You are an academic paper review assistant. Always respond in valid JSON format.",
            user_prompt=prompt,
        )
        if content:
            result = self._extract_json(content)
            if result:
                score, dist = self._compute_score_from_distribution(
                    result.get("score_distribution", {}),
                    fallback_score=max(0.0, min(1.0, float(result.get("score", 0.5)))),
                )
                return LLMResult(
                    summary=result.get("summary", abstract[:200] + "..."),
                    remark=result.get("remark", "browse"),
                    reason=result.get("reason", ""),
                    score=score,
                    score_distribution=dist,
                )
        return self._fallback(title, abstract, keyword)

    async def score_async(
        self,
        title: str,
        abstract: str,
        categories: str = "",
        keyword: str = "",
    ) -> LLMResult | None:
        """单篇论文评分（异步）。

        Args:
            title: 论文标题
            abstract: 论文摘要
            categories: 论文分类
            keyword: 搜索关键词（用于相关性判断）

        Returns:
            LLMResult 或 None（失败时）
        """
        if not self.api_key:
            return self._fallback(title, abstract, keyword)

        prompt = SUMMARIZE_PROMPT.format(
            title=title,
            abstract=abstract[:2000],
            categories=categories or "未指定",
            keyword=keyword or "未指定",
        )
        content = await self._call_async(
            system_prompt="You are an academic paper review assistant. Always respond in valid JSON format.",
            user_prompt=prompt,
        )
        if content:
            result = self._extract_json(content)
            if result:
                score, dist = self._compute_score_from_distribution(
                    result.get("score_distribution", {}),
                    fallback_score=max(0.0, min(1.0, float(result.get("score", 0.5)))),
                )
                return LLMResult(
                    summary=result.get("summary", abstract[:200] + "..."),
                    remark=result.get("remark", "browse"),
                    reason=result.get("reason", ""),
                    score=score,
                    score_distribution=dist,
                )
        return self._fallback(title, abstract, keyword)

    def score_batch(self, papers: list[dict]) -> list[LLMResult | None]:
        """批量评分（同步串行）。"""
        results = []
        for i, paper in enumerate(papers):
            title = paper.get("title", "")
            abstract = paper.get("abstract", "")
            categories = paper.get("categories", "")
            keyword = paper.get("keyword_match", "")
            if len(papers) > 1:
                print(f"    [{i + 1}/{len(papers)}] {title[:60]}...")
            results.append(self.score(title, abstract, categories, keyword))
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
                keyword = paper.get("keyword_match", "")
                if len(papers) > 1:
                    print(f"    [{idx + 1}/{len(papers)}] {title[:60]}...")
                return await self.score_async(title, abstract, categories, keyword)

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

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """分词：提取字母数字词，转小写"""
        return re.findall(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", text.lower())

    def _fallback(self, title: str, abstract: str, keyword: str = "") -> LLMResult:
        """无 API Key 时的关键词加权评分 fallback。

        优先使用 keyword 对 title 和 abstract 分别做相关性评分：
        - 标题命中 keyword 权重是摘要的 2 倍
        - 同时叠加高价值学术词（novel、breakthrough 等）评分作为辅助信号

        keyword 为空时仅使用学术词评分，保证更均匀的分布。
        """
        summary = abstract[:300] + "..." if len(abstract) > 300 else abstract

        # ── 主体：keyword 相关性评分 ────────────────────────────
        kw_score = compute_keyword_relevance(title, abstract, keyword)

        # ── 辅助：学术词信号（无 API 时的语义补偿） ──────────────
        high_terms = {
            "novel", "state-of-the-art", "breakthrough", "theoretical",
            "paradigm", "framework", "fundamental", "first",
        }
        medium_terms = {
            "effective", "efficient", "improved", "robust",
            "practical", "scalable", "real-world", "empirical",
        }

        all_words = set(self._tokenize(title)) | set(self._tokenize(abstract))
        high_matches = sum(1 for t in high_terms if t in all_words)
        medium_matches = sum(1 for t in medium_terms if t in all_words)
        term_boost = high_matches * 0.08 + medium_matches * 0.04

        # ── 综合得分 ────────────────────────────────────────────
        if keyword:
            # 有关键词：以关键词相关性为主（占比 ~85%），学术词为小幅提升
            score = min(0.95, kw_score + term_boost)
        else:
            # 无关键词：纯学术词评分（沿用旧系数保持向后兼容）
            raw = high_matches * 0.12 + medium_matches * 0.06
            score = min(0.85, 0.25 + raw)

        # ── 判定 ────────────────────────────────────────────────
        if keyword:
            if score >= 0.60:
                remark = "useful"
                reason = f"标题/摘要与关键词「{keyword}」匹配度较高"
            elif score >= 0.40:
                remark = "browse"
                reason = f"标题/摘要与关键词「{keyword}」部分匹配"
            else:
                remark = "skip"
                reason = f"与关键词「{keyword}」相关性较低"
                score = max(0.15, score)
        else:
            # 无关键词时保持向后兼容：最低为 browse
            if score >= 0.65:
                remark = "useful"
                reason = "标题/摘要学术价值较高"
            else:
                remark = "browse"
                score = max(0.30, score)
                reason = "关键词匹配较少,建议人工判断"

        return LLMResult(summary=summary, remark=remark, reason=reason, score=round(score, 2))
