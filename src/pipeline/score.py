"""唯一评分+写回循环：对论文行批量 LLM 评分并写回 llm_* / score_source。

被 ``pipeline.fetch.run_fetch_pipeline`` 唯一调用。无 API Key / 连接失败等降级由
``PaperScorer.score`` 内部处理（返回 fallback LLMResult），此处无需分支；
``score_batch_async`` 内部已 ``gather(return_exceptions=True)`` 兜底，不会抛异常。
"""

import logging

logger = logging.getLogger(__name__)


async def score_rows(scorer, rows: list[dict]) -> tuple[int, list[dict]]:
    """批量评分并写回 rows。

    Args:
        scorer: PaperScorer 实例（不可用时会自动 fallback）
        rows: 论文行 dict 列表（record_to_row 产物，含 source/source_id/title/abstract/...）

    Returns:
        (评分数, 写回后的 rows)；rows 每项含 ``llm_summary``/``llm_remark``/
        ``llm_reason``/``llm_score``/``score_source``。
    """
    if not rows:
        return 0, rows

    results = await scorer.score_batch_async(rows)

    # score_batch_async 保证每个结果都是 LLMResult 且与 rows 等长
    # （异常/None 一律转 fallback），strict=True 让长度失配当场暴露。
    for paper, result in zip(rows, results, strict=True):
        paper.update(
            llm_summary=result.summary,
            llm_remark=result.remark,
            llm_reason=result.reason,
            llm_score=result.score,
            score_source=result.source.value,
        )

    logger.info("初筛完成: %s 篇", len(rows))
    return len(rows), rows
