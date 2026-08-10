"""run_fetch_pipeline 边界测试（不触网）。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import src.pipeline.fetch as fp
from src.core.models import KeywordItem, Record


def _settings():
    return SimpleNamespace(
        llm=SimpleNamespace(api_key="", api_base="", model="", temperature=0, max_tokens=0),
        fetch=SimpleNamespace(sources=[]),
        keywords=[KeywordItem(keyword="RL", active=True)],
    )


class FakeEmptySource:
    async def fetch(self, options):
        return []


class FakeOneSource:
    async def fetch(self, options):
        return [
            Record(
                title="T", source_id="1", url="u", pdf_url="p", source="arxiv", keyword_match="RL"
            )
        ]


def _patch_source(name, cls):
    return patch.object(fp.REGISTRY.sources, "get", return_value=cls)


def _arxiv_source():
    from src.network.source.arxiv import ArxivOptions

    return SimpleNamespace(source="arxiv", options=ArxivOptions(max_results=10, lookback_days=3))


def test_run_fetch_pipeline_no_sources():
    result = asyncio.run(fp.run_fetch_pipeline(_settings(), [], 20))
    assert result["fetched"] == 0


def test_run_fetch_pipeline_empty_results():
    settings = _settings()
    settings.fetch.sources = [_arxiv_source()]
    with _patch_source("arxiv", FakeEmptySource):
        result = asyncio.run(fp.run_fetch_pipeline(settings, [KeywordItem(keyword="RL")], 20))
    assert result["fetched"] == 0


def test_run_fetch_pipeline_dry_run():
    settings = _settings()
    settings.fetch.sources = [_arxiv_source()]
    with _patch_source("arxiv", FakeOneSource):
        result = asyncio.run(
            fp.run_fetch_pipeline(settings, [KeywordItem(keyword="RL")], 20, dry_run=True)
        )
    assert result["fetched"] == 1
    assert result["new"] == 0


@pytest.fixture
def client_db():
    """占位 fixture：确认 REGISTRY 已导入（依赖 arxiv 源已注册）。"""
    assert fp.REGISTRY.sources.get("arxiv")
    return
