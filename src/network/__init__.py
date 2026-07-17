"""
Paper Researcher 网络模块，提供网络通信接口

数据源扩展方式:
    1. 继承 ``base.BaseSource`` 实现抽象方法
    2. 通过 ``factory.register_source("名称", 子类)`` 注册
    3. 使用 ``factory.get_source(settings)`` 获取数据源实例
"""

from src.network.base import BaseSource
from src.network.factory import register_source, get_source

__all__ = [
    "arxiv",
    "BaseSource",
    "register_source",
    "get_source",
]
