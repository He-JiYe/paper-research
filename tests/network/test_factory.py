"""DataSource 注册器测试：验证 REGISTRY.sources/options 及 ArxivSource 注册"""

from unittest.mock import AsyncMock

import pytest
from src.core.fetch import FetchOptions
from src.network import REGISTRY
from src.network.base import BaseSource
from src.network.source.arxiv import ArxivOptions, ArxivSource

# ─── 测试用 FakeSource 辅助函数 ─────────────────────────────


def _make_fake_source_class(name="FakeSource"):
    """动态创建一个实现了 BaseSource 抽象方法的 fake 类。"""
    return type(
        name,
        (BaseSource,),
        {
            "source_name": property(lambda self: "fake"),
            "adapt": AsyncMock(return_value=[]),
            "_fetch": AsyncMock(return_value=[]),
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
            @property
            def source_name(self) -> str:
                return "incomplete"

            # 缺少 adapt, _fetch

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
# 2.  REGISTRY.sources 取类
# ════════════════════════════════════════════════════════════


class TestGetSource:
    """测试按名称从注册表取数据源类并实例化"""

    def test_get_source_returns_arxiv_source(self):
        """REGISTRY.sources.get('arxiv') 应返回 ArxivSource 类"""
        cls = REGISTRY.sources.get("arxiv")
        assert cls is ArxivSource
        assert isinstance(cls(), BaseSource)

    def test_get_source_unknown_raises(self):
        """未知数据源名称应抛出 ValueError"""
        with pytest.raises(ValueError, match="Unknown.*pubmed"):
            REGISTRY.sources.get("pubmed")


# ════════════════════════════════════════════════════════════
# 3.  注册功能
# ════════════════════════════════════════════════════════════


class TestRegisterSource:
    """测试 REGISTRY.sources.register 注册功能"""

    def test_register_source(self):
        """注册新数据源后 get 应返回对应类"""
        cls = _make_fake_source_class("FakeSource")
        REGISTRY.sources.register("fake")(cls)
        assert REGISTRY.sources.get("fake") is cls

    def test_register_source_overwrites(self):
        """重复注册同一名称应覆盖已有数据源"""
        FakeV1 = _make_fake_source_class("FakeV1")
        FakeV2 = _make_fake_source_class("FakeV2")
        REGISTRY.sources.register("overwrite_me")(FakeV1)
        REGISTRY.sources.register("overwrite_me")(FakeV2)
        assert REGISTRY.sources.get("overwrite_me") is FakeV2
        assert REGISTRY.sources.get("overwrite_me") is not FakeV1

    def test_register_source_rejects_non_base_source(self):
        """注册非 BaseSource 子类应允许（类型检查运行时体现）"""

        class NotASource:
            pass

        REGISTRY.sources.register("not_valid")(NotASource)
        assert REGISTRY.sources.get("not_valid") is NotASource


# ════════════════════════════════════════════════════════════
# 4.  装饰器式注册
# ════════════════════════════════════════════════════════════


class TestRegisterSourceDecorator:
    """测试 @REGISTRY.sources.register("name") 装饰器式注册"""

    def test_decorator_registers_and_returns_class(self):
        @REGISTRY.sources.register("decorated_source")
        class DecoratedSource(BaseSource):
            @property
            def source_name(self) -> str:
                return "decorated_source"

            async def adapt(self, items):
                return []

            async def _fetch(self, keyword, options):
                return []

        assert REGISTRY.sources.get("decorated_source") is DecoratedSource
        assert issubclass(DecoratedSource, BaseSource)


# ════════════════════════════════════════════════════════════
# 5.  Options 注册（每个数据源可配自己的 Options 类型）
# ════════════════════════════════════════════════════════════


class TestOptionsRegistry:
    """测试数据源与其 Options 类型的绑定"""

    def test_arxiv_options_cls_registered(self):
        """arxiv 应注册 ArxivOptions"""
        assert REGISTRY.options.get("arxiv") is ArxivOptions

    def test_arxiv_options_is_fetch_options_subclass(self):
        """ArxivOptions 应继承基类 FetchOptions"""
        assert issubclass(ArxivOptions, FetchOptions)

    def test_unregistered_name_raises(self):
        """未注册 Options 的数据源名称应抛出 ValueError"""
        with pytest.raises(ValueError, match="Unknown.*no_such_source"):
            REGISTRY.options.get("no_such_source")

    def test_from_dict_builds_arxiv_options(self):
        """ArxivOptions.from_dict 从 dict 构建，未声明字段安全丢弃"""
        opts = ArxivOptions.from_dict(
            {
                "keywords": [{"keyword": "k", "categories": ["cs.LG"], "active": True}],
                "max_results": 99,
                "sort_by": "submittedDate",  # 时间排序/relevance 均可带 lookback_days>0
                "lookback_days": 7,
                "skip_ids": {"x"},
                "unrelated": 1,
            }
        )
        assert opts.max_results == 99
        assert opts.lookback_days == 7
        assert opts.skip_ids == {"x"}
        assert not hasattr(opts, "unrelated")
