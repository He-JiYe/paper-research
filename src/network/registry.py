"""多层注册表：数据源（sources）与抓取参数（options）按注册名解析。

- ``LayerRegistry``：单层 ``dict[str, type]``，支持函数式与装饰器式注册；
- ``Registry``：多层注册表，靠 ``__getattr__`` 惰性建层；
- 全局单例 ``REGISTRY``：``REGISTRY.sources.get(name)`` / ``REGISTRY.options.get(name)``。
"""


class LayerRegistry:
    """单层注册表。"""

    def __init__(self) -> None:
        self._name: str = ""
        self._sources: dict[str, type] = {}

    def register(self, name: str):
        """注册装饰器：``@REGISTRY.sources.register("pubmed")``。

        函数式调用（``register(name, cls)``）无生产使用方，已移除——
        动态类注册用 ``REGISTRY.sources.register(name)(cls)`` 等价表达。
        """

        def decorator(source_cls: type) -> type:
            self._sources[name] = source_cls
            return source_cls

        return decorator

    def get(self, name: str) -> type:
        """根据配置获取注册类。

        Args:
            name: 注册名称

        Returns:
            注册的类型

        Raises:
            ValueError: 未知的注册名称
        """
        cls = self._sources.get(name)
        if cls is None:
            raise ValueError(
                f"Unknown {self._name} registration: {name!r}. "
                f"Available: {list(self._sources.keys())}"
            )
        return cls


class Registry:
    """多层注册器。"""

    def __init__(self) -> None:
        # 合法层名白名单：拼写错误（如 REGISTRY.soures）直接报错而非静默新建空层
        self._known_layers = {"sources", "options"}
        self._layers: dict[str, LayerRegistry] = {}

    def __getattr__(self, name: str) -> LayerRegistry:
        """通过属性访问各层注册表（仅允许已知层名）。"""
        if name.startswith("_"):
            raise AttributeError(name)
        if name not in self._known_layers:
            raise AttributeError(
                f"Unknown registry layer: {name!r}（可用层: {sorted(self._known_layers)}）"
            )

        if name not in self._layers:
            self._layers[name] = LayerRegistry()
            self._layers[name]._name = name
        return self._layers[name]


REGISTRY = Registry()
