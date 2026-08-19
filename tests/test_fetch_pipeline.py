"""run_fetch_pipeline 多数据源测试：共享关键词注入 + 各源独立 options/skip。"""

import pytest
import src.pipeline.fetch as fp
from src.core.config import (
    AppConfig,
    EmailConfig,
    FetchConfig,
    LLMConfig,
    SchedulerConfig,
    ServerConfig,
    SourceConfig,
)
from src.core.models import KeywordItem, Record
from src.core.score import ScoreSource
from src.network import REGISTRY
from src.network.source.arxiv import ArxivOptions


class FakeDB:
    def __init__(self):
        self.existing = {"keep1"}
        self.added = []
        self.logs = []

    def get_existing_ids(self, source):
        return set(self.existing)

    def add_papers(self, papers):
        self.added.extend(papers)
        return len(papers)

    def add_fetch_log(self, **kw):
        self.logs.append(kw)


class _DummyScore:
    summary = "s"
    remark = "r"
    reason = "why"
    score = 0.8
    source = ScoreSource.LLM


class FakeScorer:
    def __init__(self, *a, **kw):
        pass

    @classmethod
    def from_settings(cls, settings):
        return cls()

    def score(self, *a, **kw):
        return _DummyScore()

    async def score_batch_async(self, papers, **kw):
        return [_DummyScore() for _ in papers]


def _settings() -> AppConfig:
    return AppConfig(
        llm=LLMConfig(api_key=""),  # 走串行评分分支（FakeScorer 接管）
        fetch=FetchConfig(
            sources=[
                SourceConfig(
                    source="arxiv",
                    options=ArxivOptions(max_results=7, lookback_days=2, sort_by="submittedDate"),
                ),
                SourceConfig(
                    source="arxiv",
                    options=ArxivOptions(max_results=9, lookback_days=5, sort_by="submittedDate"),
                ),
            ]
        ),
        keywords=[
            KeywordItem(keyword="WAM", active=True),
            KeywordItem(keyword="RL", active=False),
        ],
        notification=EmailConfig(enabled=False),
        scheduler=SchedulerConfig(enabled=False),
        server=ServerConfig(),
    )


@pytest.mark.asyncio
async def test_multisource_iterates_with_shared_keywords(monkeypatch):
    settings = _settings()
    db = FakeDB()
    calls = []

    async def fake_fetch(self, options):
        calls.append(options)
        return [Record(title="t", source="arxiv", source_id=f"r{len(calls)}", keyword_match="WAM")]

    FakeSource = type("FakeSource", (), {"fetch": fake_fetch})
    monkeypatch.setattr(REGISTRY.sources, "get", lambda name: FakeSource)
    monkeypatch.setattr(fp, "PaperScorer", FakeScorer)

    result = await fp.run_fetch_pipeline(settings, settings.keywords, 0, db=db, mode="incremental")

    # 两个源都抓取
    assert len(calls) == 2, f"calls={len(calls)}"
    # 共享 keywords 注入（active 过滤由 get_active_keywords 负责）
    assert calls[0].keywords == settings.keywords
    assert calls[1].keywords == settings.keywords
    # 各源自己的 options 参数保留
    assert calls[0].max_results == 7 and calls[0].lookback_days == 2
    assert calls[1].max_results == 9 and calls[1].lookback_days == 5
    # skip_ids 含 DB 已有 id（按源作用域）
    assert "keep1" in calls[0].skip_ids
    assert "keep1" in calls[1].skip_ids
    # 结果聚合 + 入库
    assert result["fetched"] == 2
    assert result["new"] == 2
    assert len(db.added) == 2


@pytest.mark.asyncio
async def test_max_results_override_applies_to_all(monkeypatch):
    settings = _settings()
    db = FakeDB()
    calls = []

    async def fake_fetch(self, options):
        calls.append(options)
        return []

    FakeSource = type("FakeSource", (), {"fetch": fake_fetch})
    monkeypatch.setattr(REGISTRY.sources, "get", lambda name: FakeSource)
    monkeypatch.setattr(fp, "PaperScorer", FakeScorer)

    await fp.run_fetch_pipeline(settings, settings.keywords, 30, db=db, mode="incremental")

    assert calls[0].max_results == 30  # CLI/Web 覆盖优先
    assert calls[1].max_results == 30


@pytest.mark.asyncio
async def test_no_sources_returns_empty(monkeypatch):
    settings = _settings()
    settings.fetch.sources = []
    result = await fp.run_fetch_pipeline(
        settings, settings.keywords, 0, db=FakeDB(), mode="incremental"
    )
    assert result["fetched"] == 0
    assert result["new"] == 0


@pytest.mark.asyncio
async def test_dry_run_returns_full_contract(monkeypatch):
    """dry-run 返回契约与其他路径一致（含 summarized 键），且不写入 DB"""
    settings = _settings()
    db = FakeDB()
    calls = []

    async def fake_fetch(self, options):
        calls.append(options)
        return [Record(title="t", source="arxiv", source_id=f"r{len(calls)}", keyword_match="WAM")]

    FakeSource = type("FakeSource", (), {"fetch": fake_fetch})
    monkeypatch.setattr(REGISTRY.sources, "get", lambda name: FakeSource)
    monkeypatch.setattr(fp, "PaperScorer", FakeScorer)

    result = await fp.run_fetch_pipeline(
        settings, settings.keywords, 0, db=db, mode="incremental", dry_run=True
    )

    assert set(result) == {"fetched", "new", "summarized", "papers_fetched", "fetch_date"}
    assert result["summarized"] == 0  # dry-run 不做 LLM 评分
    assert result["new"] == 0
    assert result["fetched"] == 2  # 两个源各返回 1 篇（source_id 不同，不触发去重）
    assert db.added == []  # 不写库
    assert db.logs == []


@pytest.mark.asyncio
async def test_dry_run_zero_papers_does_not_write_fetch_log(monkeypatch):
    """B1：dry-run 抓取到 0 篇也不写 fetch_log（不污染补抓判定 has_successful_since）。"""
    settings = _settings()
    db = FakeDB()

    async def fake_fetch(self, options):
        return []  # 抓取不到任何新论文

    FakeSource = type("FakeSource", (), {"fetch": fake_fetch})
    monkeypatch.setattr(REGISTRY.sources, "get", lambda name: FakeSource)
    monkeypatch.setattr(fp, "PaperScorer", FakeScorer)

    result = await fp.run_fetch_pipeline(
        settings, settings.keywords, 0, db=db, mode="incremental", dry_run=True
    )
    assert result["fetched"] == 0
    assert db.logs == []  # dry-run 不写 fetch_log


@pytest.mark.asyncio
async def test_zero_new_papers_writes_success_log(monkeypatch):
    """非 dry-run 抓取到 0 篇也写一条成功日志（has_successful_since 补抓判定正确性的关键）。"""
    settings = _settings()
    db = FakeDB()

    async def fake_fetch(self, options):
        return []

    FakeSource = type("FakeSource", (), {"fetch": fake_fetch})
    monkeypatch.setattr(REGISTRY.sources, "get", lambda name: FakeSource)
    monkeypatch.setattr(fp, "PaperScorer", FakeScorer)

    result = await fp.run_fetch_pipeline(settings, settings.keywords, 0, db=db, mode="incremental")

    assert result["new"] == 0
    assert len(db.logs) == 1
    assert db.logs[0]["papers_fetched"] == 0
    assert db.logs[0]["papers_new"] == 0
    assert db.logs[0]["run_time"]  # 与补抓判定同口径的秒级时间戳


@pytest.mark.asyncio
async def test_dedup_by_source_id_and_scored_fields_written(monkeypatch):
    """同 (source, source_id) 多关键词命中只评分一次（保留首个 keyword_match），评分字段落库。"""
    settings = _settings()
    db = FakeDB()

    async def fake_fetch(self, options):
        # 两个源返回同一篇（同 source_id）→ 去重后只评分/入库一次
        return [
            Record(title="dup", source="arxiv", source_id="dup1", keyword_match="WAM"),
            Record(title="dup", source="arxiv", source_id="dup1", keyword_match="RL"),
        ]

    FakeSource = type("FakeSource", (), {"fetch": fake_fetch})
    monkeypatch.setattr(REGISTRY.sources, "get", lambda name: FakeSource)
    monkeypatch.setattr(fp, "PaperScorer", FakeScorer)

    result = await fp.run_fetch_pipeline(settings, settings.keywords, 0, db=db, mode="incremental")

    assert result["fetched"] == 1  # 去重后仅 1 篇
    assert result["new"] == 1
    assert result["summarized"] == 1
    assert len(db.added) == 1
    row = db.added[0]
    assert row["keyword_match"] == "WAM"  # 保留首个命中的关键词
    assert row["llm_summary"] == "s"  # 评分字段已写回
    assert row["llm_remark"] == "r"
    assert row["llm_score"] == 0.8
    assert row["score_source"] == ScoreSource.LLM.value
