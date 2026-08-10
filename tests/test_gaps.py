"""覆盖率补强：zotero/utils、utils 时间助手、scorer/llm、scheduler、notify/report、main。"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from src.core.models import KeywordItem
from src.serve import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_runtime():
    rt = app.state.runtime
    saved = (rt.settings, rt.zotero_client, rt.zotero_import, rt.scheduler)
    rt.settings = rt.zotero_client = rt.zotero_import = rt.scheduler = None
    yield
    rt.settings, rt.zotero_client, rt.zotero_import, rt.scheduler = saved


# ─── zotero/utils ─────────────────────────────────────────


def test_suggest_short_title_zotero_format():
    """Zotero 兜底短标题（prefix=""）：Unknown-年份-缩写"""
    from src.core.text import suggest_short_title

    title = suggest_short_title(
        {"published": "2024-05-01", "title": "A Simple Framework for Contrastive Learning"},
        prefix="",
    )
    assert title == "Unknown-2024-SimpLearni"
    assert "????" in suggest_short_title({"published": "", "title": "A Novel Method"}, prefix="")


def test_extract_keys_success_and_failure():
    from src.zotero.utils import extract_key, extract_keys

    resp = {"successful": {"0": {"key": "K1"}, "1": {"key": "K2"}}, "failed": {}}
    assert extract_keys(resp, 2) == ["K1", "K2"]
    assert extract_key(resp) == "K1"
    with pytest.raises(RuntimeError):
        extract_keys({"successful": {}, "failed": {"0": "boom"}}, 1)
    with pytest.raises(RuntimeError):
        extract_keys([{"key": "K1"}], 2)  # list 数量不足


# ─── scorer/llm 补充路径 ──────────────────────────────────


def _provider(api_key: str = "sk-test"):
    from src.scorer.provider import OpenAIProvider

    return OpenAIProvider(model="m", api_base="http://api.test", api_key=api_key)


def _ready_scorer(**kw):
    from src.scorer import PaperScorer, ScoreSource

    api_key = kw.pop("api_key", "sk-test")
    with patch.object(PaperScorer, "_check_llm", return_value=(True, ScoreSource.LLM)):
        return PaperScorer(_provider(api_key), **kw)


def test_check_llm_success():
    from src.scorer import PaperScorer

    provider = _provider("sk-test")
    with patch.object(provider, "check", return_value=True):
        s = PaperScorer(provider)
    assert s._llm_ready is True


def test_call_success_and_failure():
    s = _ready_scorer(api_key="sk-test")
    resp = MagicMock()
    resp.choices[0].message.content = "ok"
    with patch("src.scorer.provider.OpenAIProvider._client") as client:
        client.return_value.chat.completions.create.return_value = resp
        assert s._call("s", "u") == "ok"
    with patch("src.scorer.provider.OpenAIProvider._client", side_effect=RuntimeError("boom")):
        assert s._call("s", "u") is None


def test_score_async_delegates_to_score():
    s = _ready_scorer(api_key="sk-test")
    payload = json.dumps(
        {
            "summary": "s",
            "remark": "useful",
            "reason": "r",
            "score_distribution": {"0": 0, "1": 0, "2": 0, "3": 0, "4": 1, "5": 0},
        }
    )
    with patch.object(s, "_call", return_value=payload):
        result = asyncio.run(s.score_async("T", "A", keyword="kw"))
    assert result is not None
    assert result.remark == "useful"


def test_score_batch_no_key():
    from src.scorer import PaperScorer

    s = PaperScorer(_provider(api_key=""))
    results = asyncio.run(
        s.score_batch_async([{"title": "T", "abstract": "A", "keyword_match": "kw"}])
    )
    assert len(results) == 1
    assert results[0] is not None


# ─── scheduler 补充路径 ───────────────────────────────────


@pytest.fixture
def sched():
    from src.serve.scheduler import FetchScheduler

    settings = SimpleNamespace(
        scheduler=SimpleNamespace(
            enabled=True, fetch_time="08:30", catch_up_on_start=True
        ),
        keywords=[KeywordItem(keyword="RL", active=True)],
        notification=SimpleNamespace(enabled=False),
        server=SimpleNamespace(host="127.0.0.1", port=8899),
    )
    return FetchScheduler(settings)


def test_scheduler_stop_cancels_task(sched):
    task = MagicMock()
    sched._task = task
    sched.stop()
    task.cancel.assert_called_once()


def test_scheduler_should_catch_up_after_time(sched, monkeypatch):
    from datetime import datetime

    monkeypatch.setattr(sched, "_now", lambda: datetime(2026, 8, 12, 20, 0))
    with patch("src.db.PaperDB") as db:
        db.return_value.has_successful_since.return_value = False
        assert sched._should_catch_up() is True
        db.return_value.has_successful_since.return_value = True
        assert sched._should_catch_up() is False
        # fetch_time=08:30 → since 取「当日 08:30:00」
        db.return_value.has_successful_since.assert_called_with("2026-08-12 08:30:00")


def test_check_llm_retries_on_transient_failure():
    """LLM 探测瞬时失败会短等重试（B3：不把一次抖动当成整批 fallback）。"""
    from src.scorer import PaperScorer

    provider = _provider("sk-test")
    with patch.object(provider, "check", side_effect=[False, True]) as check:
        s = PaperScorer(provider, probe_retries=2, probe_retry_delay=0)
    assert s._llm_ready is True
    assert check.call_count == 2


def test_check_llm_falls_back_after_all_retries():
    """LLM 探测多次全部失败 → 判不可用走 fallback（B3 兜底分支）。"""
    from src.scorer import PaperScorer

    provider = _provider("sk-test")
    with patch.object(provider, "check", return_value=False) as check:
        s = PaperScorer(provider, probe_retries=2, probe_retry_delay=0)
    assert s._llm_ready is False
    assert check.call_count == 2


def test_scheduler_catchup_fetch_sends_email(sched, monkeypatch):
    """启动补抓=正式今日抓取，有新论文即发今日邮件（与定时一致）。"""

    async def fake_pipe(*a, **k):
        return {"fetched": 3, "new": 2}

    monkeypatch.setattr("src.pipeline.fetch.run_fetch_pipeline", fake_pipe)
    with patch("src.notify.report.send_fetch_report") as send, patch("src.db.PaperDB"):
        asyncio.run(sched._run_fetch())
        send.assert_called_once()


# ─── notify/report 成功分支 ───────────────────────────────


def test_report_sends():
    from src.notify.report import send_fetch_report

    settings = SimpleNamespace(
        notification=SimpleNamespace(enabled=True),
        server=SimpleNamespace(host="127.0.0.1", port=8899),
        keywords=[],
    )
    db = MagicMock()
    db.get_pending.return_value = [{"title": "T"}]
    db.get_stats.return_value = {"total": 1}
    with patch("src.notify.sender.EmailNotifier") as ne:
        ne.return_value.send_fetch_report.return_value = True
        result = send_fetch_report(settings, db)
    assert result == {"sent": True, "reason": None}


# ─── main.py dispatch 边界 ────────────────────────────────


def test_dispatch_zotero_warning(capsys):
    from src.main import dispatch

    settings = SimpleNamespace(zotero=SimpleNamespace(api_key="", library_id=""))
    with (
        patch("src.config.loader.load_settings", return_value=settings),
        patch("src.commands.cmd_serve"),
    ):
        dispatch(MagicMock(command="serve"))
    assert "Zotero" in capsys.readouterr().out


def test_dispatch_unknown_command_noop():
    """未知命令不匹配任何分支，不抛错。"""
    from src.main import dispatch

    with patch("src.config.loader.load_settings"):
        dispatch(MagicMock(command="bogus"))  # 静默返回
