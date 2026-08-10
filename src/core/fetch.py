"""数据源抓取参数抽象基类（core 层数据模型，无 network 依赖）。

``FetchOptions`` 由各数据源子类化（如 ``ArxivOptions``），经 registry 按注册名
解析；从 network/base.py 移入 core，保证 core 无反向依赖。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, fields
from typing import Any, TypeVar

from src.core.models import KeywordItem

# 数据源原生搜索类型，由各子类指定
RawSearch = TypeVar("RawSearch")


@dataclass
class FetchOptions(ABC):
    """抓取参数基类（通用字段；每源子类扩展特有项）。

    扩展示例::

        @REGISTRY.options.register("pubmed")
        @dataclass
        class PubmedOptions(FetchOptions):
            retmax: int = 20

            def to_list(self) -> list[RawSearch]: ...

    由调用方通过 ``from_dict()`` 从字典构建并通过 ``to_list()`` 方法生成 RawSearch 列表。
    """

    keywords: list[KeywordItem] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FetchOptions":
        """从字典中自动匹配并设置字段值（未声明的键安全丢弃）。"""
        field_names = {f.name for f in fields(cls)}
        filtered_data = {k: v for k, v in data.items() if k in field_names}
        return cls(**filtered_data)

    @abstractmethod
    def to_list(self) -> list[RawSearch]:
        """把 dataclass 转成搜索列表。"""
        ...
