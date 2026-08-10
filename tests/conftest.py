"""共享测试 fixtures"""

from pathlib import Path

import pytest
from src.core.config import (
    AppConfig,
    EmailConfig,
    FetchConfig,
    LLMConfig,
    SchedulerConfig,
    ServerConfig,
    SourceConfig,
)
from src.core.models import KeywordItem
from src.network.source.arxiv import ArxivOptions


def _arxiv_options(**kw) -> ArxivOptions:
    """构造 ArxivOptions（过滤默认值，避免 keyword 字段被填）。"""
    return ArxivOptions(**kw)


@pytest.fixture
def sample_papers() -> list[dict]:
    """样本论文数据"""
    return [
        {
            "arxiv_id": "2401.00001",
            "version": 1,
            "title": "Test-Time Adaptation with Transformers",
            "authors": "Alice Zhang, Bob Li",
            "abstract": "We propose a novel method for test-time adaptation using transformer architectures.",
            "url": "https://arxiv.org/abs/2401.00001",
            "primary_category": "cs.LG",
            "categories": "cs.LG, cs.CV",
            "published": "2024-01-01",
            "arxiv_updated": "2024-01-05T12:00:00Z",
            "keyword_match": "test-time adaptation",
        },
        {
            "arxiv_id": "2401.00002",
            "version": 1,
            "title": "Out-of-Distribution Detection via Energy Scoring",
            "authors": "Chen Wang, Dan Liu",
            "abstract": "Energy-based methods for out-of-distribution detection in deep neural networks.",
            "url": "https://arxiv.org/abs/2401.00002",
            "primary_category": "cs.LG",
            "categories": "cs.LG",
            "published": "2024-01-02",
            "arxiv_updated": "2024-01-06T12:00:00Z",
            "keyword_match": "out-of-distribution detection",
        },
        {
            "arxiv_id": "2401.00003",
            "version": 2,
            "title": "Domain Generalization: A Survey",
            "authors": "Eve Zhao",
            "abstract": "A comprehensive survey of domain generalization methods.",
            "url": "https://arxiv.org/abs/2401.00003",
            "primary_category": "cs.CV",
            "categories": "cs.CV, cs.LG",
            "published": "2024-01-03",
            "arxiv_updated": "2024-01-10T12:00:00Z",
            "keyword_match": "domain generalization",
        },
    ]


@pytest.fixture
def sample_atom_xml() -> str:
    """样本 Arxiv Atom XML 响应"""
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom"'
        '      xmlns:arxiv="http://arxiv.org/schemas/atom"'
        '      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'
        "  <entry>"
        "    <id>http://arxiv.org/abs/2401.00001v1</id>"
        "    <title>Test-Time Adaptation with Transformers</title>"
        "    <summary>We propose a novel method for test-time adaptation.</summary>"
        "    <author><name>Alice Zhang</name></author>"
        "    <author><name>Bob Li</name></author>"
        "    <published>2024-01-01T00:00:00Z</published>"
        "    <updated>2024-01-05T12:00:00Z</updated>"
        '    <arxiv:primary_category scheme="http://arxiv.org/schemas/atom" term="cs.LG"/>'
        '    <category scheme="http://arxiv.org/schemas/atom" term="cs.LG"/>'
        '    <category scheme="http://arxiv.org/schemas/atom" term="cs.CV"/>'
        "  </entry>"
        "  <entry>"
        "    <id>http://arxiv.org/abs/2401.00002v1</id>"
        "    <title>Out-of-Distribution Detection via Energy Scoring</title>"
        "    <summary>Energy-based methods for OOD detection.</summary>"
        "    <author><name>Chen Wang</name></author>"
        "    <published>2024-01-02T00:00:00Z</published>"
        "    <updated>2024-01-06T12:00:00Z</updated>"
        '    <arxiv:primary_category scheme="http://arxiv.org/schemas/atom" term="cs.LG"/>'
        '    <category scheme="http://arxiv.org/schemas/atom" term="cs.LG"/>'
        "  </entry>"
        "</feed>"
    )


@pytest.fixture
def mock_settings() -> AppConfig:
    """Mock AppConfig 对象（新 YAML 配置结构）"""
    return AppConfig(
        llm=LLMConfig(
            provider="deepseek",
            model="deepseek-v4-flash",
            api_base="https://api.deepseek.com",
            api_key="",
            temperature=0.3,
            max_tokens=2000,
        ),
        fetch=FetchConfig(
            sources=[
                SourceConfig(
                    source="arxiv",
                    options=_arxiv_options(
                        max_results=50, lookback_days=7, sort_by="lastUpdatedDate"
                    ),
                )
            ]
        ),
        keywords=[
            KeywordItem(keyword="test-time adaptation", categories=["cs.CV", "cs.LG"], active=True),
            KeywordItem(keyword="out-of-distribution detection", categories=["cs.LG"], active=True),
        ],
        notification=EmailConfig(
            enabled=False,
            smtp_host="smtp.qq.com",
            smtp_port=465,
            username="test@qq.com",
            password="",
            from_addr="test@qq.com",
            to_addr="test@qq.com",
        ),
        scheduler=SchedulerConfig(enabled=False, fetch_time="09:00"),
        server=ServerConfig(host="127.0.0.1", port=8899),
    )


@pytest.fixture
def active_keywords() -> list[KeywordItem]:
    """活跃关键词列表"""
    return [
        KeywordItem(keyword="test-time adaptation", categories=["cs.CV", "cs.LG"], active=True),
        KeywordItem(keyword="out-of-distribution detection", categories=["cs.LG"], active=True),
    ]


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    """临时目录"""
    return tmp_path


@pytest.fixture(autouse=True)
def _isolate_examples_dir(tmp_path, monkeypatch):
    """把 few-shot 示例目录重定向到临时目录，避免测试污染真实 examples/"""
    monkeypatch.setattr("src.scorer.prompt.EXAMPLES_DIR", tmp_path / "examples")
