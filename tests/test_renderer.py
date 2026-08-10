"""邮件渲染（notify.renderer）直接单测：主题/正文/Top-N 排序/None 容错/HTML 转义。"""

from src.notify.renderer import _escape, render_email_report


def _stats(**overrides) -> dict:
    base = {
        "total": 10,
        "pending": 5,
        "by_remark": {"important": 2, "useful": 3},
    }
    base.update(overrides)
    return base


def _paper(source_id, score, title="Test Paper") -> dict:
    return {
        "source": "arxiv",
        "source_id": source_id,
        "title": title,
        "url": f"https://arxiv.org/abs/{source_id}",
        "llm_remark": "important",
        "llm_score": score,
    }


class TestRenderEmailReport:
    def test_subject_contains_today_and_keywords(self):
        content = render_email_report(_stats(), [], ["WAM", "RL"], "http://127.0.0.1:8899")
        assert "Paper Research - " in content.subject
        assert "[WAM, RL]" in content.subject

    def test_body_renders_stats(self):
        content = render_email_report(
            _stats(total=10, pending=5, by_remark={"important": 2, "useful": 3}),
            [],
            ["kw"],
            "http://127.0.0.1:8899",
        )
        assert "10" in content.text  # total
        assert "5" in content.text  # pending
        assert "2" in content.text  # important
        assert "3" in content.text  # useful

    def test_empty_papers_renders_without_table(self):
        content = render_email_report(_stats(), [], ["kw"], "http://127.0.0.1:8899")
        assert content.html  # 仍有正文
        assert content.text

    def test_top_n_sorted_by_score_desc(self):
        papers = [
            _paper("a", 0.3, "Low"),
            _paper("b", 0.9, "High"),
            _paper("c", 0.6, "Mid"),
        ]
        content = render_email_report(_stats(), papers, ["kw"], "http://127.0.0.1:8899", top_n=3)
        html = content.html
        assert html.index("High") < html.index("Mid") < html.index("Low")

    def test_none_score_does_not_crash(self):
        """历史数据 llm_score 为 None 时排序不抛错，整封邮件仍能生成（回归 A2）。"""
        papers = [
            _paper("a", None, "Null Score"),
            _paper("b", 0.9, "Scored"),
        ]
        content = render_email_report(_stats(), papers, ["kw"], "http://127.0.0.1:8899")
        assert "Scored" in content.html
        assert "Null Score" in content.html

    def test_html_escaped(self):
        papers = [_paper("a", 0.5, '<script>alert("x")</script>')]
        content = render_email_report(_stats(), papers, ["kw"], "http://127.0.0.1:8899")
        assert "<script>" not in content.html
        assert "&lt;script&gt;" in content.html


class TestEscape:
    def test_escapes_special_chars(self):
        assert _escape('<a href="x">&</a>') == "&lt;a href=&quot;x&quot;&gt;&amp;&lt;/a&gt;"

    def test_none_becomes_empty(self):
        assert _escape(None) == ""

    def test_non_string_coerced(self):
        assert _escape(123) == "123"
