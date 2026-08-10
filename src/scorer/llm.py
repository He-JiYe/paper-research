"""PaperScorer：论文初筛 — 对标题+摘要进行 LLM 评级和评分。

- LLM 调用统一走 ``scorer.provider`` 抽象（OpenAI SDK / ollama 原生接口）；
- 同步/异步去重：``score_async`` 直接委托 ``score`` 在线程中执行（``asyncio.to_thread``）；
- 解析原语（JSON/分布归一化/期望得分）在 ``parse`` 模块，few-shot 在 ``prompt`` 模块。
"""

import asyncio
import logging
import time

from src.core.score import LLMResult, ScoreSource
from src.scorer.fallback import fallback_score
from src.scorer.parse import try_build_result
from src.scorer.prompt import build_summarize_prompt
from src.scorer.provider import ChatProvider, build_provider

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = "You are an academic paper review assistant. Always respond in valid JSON format."

_PROMPT_ABSTRACT_LIMIT = 2000  # 进 prompt 的摘要截断长度
_PROBE_RETRIES_DEFAULT = 3  # LLM 可用性探测重试次数（扛服务冷启动/瞬时抖动）
_PROBE_RETRY_DELAY_DEFAULT = 2.0  # 探测失败重试间隔（秒）


class PaperScorer:
    """论文初筛评分器。

    对论文标题+摘要进行 LLM 评级（important/useful/browse/skip）
    和相关性评分（0-1），并生成中文摘要。
    """

    def __init__(
        self,
        provider: ChatProvider,
        *,
        max_retries: int = 2,
        max_concurrent: int = 3,
        probe_retries: int = _PROBE_RETRIES_DEFAULT,
        probe_retry_delay: float = _PROBE_RETRY_DELAY_DEFAULT,
    ):
        self._provider = provider
        self.max_retries = max_retries  # LLM 输出无效时的额外重试次数
        self.max_concurrent = max_concurrent  # 批量评分并发数（本地 Ollama 串行，默认 1）
        self._probe_retries = probe_retries
        self._probe_retry_delay = probe_retry_delay

        # 初始化时一次性检测 LLM 可用性：不可用则全程 fallback
        self._llm_ready, self._fallback_source = self._check_llm()

    @classmethod
    def from_settings(cls, settings) -> "PaperScorer":
        """按配置构造评分器（统一入口，provider 由 settings.llm 解析）。"""
        return cls(
            build_provider(settings.llm),
            max_concurrent=settings.llm.max_concurrent,
        )

    def _check_llm(self) -> tuple[bool, ScoreSource]:
        """检测 LLM 可用性（无 Key 或连接失败判定为不可用）。

        探测带短重试：Ollama 冷启动 / 网络瞬时抖动时，第一次 check 可能失败，
        短等后重试几次，避免整个批次误走 fallback。

        Returns:
            (是否可用, 不可用时的 fallback 来源)；可用时第二项为 ``ScoreSource.LLM``。
        """
        if self._provider.requires_key and not self._provider.api_key:
            return False, ScoreSource.FALLBACK_NO_KEY
        for attempt in range(1, self._probe_retries + 1):
            try:
                ok = self._provider.check()
            except Exception as e:
                logger.warning(
                    "LLM 可用性检测失败（第 %s/%s 次）: %s", attempt, self._probe_retries, e
                )
                ok = False
            if ok:
                return True, ScoreSource.LLM
            if attempt < self._probe_retries:
                time.sleep(self._probe_retry_delay)
        logger.warning("LLM 检测 %s 次均失败，本次评分使用 fallback", self._probe_retries)
        return False, ScoreSource.FALLBACK_CONNECTION

    def _call(self, system_prompt: str, user_prompt: str) -> str | None:
        """单轮 LLM 调用（委托 provider）。作为测试 mock 接缝点保留，勿内联。"""
        return self._provider.chat(system_prompt, user_prompt)

    def score(
        self,
        title: str,
        abstract: str,
        categories: str = "",
        keyword: str = "",
        source: str = "",
        updated: str = "",
    ) -> LLMResult | None:
        """单篇论文评分（同步，唯一实现）。

        不可用 → 直接 fallback；可用 → 调 LLM，输出无效带错误说明重试，耗尽后 fallback。

        source/updated 随 prompt 提供给 LLM（来源与时效性辅助判断）。
        """
        if not self._llm_ready:
            return fallback_score(title, abstract, keyword, source=self._fallback_source)

        prompt = build_summarize_prompt(
            keyword=keyword,
            title=title,
            abstract=abstract[:_PROMPT_ABSTRACT_LIMIT],
            categories=categories,
            source=source,
            updated=updated,
        )

        for attempt in range(self.max_retries + 1):
            content = self._call(system_prompt=_SYSTEM_PROMPT, user_prompt=prompt)
            if content is None:
                logger.warning("LLM 调用失败，使用 fallback 评分")
                return fallback_score(
                    title, abstract, keyword, source=ScoreSource.FALLBACK_CONNECTION
                )

            result, error = try_build_result(content, abstract)
            if result is not None:
                return result  # LLM as judge

            logger.warning(
                "LLM 输出无效（%s），第 %s/%s 次重试", error, attempt + 1, self.max_retries + 1
            )
            prompt = (
                f"{prompt}\n\n【错误反馈】上次输出不符合要求：{error}。请严格按 JSON 模板重新输出。"
            )

        logger.warning("LLM 输出多次无效，使用 fallback 评分")
        return fallback_score(title, abstract, keyword, source=ScoreSource.FALLBACK_INVALID)

    async def score_async(
        self,
        title: str,
        abstract: str,
        categories: str = "",
        keyword: str = "",
        source: str = "",
        updated: str = "",
    ) -> LLMResult | None:
        """单篇论文评分（异步）：委托 ``score`` 在线程执行，不阻塞事件循环。"""
        return await asyncio.to_thread(
            self.score, title, abstract, categories, keyword, source, updated
        )

    async def score_batch_async(
        self,
        papers: list[dict]
    ) -> list[LLMResult | None]:
        """批量评分（异步并发，Semaphore 控制并发数）。
        """
        if not papers:
            return []

        sem = asyncio.Semaphore(max(1, self.max_concurrent))

        tasks = [_score_one(sem, self, p, i, len(papers)) for i, p in enumerate(papers)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [
            (
                r
                if isinstance(r, LLMResult)
                else _fallback_for_exception(r, papers[i], i)
            )
            for i, r in enumerate(results)
        ]


async def _score_one(
    sem: asyncio.Semaphore,
    scorer: PaperScorer,
    paper: dict,
    idx: int,
    total: int,
) -> LLMResult | None:
    """单篇评分（并发控制）；提取为模块级函数便于单测。"""
    async with sem:
        title = paper.get("title", "")
        abstract = paper.get("abstract", "")
        categories = paper.get("categories", "")
        keyword = paper.get("keyword_match", "")
        source = paper.get("source", "")
        updated = paper.get("updated", "")
        if total > 1:
            logger.info("[%s/%s] %s...", idx + 1, total, title[:60])
        return await scorer.score_async(title, abstract, categories, keyword, source, updated)


def _fallback_for_exception(result, paper: dict, idx: int) -> LLMResult:
    """把异常条目转 fallback：记录真实原因，避免伪装成连接失败且无日志。

    ``result`` 只可能是 BaseException（``gather(return_exceptions=True)`` 捕获）
    或 None（score 类型标注允许、但实现恒返回 LLMResult），二者均按异常兜底。
    """
    if isinstance(result, BaseException):
        logger.warning("评分第 %s 篇异常，转 fallback: %s", idx + 1, result)
    else:
        logger.warning("评分第 %s 篇返回非 LLMResult: %r，转 fallback", idx + 1, result)
    return fallback_score(
        paper.get("title", ""),
        paper.get("abstract", ""),
        keyword=paper.get("keyword_match", ""),
        source=ScoreSource.FALLBACK_CONNECTION,
    )
