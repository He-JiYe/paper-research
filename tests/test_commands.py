"""命令实现测试"""

from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from src.commands import cmd_fetch


class TestCmdFetch:
    """测试 cmd_fetch 命令"""

    def _make_args(self, **overrides):
        """创建模拟 args 对象"""
        args = MagicMock()
        args.keyword = None
        args.dry_run = False
        args.serve = False
        args.mode = "incremental"
        args.max_results = 0
        for k, v in overrides.items():
            setattr(args, k, v)
        return args

    @pytest.fixture(autouse=True)
    def _patch_deps(self, request):
        """Mock 所有外部依赖

        cmd_fetch → run_fetch_pipeline → get_source(settings) → source.fetch_all()
        因此 patch get_source 返回 mock source。
        """
        patcher1 = patch("src.config.get_active_keywords")
        patcher2 = patch("src.network.factory.get_source")
        patcher3 = patch("src.agents.paper_scorer.PaperScorer")
        patcher4 = patch("src.db.insert_fetch_log")
        patcher5 = patch("src.db.get_paper")
        patcher6 = patch("src.db.get_keyword_paper_count", return_value=10)
        patcher7 = patch("src.db.get_stats", return_value={})
        patcher8 = patch("src.serve.renderer.generate_summary_html")
        patcher9 = patch("src.serve.renderer.generate_summary_html")
        patcher10 = patch("src.serve.renderer.generate_notes_index_html")
        patcher11 = patch("src.db.insert_paper")
        patcher12 = patch("src.db.touch_paper")
        patcher13 = patch("src.db.update_paper_summary")
        patcher14 = patch("src.db.update_paper_version")

        self.mock_get_kw = patcher1.start()
        self.mock_get_source = patcher2.start()
        self.mock_scorer_cls = patcher3.start()
        self.mock_insert_fetch_log = patcher4.start()
        self.mock_get_paper = patcher5.start()
        self.mock_kw_count = patcher6.start()
        self.mock_get_stats = patcher7.start()
        self.mock_gen_summary = patcher8.start()
        self.mock_gen_landing = patcher9.start()
        self.mock_gen_notes = patcher10.start()
        self.mock_insert_paper = patcher11.start()
        self.mock_touch_paper = patcher12.start()
        self.mock_update_summary = patcher13.start()
        self.mock_update_version = patcher14.start()

        request.addfinalizer(patcher1.stop)
        request.addfinalizer(patcher2.stop)
        request.addfinalizer(patcher3.stop)
        request.addfinalizer(patcher4.stop)
        request.addfinalizer(patcher5.stop)
        request.addfinalizer(patcher6.stop)
        request.addfinalizer(patcher7.stop)
        request.addfinalizer(patcher8.stop)
        request.addfinalizer(patcher9.stop)
        request.addfinalizer(patcher10.stop)
        request.addfinalizer(patcher11.stop)
        request.addfinalizer(patcher12.stop)
        request.addfinalizer(patcher13.stop)
        request.addfinalizer(patcher14.stop)

        self.mock_get_kw.return_value = [
            {"keyword": "test-time adaptation", "arxiv_cats": ["cs.CV", "cs.LG"], "active": True},
            {"keyword": "out-of-distribution detection", "arxiv_cats": ["cs.LG"], "active": True},
        ]
        # mock source 的 fetch_all 返回 (空列表, 0, 0)
        self.mock_source = AsyncMock()
        self.mock_source.fetch_all.return_value = ([], 0, 0)
        self.mock_get_source.return_value = self.mock_source
        yield

    def test_fetch_incremental_mode(self, mock_settings, db_conn):
        """增量模式应传 historical=False（有时间限制）"""
        args = self._make_args()
        cmd_fetch(args, mock_settings, db_conn)
        call_kwargs = self.mock_source.fetch_all.call_args[1]
        assert call_kwargs.get("historical") is False

    def test_fetch_historical_mode(self, mock_settings, db_conn):
        """历史模式应传 historical=True（无时间限制）"""
        args = self._make_args(mode="historical")
        cmd_fetch(args, mock_settings, db_conn)
        call_kwargs = self.mock_source.fetch_all.call_args[1]
        assert call_kwargs.get("historical") is True

    def test_fetch_incremental_uses_max_results(self, mock_settings, db_conn):
        """增量模式 max_results 使用 settings.fetch.max_results"""
        args = self._make_args()
        cmd_fetch(args, mock_settings, db_conn)
        call_kwargs = self.mock_source.fetch_all.call_args[1]
        assert call_kwargs.get("max_results") == mock_settings.fetch.max_results

    def test_fetch_historical_uses_max_results(self, mock_settings, db_conn):
        """历史模式不设 args.max_results 时使用 settings 默认值"""
        args = self._make_args(mode="historical")
        cmd_fetch(args, mock_settings, db_conn)
        call_kwargs = self.mock_source.fetch_all.call_args[1]
        assert call_kwargs.get("max_results") == mock_settings.fetch.max_results

    def test_fetch_with_custom_max_results(self, mock_settings, db_conn):
        """自定义 max_results 应覆盖默认值"""
        args = self._make_args(mode="historical", max_results=100)
        cmd_fetch(args, mock_settings, db_conn)
        call_kwargs = self.mock_source.fetch_all.call_args[1]
        assert call_kwargs.get("max_results") == 100

    def test_fetch_keyword_filter(self, mock_settings, db_conn):
        """指定 keyword 时应只查询匹配的关键词"""
        args = self._make_args(keyword="test-time adaptation")
        self.mock_source.fetch_all.return_value = ([], 0, 0)
        cmd_fetch(args, mock_settings, db_conn)
        kw_list = self.mock_source.fetch_all.call_args[0][0]
        assert len(kw_list) == 1
        assert kw_list[0]["keyword"] == "test-time adaptation"

    def test_fetch_dry_run_no_insert(self, mock_settings, db_conn):
        """dry-run 模式不应插入数据库"""
        args = self._make_args(dry_run=True)
        self.mock_source.fetch_all.return_value = (
            [{"arxiv_id": "2401.99999", "title": "Test", "keyword_match": "test"}],
            1, 0,
        )
        cmd_fetch(args, mock_settings, db_conn)
        self.mock_insert_paper.assert_not_called()

    def test_fetch_invalid_keyword(self, mock_settings, db_conn):
        """不存在的关键词应提示并返回"""
        args = self._make_args(keyword="nonexistent-keyword")
        self.mock_get_kw.return_value = []
        cmd_fetch(args, mock_settings, db_conn)
        self.mock_source.fetch_all.assert_not_called()
