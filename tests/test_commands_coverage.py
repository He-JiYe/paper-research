"""commands.py coverage completion"""

import argparse
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


def args(**kw):
    defaults = {
        "command": None,
        "keyword": None,
        "dry_run": False,
        "serve": False,
        "head": 10,
        "all": False,
        "arxiv_id": None,
        "type": None,
        "mark": None,
        "status": None,
        "sort": "date",
    }
    defaults.update(kw)
    return argparse.Namespace(**defaults)


class TestCmdFetchIncremental:
    def test_fetch_with_log(self, conn, mock_settings, capsys):
        from src.db import insert_fetch_log

        insert_fetch_log(
            conn,
            keywords_used=1,
            papers_fetched=5,
            papers_new=3,
            papers_updated=0,
            papers_summarized=3,
            status="success",
        )
        from src.commands import cmd_fetch

        a = args(command="fetch", dry_run=True)
        with patch(
            "src.config.get_active_keywords",
            return_value=[{"keyword": "ML", "active": True}],
        ):
            with patch(
                "src.network.arxiv.fetch_all", AsyncMock(return_value=([], 0, 0))
            ):
                cmd_fetch(a, mock_settings, conn)
                out = capsys.readouterr().out
                assert "Dry-run" in out

    def test_fetch_dry_run_many(self, conn, mock_settings, capsys):
        papers = []
        for i in range(1, 15):
            papers.append(
                {
                    "arxiv_id": f"2401.{i:05d}",
                    "version": 1,
                    "title": f"Paper {i}",
                    "authors": "A",
                    "abstract": "x",
                    "url": "",
                    "primary_category": "cs.CV",
                    "categories": "cs.CV",
                    "published": "2024-01-01",
                    "arxiv_updated": "",
                    "keyword_match": "t",
                }
            )
        from src.commands import cmd_fetch

        a = args(command="fetch", dry_run=True)
        with patch(
            "src.config.get_active_keywords",
            return_value=[{"keyword": "ML", "active": True}],
        ):
            with patch(
                "src.network.arxiv.fetch_all", AsyncMock(return_value=(papers, 14, 0))
            ):
                cmd_fetch(a, mock_settings, conn)
                out = capsys.readouterr().out
                assert "Dry-run" in out

    def test_fetch_version_update(self, conn, mock_settings, capsys):
        from src.db import insert_paper

        insert_paper(
            conn,
            {
                "arxiv_id": "2401.00001",
                "version": 1,
                "title": "Old",
                "authors": "A",
                "abstract": "a",
                "url": "",
                "primary_category": "cs.CV",
                "categories": "cs.CV",
                "published": "2024-01-01",
                "arxiv_updated": "2024-01-01T00:00:00Z",
                "keyword_match": "t",
                "fetch_date": "2024-07-01",
            },
        )
        new = {
            "arxiv_id": "2401.00001",
            "version": 2,
            "title": "New",
            "authors": "A",
            "abstract": "a",
            "url": "",
            "primary_category": "cs.CV",
            "categories": "cs.CV",
            "published": "2024-01-01",
            "arxiv_updated": "2024-02-01T00:00:00Z",
            "keyword_match": "t",
        }
        from src.commands import cmd_fetch

        a = args(command="fetch")
        with patch(
            "src.config.get_active_keywords",
            return_value=[{"keyword": "ML", "active": True}],
        ):
            with patch(
                "src.network.arxiv.fetch_all", AsyncMock(return_value=([new], 1, 0))
            ):
                with patch("src.agents.PaperScorer") as MockPS:
                    m = MagicMock()
                    m.score.return_value = MagicMock(
                        summary="S", remark="u", reason="R", score=0.7
                    )
                    MockPS.return_value = m
                    with (
                        patch("src.serve.generate_summary_html"),
                        patch("src.serve.generate_landing_html"),
                        patch("src.serve.generate_notes_index_html"),
                    ):
                        cmd_fetch(a, mock_settings, conn)
                        out = capsys.readouterr().out
                        assert "更新" in out

    def test_fetch_no_new(self, conn, mock_settings, capsys):
        from src.commands import cmd_fetch

        a = args(command="fetch")
        with patch(
            "src.config.get_active_keywords",
            return_value=[{"keyword": "ML", "active": True}],
        ):
            with patch(
                "src.network.arxiv.fetch_all", AsyncMock(return_value=([], 0, 0))
            ):
                with (
                    patch("src.serve.generate_summary_html"),
                    patch("src.serve.generate_landing_html"),
                    patch("src.serve.generate_notes_index_html"),
                ):
                    cmd_fetch(a, mock_settings, conn)
                    out = capsys.readouterr().out
                    assert "完成" in out

    def test_fetch_with_apikey(self, conn, capsys):
        from src.config import (AppConfig, ArxivConfig, LLMConfig,
                                NotificationConfig, ScoringConfig,
                                ServerConfig)

        s = AppConfig(
            llm=LLMConfig(api_key="sk-t"),
            arxiv=ArxivConfig(),
            server=ServerConfig(),
            notification=NotificationConfig(),
            scoring=ScoringConfig(),
        )
        from src.commands import cmd_fetch

        a = args(command="fetch")
        with patch(
            "src.config.get_active_keywords",
            return_value=[{"keyword": "ML", "active": True}],
        ):
            with patch(
                "src.network.arxiv.fetch_all", AsyncMock(return_value=([], 0, 0))
            ):
                with (
                    patch("src.serve.generate_summary_html"),
                    patch("src.serve.generate_landing_html"),
                    patch("src.serve.generate_notes_index_html"),
                ):
                    cmd_fetch(a, s, conn)
                    out = capsys.readouterr().out
                    assert "完成" in out

    def test_fetch_with_serve(self, conn, mock_settings, capsys):
        from src.commands import cmd_fetch

        a = args(command="fetch", dry_run=False, serve=True)
        with patch(
            "src.config.get_active_keywords",
            return_value=[{"keyword": "ML", "active": True}],
        ):
            with patch(
                "src.network.arxiv.fetch_all", AsyncMock(return_value=([], 0, 0))
            ):
                with (
                    patch("src.serve.generate_summary_html"),
                    patch("src.serve.generate_landing_html"),
                    patch("src.serve.generate_notes_index_html"),
                ):
                    with patch("src.commands.cmd_serve") as ms:
                        cmd_fetch(a, mock_settings, conn)
                        ms.assert_called_once()


class TestCmdServe:
    def test_serve_no_summary(self, conn, mock_settings, capsys, temp_dir):
        from src.commands import cmd_serve

        with patch("src.commands.OUTPUT_DIR", Path(temp_dir)):
            cmd_serve(args(command="serve"), mock_settings, conn)
            out = capsys.readouterr().out
            assert "尚未生成" in out

    def test_serve_with_summary(self, conn, mock_settings, capsys, temp_dir):
        from src.commands import cmd_serve

        d = Path(temp_dir) / "summaries"
        d.mkdir(parents=True)
        (d / "index.html").write_text("<html></html>")
        with patch("src.commands.OUTPUT_DIR", Path(temp_dir)):
            with patch("webbrowser.open"), patch("src.serve.run_server"):
                cmd_serve(args(command="serve"), mock_settings, conn)
                out = capsys.readouterr().out
                assert "Summary" in out


class TestCmdReviewAll:
    def test_review_all(self, sample_papers, conn, mock_settings, capsys):
        from src.commands import cmd_review

        cmd_review(args(command="review", head=10, all=True), mock_settings, conn)
        out = capsys.readouterr().out
        assert "ArXiv ID" in out


class TestCmdMarkDeepRead:
    def test_mark_deep_read(self, conn, mock_settings, capsys, sample_papers):
        from src.commands import cmd_mark

        with patch("src.agents.note_agent.NoteAgent") as M:

            async def g(*a, **kw):
                return None

            M.generate_from_arxiv_id = g
            with (
                patch("src.serve.renderer.generate_summary_html"),
                patch("src.serve.renderer.generate_notes_index_html"),
            ):
                cmd_mark(
                    args(command="mark", arxiv_id="2401.00001", type="deep-read"),
                    mock_settings,
                    conn,
                )
                out = capsys.readouterr().out
                assert "Deep Read" in out


class TestCmdNote:
    def test_note_new(self, conn, mock_settings, capsys):
        from src.commands import cmd_note

        mp = [
            {
                "arxiv_id": "2401.00100",
                "version": 1,
                "title": "New",
                "authors": "A",
                "abstract": "a",
                "url": "",
                "primary_category": "cs.CV",
                "categories": "cs.CV",
                "published": "2024-01-01",
                "arxiv_updated": "",
                "keyword_match": "t",
            }
        ]
        with patch("src.network.arxiv.fetch_by_ids", AsyncMock(return_value=mp)):
            with patch("src.agents.PaperScorer") as M:
                m = MagicMock()
                m.score.return_value = MagicMock(
                    summary="S", remark="i", reason="R", score=0.9
                )
                M.return_value = m
                with patch("src.agents.note_agent.NoteAgent") as M2:

                    async def g(*a, **kw):
                        return None

                    M2.generate_from_arxiv_id = g
                    with (
                        patch("os.startfile"),
                        patch("src.serve.renderer.generate_summary_html"),
                        patch("src.serve.renderer.generate_notes_index_html"),
                    ):
                        cmd_note(
                            args(command="note", arxiv_id="2401.00100"),
                            mock_settings,
                            conn,
                        )
                        out = capsys.readouterr().out
                        assert "已入库" in out

    def test_note_existing(self, conn, mock_settings, capsys, sample_papers):
        from src.commands import OUTPUT_DIR as CMD_OUTPUT_DIR
        from src.commands import cmd_note

        note_path = CMD_OUTPUT_DIR / "notes" / "2401.00001" / "note.md"
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text("# Existing Note")
        try:
            with (
                patch("os.startfile"),
                patch("src.serve.renderer.generate_summary_html"),
                patch("src.serve.renderer.generate_notes_index_html"),
            ):
                cmd_note(
                    args(command="note", arxiv_id="2401.00001"), mock_settings, conn
                )
                out = capsys.readouterr().out
                assert "打开" in out
        finally:
            import shutil

            shutil.rmtree(str(note_path.parent), ignore_errors=True)


class TestCmdListMore:
    def test_list_rdate(self, sample_papers, conn, mock_settings, capsys):
        from src.commands import cmd_list

        cmd_list(args(command="list", sort="rdate", head=10), mock_settings, conn)
        assert "2401" in capsys.readouterr().out

    def test_list_rscore(self, sample_papers, conn, mock_settings, capsys):
        from src.commands import cmd_list

        cmd_list(args(command="list", sort="rscore", head=10), mock_settings, conn)
        assert "2401" in capsys.readouterr().out

    def test_list_status(self, sample_papers, conn, mock_settings, capsys):
        from src.commands import cmd_list

        cmd_list(
            args(command="list", status="summarized", head=10), mock_settings, conn
        )
        assert "2401" in capsys.readouterr().out


class TestCmdStatusLogs:
    def test_status_with_logs(self, conn, mock_settings, capsys):
        from src.db import insert_fetch_log

        insert_fetch_log(
            conn,
            keywords_used=2,
            papers_fetched=10,
            papers_new=5,
            papers_updated=1,
            papers_summarized=5,
            status="success",
        )
        from src.commands import cmd_status

        cmd_status(args(command="status"), mock_settings, conn)
        out = capsys.readouterr().out
        assert "Fet" in out or "OK" in out


class TestCmdFetchEdgeCases:
    def test_fetch_incremental_bad_log(self, conn, mock_settings, capsys):
        """fetch_log 包含无效日期格式（触发 except pass）"""
        from src.db import insert_fetch_log

        insert_fetch_log(
            conn,
            keywords_used=1,
            papers_fetched=0,
            papers_new=0,
            papers_updated=0,
            papers_summarized=0,
            status="success",
        )
        from src.commands import cmd_fetch

        a = args(command="fetch", dry_run=True)
        with patch("src.config.get_active_keywords", return_value=[]):
            cmd_fetch(a, mock_settings, conn)

    def test_fetch_fallback_score_none(self, conn, mock_settings, capsys):
        """scorer.score 返回 None"""
        from src.commands import cmd_fetch

        a = args(command="fetch")
        with patch(
            "src.config.get_active_keywords",
            return_value=[{"keyword": "ML", "active": True}],
        ):
            with patch(
                "src.network.arxiv.fetch_all", AsyncMock(return_value=([], 0, 0))
            ):
                with (
                    patch("src.serve.generate_summary_html"),
                    patch("src.serve.generate_landing_html"),
                    patch("src.serve.generate_notes_index_html"),
                ):
                    cmd_fetch(a, mock_settings, conn)
                    assert "完成" in capsys.readouterr().out

    def test_fetch_fallback_exception(self, conn, mock_settings, capsys):
        """scorer.score 抛出异常"""
        from src.commands import cmd_fetch

        paper = {
            "arxiv_id": "2401.00999",
            "version": 1,
            "title": "New Fail Paper",
            "authors": "A",
            "abstract": "a",
            "url": "",
            "primary_category": "cs.CV",
            "categories": "cs.CV",
            "published": "2024-01-01",
            "arxiv_updated": "2024-01-01T00:00:00Z",
            "keyword_match": "t",
        }
        a = args(command="fetch")
        with patch(
            "src.config.get_active_keywords",
            return_value=[{"keyword": "ML", "active": True}],
        ):
            with patch(
                "src.network.arxiv.fetch_all", AsyncMock(return_value=([paper], 1, 0))
            ):
                with patch("src.agents.PaperScorer") as M:
                    mr = MagicMock()
                    mr.score.side_effect = Exception("Score failed")
                    M.return_value = mr
                    with (
                        patch("src.serve.generate_summary_html"),
                        patch("src.serve.generate_landing_html"),
                        patch("src.serve.generate_notes_index_html"),
                    ):
                        cmd_fetch(a, mock_settings, conn)
                        out = capsys.readouterr().out
                        assert "Fallback" in out

    def test_fetch_with_api_key_and_papers(self, conn, capsys):
        """有 api_key 且返回新论文（async 评分路径）"""
        from src.config import (AppConfig, ArxivConfig, LLMConfig,
                                NotificationConfig, ScoringConfig,
                                ServerConfig)

        s = AppConfig(
            llm=LLMConfig(api_key="sk-t"),
            arxiv=ArxivConfig(),
            server=ServerConfig(),
            notification=NotificationConfig(),
            scoring=ScoringConfig(),
        )
        from src.commands import cmd_fetch

        new = {
            "arxiv_id": "2401.00100",
            "version": 1,
            "title": "API Paper",
            "authors": "A",
            "abstract": "a",
            "url": "",
            "primary_category": "cs.CV",
            "categories": "cs.CV",
            "published": "2024-01-01",
            "arxiv_updated": "",
            "keyword_match": "t",
        }
        a = args(command="fetch")
        with patch(
            "src.config.get_active_keywords",
            return_value=[{"keyword": "ML", "active": True}],
        ):
            with patch(
                "src.network.arxiv.fetch_all", AsyncMock(return_value=([new], 1, 0))
            ):
                with (
                    patch("src.serve.generate_summary_html"),
                    patch("src.serve.generate_landing_html"),
                    patch("src.serve.generate_notes_index_html"),
                ):
                    cmd_fetch(a, s, conn)
                    out = capsys.readouterr().out
                    assert "完成" in out


class TestCmdMarkEdgeCases:
    def test_mark_ignore_skip_note(self, conn, mock_settings, capsys, sample_papers):
        """标记 ignore 不触发笔记生成"""
        from src.commands import cmd_mark

        with (
            patch("src.serve.renderer.generate_summary_html"),
            patch("src.serve.renderer.generate_notes_index_html"),
        ):
            cmd_mark(
                args(command="mark", arxiv_id="2401.00001", type="ignore"),
                mock_settings,
                conn,
            )
            out = capsys.readouterr().out
            assert "Ignored" in out


class TestRendererCoverage:
    def test_summary_no_keyword(self, temp_dir):
        """论文无 keyword_match 时的 summary"""
        from src.serve.renderer import generate_summary_html

        grouped = {
            "unmarked": [
                {
                    "arxiv_id": "2401.00001",
                    "title": "No KW",
                    "llm_remark": "browse",
                    "llm_score": 0.5,
                    "llm_reason": "R",
                    "keyword_match": "",
                    "published": "2024-01-01",
                    "authors": "A",
                    "user_mark": None,
                    "url": "",
                    "llm_summary": "S",
                }
            ],
            "marked": [],
            "lurk": [],
        }
        path = generate_summary_html(grouped, output_dir=temp_dir)
        assert path.exists()
        html = path.read_text(encoding="utf-8")
        assert "No KW" in html

    def test_notes_index_with_no_conn(self, temp_dir):
        """无 conn 参数时的 notes 生成"""
        from src.serve.renderer import generate_notes_index_html

        papers = [
            {
                "arxiv_id": "2401.00001",
                "title": "Test Paper",
                "authors": "A",
                "llm_remark": "useful",
                "llm_score": 0.7,
                "keyword_match": "ML",
                "published": "2024-01-01",
                "user_mark": "deep_read",
                "url": "",
                "note_short_title": "",
                "note_description": "",
                "abstract": "a",
            }
        ]
        with patch(
            "src.db.get_all_categories", return_value=[{"id": 1, "name": "精读"}]
        ):
            with patch(
                "src.db.get_note_categories", return_value=[{"id": 1, "name": "精读"}]
            ):
                path = generate_notes_index_html(papers, output_dir=temp_dir, conn=None)
                assert path.exists()

    def test_notes_index_thumbnail(self, temp_dir):
        """缩略图路径"""
        from src.serve.renderer import generate_notes_index_html

        notes_dir = temp_dir / "notes" / "2401.00001"
        fig_dir = notes_dir / "figures"
        fig_dir.mkdir(parents=True)
        (fig_dir / "fig1.png").write_bytes(b"PNG")
        papers = [
            {
                "arxiv_id": "2401.00001",
                "title": "With Fig",
                "authors": "A",
                "llm_remark": "useful",
                "llm_score": 0.7,
                "keyword_match": "ML",
                "published": "2024-01-01",
                "user_mark": "deep_read",
                "url": "",
                "note_short_title": "",
                "note_description": "",
                "abstract": "a",
            }
        ]
        with patch("src.db.get_all_categories", return_value=[]):
            with patch("src.db.get_note_categories", return_value=[]):
                path = generate_notes_index_html(papers, output_dir=temp_dir, conn=None)
                assert path.exists()


class TestCmdReMark:
    def test_remark_same_type(self, conn, mock_settings, capsys, sample_papers):
        """重复标记相同类型"""
        from src.commands import cmd_mark

        with patch("src.agents.note_agent.NoteAgent") as M:

            async def g(*a, **kw):
                return None

            M.generate_from_arxiv_id = g
            with (
                patch("src.serve.renderer.generate_summary_html"),
                patch("src.serve.renderer.generate_notes_index_html"),
            ):
                cmd_mark(
                    args(command="mark", arxiv_id="2401.00001", type="deep-read"),
                    mock_settings,
                    conn,
                )
            out = capsys.readouterr().out
            assert "Deep Read" in out

    def test_remark_change_type(self, conn, mock_settings, capsys, sample_papers):
        """从 deep_read 改为 ignore"""
        from src.commands import cmd_mark

        with (
            patch("src.serve.renderer.generate_summary_html"),
            patch("src.serve.renderer.generate_notes_index_html"),
        ):
            cmd_mark(
                args(command="mark", arxiv_id="2401.00001", type="ignore"),
                mock_settings,
                conn,
            )
            out = capsys.readouterr().out
            assert "Ignored" in out


class TestCmdNoteForce:
    def test_note_force_regenerate(self, conn, mock_settings, capsys, sample_papers):
        """note --force 强制重新生成"""
        from src.commands import cmd_note
        from src.config import OUTPUT_DIR

        note_path = OUTPUT_DIR / "notes" / "2401.00001" / "note.md"
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text("# Old Note")
        try:
            with patch("src.agents.note_agent.NoteAgent") as M:

                async def g(*a, **kw):
                    return None

                M.generate_from_arxiv_id = g
                with (
                    patch("os.startfile"),
                    patch("src.serve.renderer.generate_summary_html"),
                    patch("src.serve.renderer.generate_notes_index_html"),
                ):
                    cmd_note(
                        args(command="note", arxiv_id="2401.00001", force=True),
                        mock_settings,
                        conn,
                    )
                    out = capsys.readouterr().out
                    assert "Regenerating" in out
        finally:
            import shutil

            shutil.rmtree(str(note_path.parent), ignore_errors=True)

    def test_note_skip_without_force(self, conn, mock_settings, capsys, sample_papers):
        """有笔记但不用 --force 时跳过"""
        from src.commands import cmd_note
        from src.config import OUTPUT_DIR

        note_path = OUTPUT_DIR / "notes" / "2401.00001" / "note.md"
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text("# Existing Note")
        try:
            with (
                patch("os.startfile"),
                patch("src.serve.renderer.generate_summary_html"),
                patch("src.serve.renderer.generate_notes_index_html"),
            ):
                cmd_note(
                    args(command="note", arxiv_id="2401.00001"), mock_settings, conn
                )
                out = capsys.readouterr().out
                assert "already exists" in out
        finally:
            import shutil

            shutil.rmtree(str(note_path.parent), ignore_errors=True)
