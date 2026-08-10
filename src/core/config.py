"""配置数据模型：8 个 Pydantic 模型（config.yaml 的结构化表示）。

- 从 dataclass 迁移到 Pydantic：自动类型强转、未知字段忽略（``extra="ignore"``）；
- 仅依赖 core 层（FetchOptions 同处 core），保持零 src 依赖；
- ``SourceConfig.options`` 可持有解析后的 ``FetchOptions`` 实例（loader 经 registry 注入），
  或原始参数字典（未解析阶段）。
"""

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.core.models import KeywordItem


class _Base(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="ignore")


class LLMConfig(_Base):
    """LLM 评分配置（provider 决定走 OpenAI SDK 还是 ollama 原生接口）。"""

    provider: str = "deepseek"  # deepseek | ollama
    model: str = "deepseek-v4-flash"
    api_base: str = ""  # 空则由 build_provider 按 provider 回退默认端点（deepseek/ollama）
    api_key: str = ""  # ollama 本地部署无需 key
    temperature: float = 0.3
    max_tokens: int = 2000
    think: bool = False  # 推理模型（如 qwen3）是否开启思考；JSON 打分默认关，可配置
    max_concurrent: int = 1  # 批量评分并发数；本地 Ollama 串行打分，默认 1


class SourceConfig(_Base):
    """单个数据源的抓取配置。

    ``options`` 由 config.loader 按 ``source`` 注册名解析为对应 ``FetchOptions``
    子类（如 ``ArxivOptions``），携带该源专属参数；关键词为全源共享。

    YAML 里 ``source`` 之外的同级键（如 max_results/lookback_days）经
    ``_collect_options`` 收集进 ``options``（保持 fetch.sources[*] 逐源参数平铺写法）。
    """

    source: str = ""  # 必填：config.yaml 漏写时 loader 经 registry 快速失败，不静默当 arxiv
    options: Any = None  # 原始 dict 或 loader 解析后的 FetchOptions 实例

    @model_validator(mode="before")
    @classmethod
    def _collect_options(cls, data):
        """把 source 之外的同级键收进 options（config.yaml 逐源参数平铺写法）。"""
        if isinstance(data, dict) and "options" not in data:
            data["options"] = {k: v for k, v in data.items() if k != "source"}
        return data


class FetchConfig(_Base):
    """抓取配置：多数据源（每个 source 带自己的 Options 参数）。

    keywords 为全源共享（AppConfig 顶层，形式固定为 core.KeywordItem）。
    """

    sources: list[SourceConfig] = Field(default_factory=list)


class EmailConfig(_Base):
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 465
    username: str = ""
    password: str = ""
    from_addr: str = ""
    to_addr: str = ""


class SchedulerConfig(_Base):
    enabled: bool = True
    fetch_time: str = "08:30"
    catch_up_on_start: bool = True  # serve 启动时若今天错过抓取则自动补抓

    @field_validator("fetch_time")
    @classmethod
    def _validate_fetch_time(cls, v: str) -> str:
        """校验每日抓取时间必须是合法 HH:MM（小时 0-23、分钟 0-59），非法值加载即报错（防调度器静默死亡）。"""
        m = re.fullmatch(r"(\d{2}):(\d{2})", v)
        if not m:
            raise ValueError(f"fetch_time 必须是 HH:MM 格式，收到: {v!r}")
        hour, minute = int(m.group(1)), int(m.group(2))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"fetch_time 越界（小时 0-23，分钟 0-59），收到: {v!r}")
        return v


class ServerConfig(_Base):
    host: str = "127.0.0.1"
    port: int = 8899


class ZoteroConfig(_Base):
    api_key: str = ""
    library_id: str = ""
    library_type: str = "user"


class AppConfig(_Base):
    llm: LLMConfig = LLMConfig()
    fetch: FetchConfig = FetchConfig()
    zotero: ZoteroConfig = ZoteroConfig()
    keywords: list[KeywordItem] = Field(default_factory=list)
    notification: EmailConfig = EmailConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    server: ServerConfig = ServerConfig()
