"""数据源子包：每个数据源一个模块，通过 ``REGISTRY.register`` 注册。

导入本包即触发各数据源模块的 ``@REGISTRY.*.register`` 装饰器执行注册。
新增数据源时在此处显式 import 对应模块即可。
"""

from src.network.source import arxiv
