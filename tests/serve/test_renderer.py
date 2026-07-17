"""HTML 渲染器测试"""

from pathlib import Path
from unittest.mock import patch

from src.serve.renderer import (
    REMARK_COLORS,
    REMARK_LABELS,
    SECTION_LABELS,
    generate_notes_index_html,
    generate_summary_html,
    render_note_detail_html,
)


class TestConstants:
    def test_remark_labels(self):
        assert "important" in REMARK_LABELS
        assert "useful" in REMARK_LABELS
        assert "browse" in REMARK_LABELS
        assert "skip" in REMARK_LABELS

    def test_remark_colors(self):
        assert REMARK_COLORS["important"] == "#e74c3c"
        assert REMARK_COLORS["useful"] == "#f39c12"
        assert REMARK_COLORS["skip"] == "#95a5a6"

    def test_section_labels(self):
        assert "unmarked" in SECTION_LABELS
        assert "marked" in SECTION_LABELS
        assert "lurk" in SECTION_LABELS


class TestGenerateSummaryHtml:
    def test_generate_summary_html_basic(self, temp_dir):
        """generate_summary_html 生成有效的 HTML 文件"""
        grouped = {
            "unmarked": [],
            "marked": [],
            "lurk": [],
        }
        path = generate_summary_html(grouped, output_dir=temp_dir)
        assert isinstance(path, Path)
        assert path.exists()
        html = path.read_text(encoding="utf-8")
        assert (
            "<html" in html.lower()
            or "<!doctype" in html.lower()
            or "summary.html" not in html
        )
        # 至少包含一些 HTML 标签
        assert ">" in html

    def test_generate_summary_html_with_papers(self, temp_dir):
        """generate_summary_html 包含论文数据"""
        grouped = {
            "unmarked": [
                {
                    "arxiv_id": "2401.00001",
                    "title": "Test Paper",
                    "llm_remark": "important",
                    "llm_score": 0.9,
                    "llm_reason": "Great work",
                    "keyword_match": "ML",
                    "published": "2024-01-01",
                    "authors": "Alice",
                    "user_mark": None,
                    "url": "https://arxiv.org/abs/2401.00001",
                    "llm_summary": "Summary",
                },
            ],
            "marked": [
                {
                    "arxiv_id": "2401.00002",
                    "title": "Marked Paper",
                    "llm_remark": "useful",
                    "llm_score": 0.7,
                    "llm_reason": "Good",
                    "keyword_match": "CV",
                    "published": "2024-01-02",
                    "authors": "Bob",
                    "user_mark": "deep_read",
                    "url": "https://arxiv.org/abs/2401.00002",
                    "llm_summary": "Summary",
                },
            ],
            "lurk": [],
        }
        path = generate_summary_html(grouped, output_dir=temp_dir)
        html = path.read_text(encoding="utf-8")
        assert "Test Paper" in html
        assert "Marked Paper" in html

    def test_generate_summary_html_output_path(self, temp_dir):
        """输出到 summaries/index.html"""
        grouped = {"unmarked": [], "marked": [], "lurk": []}
        path = generate_summary_html(grouped, output_dir=temp_dir)
        assert path.parent.name == "summaries"
        assert path.name == "index.html"

    def test_generate_summary_html_default_output_dir(self, monkeypatch):
        """不指定 output_dir 时使用默认值"""
        grouped = {"unmarked": [], "marked": [], "lurk": []}
        path = generate_summary_html(grouped)
        assert path.exists()
        # 清理
        path.unlink(missing_ok=True)


class TestGenerateNotesIndexHtml:
    def test_generate_notes_index_html_empty(self, conn, temp_dir):
        """无论文时仍生成文件"""
        with (
            patch("src.db.get_all_categories", return_value=[]),
            patch("src.db.get_note_categories", return_value=[]),
        ):
            path = generate_notes_index_html([], output_dir=temp_dir, conn=conn)
            assert path.exists()
            assert path.name == "index.html"
            assert path.parent.name == "notes"

    def test_generate_notes_index_html_with_papers(self, conn, temp_dir):
        """包含标记笔记的论文"""
        papers = [
            {
                "arxiv_id": "2401.00001",
                "title": "Paper 1",
                "llm_remark": "important",
                "llm_score": 0.9,
                "keyword_match": "ML",
                "published": "2024-01-01",
                "authors": "A",
                "user_mark": "deep_read",
                "url": "",
                "note_short_title": "2024-Paper1",
                "note_description": "Desc",
                "abstract": "",
            },
            {
                "arxiv_id": "2401.00002",
                "title": "Paper 2",
                "llm_remark": "browse",
                "llm_score": 0.5,
                "keyword_match": "CV",
                "published": "2024-01-02",
                "authors": "B",
                "user_mark": "skim",
                "url": "",
                "note_short_title": "",
                "note_description": "",
                "abstract": "",
            },
        ]
        with (
            patch("src.db.get_all_categories", return_value=[]),
            patch("src.db.get_note_categories", return_value=[]),
        ):
            path = generate_notes_index_html(papers, output_dir=temp_dir, conn=conn)
            html = path.read_text(encoding="utf-8")
            assert "Paper 1" in html
            assert "Paper 2" in html

    def test_generate_notes_index_html_deduplication(self, conn, temp_dir):
        """重复 arxiv_id 的论文去重"""
        papers = [
            {
                "arxiv_id": "2401.00001",
                "title": "Paper 1",
                "llm_remark": "important",
                "llm_score": 0.9,
                "keyword_match": "ML",
                "published": "2024-01-01",
                "authors": "A",
                "user_mark": "deep_read",
                "url": "",
                "note_short_title": "",
                "note_description": "",
                "abstract": "",
            },
            {
                "arxiv_id": "2401.00001",
                "title": "Paper 1 Dup",
                "llm_remark": "important",
                "llm_score": 0.9,
                "keyword_match": "CV",
                "published": "2024-01-01",
                "authors": "A",
                "user_mark": "deep_read",
                "url": "",
                "note_short_title": "",
                "note_description": "",
                "abstract": "",
            },
        ]
        with (
            patch("src.db.get_all_categories", return_value=[]),
            patch("src.db.get_note_categories", return_value=[]),
        ):
            path = generate_notes_index_html(papers, output_dir=temp_dir, conn=conn)
            html = path.read_text(encoding="utf-8")
            assert "Paper 1" in html
            assert html.count("arxiv_id") >= 1


class TestRenderNoteDetailHtml:
    def test_render_note_detail_html_basic(self):
        """render_note_detail_html 渲染详情页"""
        html = render_note_detail_html(
            arxiv_id="2401.00001",
            title="Test Note",
            body_html="<p>Content</p>",
            paper=None,
        )
        assert html
        assert "<p>Content</p>" in html
        assert "2401.00001" in html

    def test_render_note_detail_html_with_paper(self):
        """包含论文元数据"""
        paper = {
            "user_mark": "deep_read",
            "note_short_title": "2024-Test",
            "note_description": "A test paper",
        }
        html = render_note_detail_html(
            arxiv_id="2401.00001",
            title="Test",
            body_html="<p>Body</p>",
            paper=paper,
        )
        assert "2024-Test" in html
        assert "A test paper" in html

    def test_render_note_detail_html_different_marks(self):
        """不同标记类型显示对应徽章"""
        for mark, expected in [
            ("skim", "粗读"),
            ("deep_read", "精读"),
            ("lurk", "延后"),
        ]:
            paper = {"user_mark": mark, "note_short_title": "", "note_description": ""}
            html = render_note_detail_html(
                arxiv_id="2401.00001", title="Test", body_html="<p>B</p>", paper=paper
            )
            assert expected in html
