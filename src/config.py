"""配置加载模块：读取 config.yaml"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# 项目根目录
ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT_DIR / "config"
DATA_DIR = ROOT_DIR / "data"
OUTPUT_DIR = ROOT_DIR / "output"

_CONFIG_PATH = CONFIG_DIR / "config.yaml"


# ─── Dataclass 定义 ─────────────────────────────────────────


@dataclass
class LLMConfig:
    provider: str = "deepseek"
    model: str = "deepseek-v4-flash"
    api_base: str = "https://api.deepseek.com"
    api_key: str = ""
    temperature: float = 0.3
    max_tokens: int = 2000


@dataclass
class FetchConfig:
    source: str = "arxiv"
    max_concurrent_requests: int = 5
    lookback_days: int = 3
    max_results: int = 20
    sort_by: str = "lastUpdatedDate"


@dataclass
class KeywordEntry:
    keyword: str
    arxiv_cats: list[str] | None = None
    active: bool = True


@dataclass
class EmailConfig:
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 465
    username: str = ""
    password: str = ""
    from_addr: str = ""
    to_addr: str = ""


@dataclass
class SchedulerConfig:
    enabled: bool = True
    fetch_time: str = "09:00"
    timezone: str = "Asia/Shanghai"
    catch_up_on_start: bool = True  # serve 启动时若今天错过抓取则自动补抓


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8899


@dataclass
class ZoteroConfig:
    api_key: str = ""
    library_id: str = ""
    library_type: str = "user"


@dataclass
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    fetch: FetchConfig = field(default_factory=FetchConfig)
    zotero: ZoteroConfig = field(default_factory=ZoteroConfig)
    keywords: list[KeywordEntry] = field(default_factory=list)
    notification: EmailConfig = field(default_factory=EmailConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    server: ServerConfig = field(default_factory=ServerConfig)


# ─── 环境变量替换 ───────────────────────────────────────────


_ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


def _resolve_env_vars(value: str) -> str:
    """将 ${VAR_NAME} 替换为环境变量值，未设置则保留原样"""

    def _replace(m: re.Match) -> str:
        var_name = m.group(1)
        return os.environ.get(var_name, m.group(0))

    return _ENV_VAR_PATTERN.sub(_replace, value)


def _resolve_env_vars_recursive(obj):
    """递归处理所有字符串值中的 ${VAR} 占位符"""
    if isinstance(obj, str):
        return _resolve_env_vars(obj)
    if isinstance(obj, dict):
        return {k: _resolve_env_vars_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env_vars_recursive(item) for item in obj]
    return obj


# ─── YAML 加载 ──────────────────────────────────────────────


def _load_yaml() -> dict:
    """加载 config.yaml，文件不存在则返回空字典"""
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return _resolve_env_vars_recursive(raw)
    return {}


# ─── 构建配置对象 ────────────────────────────────────────────


def load_settings() -> AppConfig:
    """加载 config.yaml 并返回 AppConfig 对象"""
    data = _load_yaml()

    # LLM
    llm_data = data.get("llm", {})
    llm = LLMConfig(
        provider=llm_data.get("provider", "deepseek"),
        model=llm_data.get("model", "deepseek-v4-flash"),
        api_base=llm_data.get("api_base", "https://api.deepseek.com"),
        api_key=llm_data.get("api_key", ""),
        temperature=llm_data.get("temperature", 0.3),
        max_tokens=llm_data.get("max_tokens", 2000),
    )

    # Fetch
    fetch_data = data.get("fetch", {})
    fetch = FetchConfig(
        source=fetch_data.get("source", "arxiv"),
        max_concurrent_requests=fetch_data.get("max_concurrent_requests", 5),
        lookback_days=fetch_data.get("lookback_days", 3),
        max_results=fetch_data.get("max_results", 20),
        sort_by=fetch_data.get("sort_by", "lastUpdatedDate"),
    )

    # Keywords
    keywords_raw = data.get("keywords", [])
    keywords = [
        KeywordEntry(
            keyword=kw["keyword"],
            arxiv_cats=kw.get("arxiv_cats") or [],
            active=kw.get("active", True),
        )
        for kw in keywords_raw
        if "keyword" in kw
    ]

    # Email notification
    email_data = data.get("notification", {}).get("email", {})
    email = EmailConfig(
        enabled=email_data.get("enabled", False),
        smtp_host=email_data.get("smtp_host", ""),
        smtp_port=email_data.get("smtp_port", 465),
        username=email_data.get("username", ""),
        password=email_data.get("password", ""),
        from_addr=email_data.get("from", ""),
        to_addr=email_data.get("to", ""),
    )

    # Scheduler
    sched_data = data.get("scheduler", {})
    scheduler = SchedulerConfig(
        enabled=sched_data.get("enabled", True),
        fetch_time=sched_data.get("fetch_time", "09:00"),
        timezone=sched_data.get("timezone", "Asia/Shanghai"),
        catch_up_on_start=sched_data.get("catch_up_on_start", True),
    )

    # Zotero
    zotero_data = data.get("zotero", {})
    zotero = ZoteroConfig(
        api_key=zotero_data.get("api_key", ""),
        library_id=str(zotero_data.get("library_id", "")),
        library_type=zotero_data.get("library_type", "user"),
    )

    # Server
    server_data = data.get("server", {})
    server = ServerConfig(
        host=server_data.get("host", "127.0.0.1"),
        port=server_data.get("port", 8899),
    )

    return AppConfig(
        llm=llm,
        fetch=fetch,
        zotero=zotero,
        keywords=keywords,
        notification=email,
        scheduler=scheduler,
        server=server,
    )


# ─── 便捷函数 ────────────────────────────────────────────────


def get_active_keywords() -> list[KeywordEntry]:
    """获取所有活跃的关键词"""
    cfg = load_settings()
    return [kw for kw in cfg.keywords if kw.active]


def get_data_dir() -> str:
    """获取数据目录路径（用于临时存储等）"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return str(DATA_DIR)


# 向下兼容别名
get_db_path = get_data_dir


def get_output_dir() -> Path:
    """获取输出目录"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR
