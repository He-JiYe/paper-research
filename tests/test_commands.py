"""CLI 命令实现测试"""

import argparse
from unittest.mock import AsyncMock, MagicMock, patch


def make_args(**kwargs):
    defaults = {
        "keyword": None,
        "dry_run": False,
        "serve": False,
        "head": 10,
        "all": False,
        "arxiv_id": "2401.00001",
        "type": "deep-read",
        "mark": None,
        "status": None,
        "sort": "date",
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class TestCmdReview:
    def test_review_no_papers(self, conn, mock_settings, capsys):
        from src.commands import cmd_review

        args = make_args(command="review", head=10, all=False)
        cmd_review(args, mock_settings, conn)
        captured = capsys.readouterr()
        assert "待审核" in captured.out
        assert "暂无" in captured.out or "所有" in captured.out

    def test_review_with_papers(self, sample_papers, conn, mock_settings, capsys):
        from src.commands import cmd_review

        args = make_args(command="review", head=10, all=False)
        cmd_review(args, mock_settings, conn)
        captured = capsys.readouterr()
        assert "ArXiv ID" in captured.out
        assert "2401.00002" in captured.out

    def test_review_all(self, sample_papers, conn, mock_settings, capsys):
        from src.commands import cmd_review

        args = make_args(command="review", head=10, all=True)
        cmd_review(args, mock_settings, conn)
        captured = capsys.readouterr()
        assert "2401.00002" in captured.out


class TestCmdList:
    def test_list_empty(self, conn, mock_settings, capsys):
        from src.commands import cmd_list

        args = make_args(command="list", head=10, all=False)
        cmd_list(args, mock_settings, conn)
        captured = capsys.readouterr()
        assert "共" in captured.out

    def test_list_with_papers(self, sample_papers, conn, mock_settings, capsys):
        from src.commands import cmd_list

        args = make_args(command="list", head=10, all=False)
        cmd_list(args, mock_settings, conn)
        captured = capsys.readouterr()
        assert "2401.00001" in captured.out

    def test_list_by_keyword(self, sample_papers, conn, mock_settings, capsys):
        from src.commands import cmd_list

        args = make_args(
            command="list", keyword="test-time adaptation", head=10, all=False
        )
        cmd_list(args, mock_settings, conn)
        captured = capsys.readouterr()
        assert "2401.00001" in captured.out

    def test_list_by_mark(self, sample_papers, conn, mock_settings, capsys):
        from src.commands import cmd_list

        args = make_args(command="list", mark="deep_read", head=10, all=False)
        cmd_list(args, mock_settings, conn)
        captured = capsys.readouterr()
        assert "精读" in captured.out

    def test_list_all_flag(self, sample_papers, conn, mock_settings, capsys):
        from src.commands import cmd_list

        args = make_args(command="list", head=10, all=True)
        cmd_list(args, mock_settings, conn)
        captured = capsys.readouterr()
        assert "2401.00001" in captured.out


class TestCmdStatus:
    def test_status_empty(self, conn, mock_settings, capsys):
        from src.commands import cmd_status

        args = make_args(command="status")
        cmd_status(args, mock_settings, conn)
        captured = capsys.readouterr()
        assert "Stats" in captured.out or "Paper" in captured.out

    def test_status_with_data(self, sample_papers, conn, mock_settings, capsys):
        from src.commands import cmd_status

        args = make_args(command="status")
        cmd_status(args, mock_settings, conn)
        captured = capsys.readouterr()
        assert "Total" in captured.out or "total" in captured.out.lower()


class TestCmdNotify:
    def test_cmd_notify(self, conn, mock_settings, capsys):
        """cmd_notify 调用通知函数（dispatch 内使用 lazy import）"""
        from src.commands import cmd_notify

        args = make_args(command="notify")
        with (
            patch("src.notify.send_windows_toast") as mock_toast,
            patch("src.notify.send_email_if_configured") as mock_email,
        ):
            cmd_notify(args, mock_settings, conn)
            mock_toast.assert_called_once()
            mock_email.assert_called_once()


class TestCmdMark:
    def test_cmd_mark_paper_not_found_on_arxiv(self, conn, mock_settings, capsys):
        from src.commands import cmd_mark

        args = make_args(command="mark", arxiv_id="9999.99999", type="ignore")

        with patch("src.network.arxiv.fetch_by_ids", AsyncMock(return_value=[])):
            with patch("src.serve.renderer.generate_summary_html"):
                with patch("src.serve.renderer.generate_notes_index_html"):
                    cmd_mark(args, mock_settings, conn)
                    captured = capsys.readouterr()
                    assert "未找到" in captured.out

    def test_cmd_mark_ignore(self, conn, mock_settings, capsys, sample_papers):
        from src.commands import cmd_mark

        args = make_args(command="mark", arxiv_id="2401.00002", type="ignore")

        with patch("src.serve.renderer.generate_summary_html"):
            with patch("src.serve.renderer.generate_notes_index_html"):
                cmd_mark(args, mock_settings, conn)
                captured = capsys.readouterr()
                assert "Ignored" in captured.out

    def test_cmd_mark_deep_read_new_paper(self, conn, mock_settings, capsys):
        from src.commands import cmd_mark

        args = make_args(command="mark", arxiv_id="2401.00100", type="deep-read")

        mock_paper_data = [
            {
                "arxiv_id": "2401.00100",
                "version": 1,
                "title": "New Paper",
                "authors": "A",
                "abstract": "Abstract",
                "url": "",
                "primary_category": "cs.CV",
                "categories": "cs.CV",
                "published": "2024-01-01",
                "arxiv_updated": "",
                "keyword_match": "test",
                "fetch_date": "2024-07-01",
            }
        ]

        with patch(
            "src.network.arxiv.fetch_by_ids", AsyncMock(return_value=mock_paper_data)
        ):
            with patch("src.agents.PaperScorer") as MockScorer:
                mock_scorer = MagicMock()
                mock_scorer.score.return_value = MagicMock(
                    summary="Summary", remark="important", reason="Good", score=0.9
                )
                MockScorer.return_value = mock_scorer
                with patch("src.agents.note_agent.NoteAgent") as MockNoteAgent:

                    async def mock_generate(*a, **kw):
                        return None

                    MockNoteAgent.generate_from_arxiv_id = mock_generate
                    with patch("src.serve.renderer.generate_summary_html"):
                        with patch("src.serve.renderer.generate_notes_index_html"):
                            cmd_mark(args, mock_settings, conn)
                            captured = capsys.readouterr()
                            assert "已入库" in captured.out


class TestCmdFetch:
    def test_cmd_fetch_dry_run(self, conn, mock_settings, capsys):
        from src.commands import cmd_fetch

        args = make_args(command="fetch", keyword=None, dry_run=True, serve=False)

        with (
            patch(
                "src.config.get_active_keywords",
                return_value=[
                    {"keyword": "ML", "arxiv_cats": ["cs.LG"], "active": True}
                ],
            ),
            patch("src.network.arxiv.fetch_all", AsyncMock(return_value=([], 0, 0))),
        ):
            cmd_fetch(args, mock_settings, conn)
            captured = capsys.readouterr()
            assert "Dry-run" in captured.out

    def test_cmd_fetch_no_matching_keyword(self, conn, mock_settings, capsys):
        from src.commands import cmd_fetch

        args = make_args(
            command="fetch", keyword="nonexistent_keyword", dry_run=False, serve=False
        )

        with patch(
            "src.config.get_active_keywords",
            return_value=[{"keyword": "ML", "active": True}],
        ):
            cmd_fetch(args, mock_settings, conn)
            captured = capsys.readouterr()
            assert "未找到" in captured.out


class TestCmdNote:
    def test_cmd_note_paper_not_found_on_arxiv(self, conn, mock_settings, capsys):
        from src.commands import cmd_note

        args = make_args(command="note", arxiv_id="9999.99999")

        with patch("src.network.arxiv.fetch_by_ids", AsyncMock(return_value=[])):
            cmd_note(args, mock_settings, conn)
            captured = capsys.readouterr()
            assert "未找到" in captured.out


class TestCmdFetchFull:
    def test_cmd_fetch_with_new_papers(self, conn, mock_settings, capsys):
        """fetch 获取新论文并入库"""
        from src.commands import cmd_fetch

        args = make_args(command="fetch", keyword=None, dry_run=False, serve=False)

        mock_paper = {
            "arxiv_id": "2401.00100",
            "version": 1,
            "title": "New Paper",
            "authors": "A",
            "abstract": "Abstract",
            "url": "",
            "primary_category": "cs.CV",
            "categories": "cs.CV",
            "published": "2024-01-01",
            "arxiv_updated": "",
            "keyword_match": "ML",
            "fetch_date": "2024-07-01",
        }

        with (
            patch(
                "src.config.get_active_keywords",
                return_value=[
                    {"keyword": "ML", "arxiv_cats": ["cs.LG"], "active": True}
                ],
            ),
            patch(
                "src.network.arxiv.fetch_all",
                AsyncMock(return_value=([mock_paper], 1, 0)),
            ),
        ):
            with patch("src.agents.PaperScorer") as MockScorer:
                mock_scorer = MagicMock()
                mock_scorer.score.return_value = MagicMock(
                    summary="S", remark="useful", reason="R", score=0.7
                )
                MockScorer.return_value = mock_scorer
                with patch("src.serve.generate_summary_html"):
                    with patch("src.serve.generate_landing_html"):
                        with patch("src.serve.generate_notes_index_html"):
                            cmd_fetch(args, mock_settings, conn)
                            captured = capsys.readouterr()
                            assert "Paper Research" in captured.out

    def test_cmd_fetch_with_keyword_filter(self, conn, mock_settings, capsys):
        """fetch 指定关键词"""
        from src.commands import cmd_fetch

        args = make_args(command="fetch", keyword="ML", dry_run=True, serve=False)

        with patch(
            "src.config.get_active_keywords",
            return_value=[
                {"keyword": "ML", "active": True},
                {"keyword": "CV", "active": True},
            ],
        ):
            cmd_fetch(args, mock_settings, conn)
            captured = capsys.readouterr()
            assert "Dry-run" in captured.out


class TestCmdListEdgeCases:
    def test_list_with_sort_by_score(self, sample_papers, conn, mock_settings, capsys):
        from src.commands import cmd_list

        args = make_args(command="list", sort="score", head=10, all=False)
        cmd_list(args, mock_settings, conn)
        captured = capsys.readouterr()
        assert "2401.00001" in captured.out

    def test_list_empty_result(self, conn, mock_settings, capsys):
        from src.commands import cmd_list

        args = make_args(command="list", keyword="nonexistent", head=10, all=False)
        cmd_list(args, mock_settings, conn)
        captured = capsys.readouterr()
        assert "(" in captured.out
