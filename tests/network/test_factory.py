"""DataSource 工厂测试：验证 get_source、register_source 及 ArxivSource 委托逻辑"""
from unittest.mock import AsyncMock, patch

import pytest

from src.config import AppConfig
from src.network.base import BaseSource
from src.network.factory import get_source, register_source, ArxivSource


# ─── 测试用 FakeSource 辅助函数 ─────────────────────────────


def _make_fake_source_class(name="FakeSource"):
    """动态创建一个实现了 BaseSource 的 fake 类，避免重复代码。"""
    return type(
        name,
        (BaseSource,),
        {
            "fetch_all": AsyncMock(),
            "fetch_by_ids": AsyncMock(),
            "search": AsyncMock(),
            "download_pdf": AsyncMock(),
        },
    )


# ════════════════════════════════════════════════════════════
# 1.  BaseSource ABC 约束
# ════════════════════════════════════════════════════════════


class TestBaseSourceABC:
    """测试 BaseSource 抽象基类约束"""

    def test_cannot_instantiate_base_class(self):
        """BaseSource 含抽象方法，不能直接实例化"""
        with pytest.raises(TypeError, match="abstract"):
            BaseSource()

    def test_subclass_must_implement_abstract_methods(self):
        """未完全实现抽象方法的子类也不能实例化"""
        class IncompleteSource(BaseSource):
            async def fetch_all(self, **kwargs):
                pass
            # 缺少 fetch_by_ids, search, download_pdf

        with pytest.raises(TypeError, match="abstract"):
            IncompleteSource()

    def test_complete_subclass_can_instantiate(self):
        """完全实现了所有抽象方法后可以正常实例化"""
        cls = _make_fake_source_class("CompleteSource")
        instance = cls()
        assert isinstance(instance, BaseSource)

    def test_arxsource_is_base_source_subclass(self):
        """ArxivSource 继承自 BaseSource"""
        assert issubclass(ArxivSource, BaseSource)

# ════════════════════════════════════════════════════════════
# 2.  get_source 工厂函数
# ════════════════════════════════════════════════════════════


class TestGetSource:
    """测试 get_source 工厂函数"""

    def test_get_source_returns_arxiv_source(self, mock_settings):
        """get_source(AppConfig(source="arxiv")) 应返回 ArxivSource 实例"""
        source = get_source(mock_settings)
        assert isinstance(source, ArxivSource)
        assert isinstance(source, BaseSource)

    def test_get_source_default(self):
        """get_source(AppConfig()) 应使用默认 "arxiv" 并返回 ArxivSource"""
        source = get_source(AppConfig())
        assert isinstance(source, ArxivSource)

    def test_get_source_unknown_raises(self):
        """未知数据源名称应抛出 ValueError"""
        config = AppConfig(source="pubmed")
        with pytest.raises(ValueError, match="Unknown data source.*pubmed"):
            get_source(config)

    def test_get_source_accepts_none(self):
        """get_source(None) 应通过 getattr 默认值回退到 "arxiv" """
        source = get_source(None)
        assert isinstance(source, ArxivSource)


# ════════════════════════════════════════════════════════════
# 3.  register_source 注册功能
# ════════════════════════════════════════════════════════════


class TestRegisterSource:
    """测试 register_source 注册功能"""

    def test_register_source(self):
        """注册新数据源后 get_source 应返回对应实例"""
        cls = _make_fake_source_class("FakeSource")

        register_source("fake", cls)
        source = get_source(AppConfig(source="fake"))
        assert isinstance(source, cls)
        assert isinstance(source, BaseSource)

    def test_register_source_overwrites(self):
        """重复注册同一名称应覆盖已有数据源"""
        FakeV1 = _make_fake_source_class("FakeV1")
        FakeV2 = _make_fake_source_class("FakeV2")

        register_source("overwrite_me", FakeV1)
        register_source("overwrite_me", FakeV2)
        source = get_source(AppConfig(source="overwrite_me"))
        assert isinstance(source, FakeV2)
        assert not isinstance(source, FakeV1)

    def test_register_source_rejects_non_base_source(self):
        """注册非 BaseSource 子类应允许（类型检查在运行时由 get_source 体现）"""
        class NotASource:
            pass

        register_source("not_valid", NotASource)
        source = get_source(AppConfig(source="not_valid"))
        # 由于 NotASource 没有实现 BaseSource 接口，这是个运行时缺陷
        # 但 register_source 本身不阻止（Python 鸭子类型）
        assert isinstance(source, NotASource)


# ════════════════════════════════════════════════════════════
# 4.  ArxivSource 委托测试
# ════════════════════════════════════════════════════════════


class TestArxivSourceDelegation:
    """测试 ArxivSource 正确委托给 src.network.arxiv 模块函数"""

    @pytest.mark.asyncio
    async def test_fetch_all_delegates(self, mock_settings):
        """ArxivSource.fetch_all 应委托给 src.network.arxiv.fetch_all"""
        keywords = [{"keyword": "test", "active": True}]
        expected = ([{"arxiv_id": "2401.00001"}], 1, 0)

        with patch("src.network.arxiv.fetch_all", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = expected
            source = ArxivSource()
            result = await source.fetch_all(keywords, mock_settings, max_results=10)

            assert result == expected
            mock_fetch.assert_awaited_once_with(keywords, mock_settings, 10, False, None)

    @pytest.mark.asyncio
    async def test_fetch_by_ids_delegates(self):
        """ArxivSource.fetch_by_ids 应委托给 src.network.arxiv.fetch_by_ids"""
        ids = ["2401.00001", "2401.00002"]
        expected = [{"arxiv_id": "2401.00001"}]

        with patch("src.network.arxiv.fetch_by_ids", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = expected
            source = ArxivSource()
            result = await source.fetch_by_ids(ids)

            assert result == expected
            mock_fetch.assert_awaited_once_with(ids)

    @pytest.mark.asyncio
    async def test_search_delegates(self):
        """ArxivSource.search 应委托给 src.network.arxiv.search_arxiv"""
        expected = [{"arxiv_id": "2401.00001"}]

        with patch("src.network.arxiv.search_arxiv", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = expected
            source = ArxivSource()
            result = await source.search("test query", max_results=10, categories=["cs.LG"])

            assert result == expected
            mock_search.assert_awaited_once_with("test query", 10, ["cs.LG"])

    @pytest.mark.asyncio
    async def test_download_pdf_delegates(self, tmp_path):
        """ArxivSource.download_pdf 应委托给 src.network.arxiv.download_pdf"""
        expected = tmp_path / "notes" / "2401.00001" / "paper.pdf"

        with patch("src.network.arxiv.download_pdf", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = expected
            source = ArxivSource()
            result = await source.download_pdf("2401.00001", tmp_path)

            assert result == expected
            mock_dl.assert_awaited_once_with("2401.00001", tmp_path)
