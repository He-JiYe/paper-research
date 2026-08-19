"""配置加载模块测试（Pydantic 模型 + loader 加载 + 派生助手）"""

import os

import src.config.loader as loader_mod
import yaml
from src.config.loader import _resolve_env_vars, load_settings
from src.config.settings import get_active_keywords
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
from src.paths import ROOT_DIR


class TestResolveEnvVars:
    def test_basic_substitution(self):
        os.environ["_TEST_VAR"] = "hello"
        assert _resolve_env_vars("${_TEST_VAR}") == "hello"
        os.environ.pop("_TEST_VAR", None)

    def test_unset_var_keeps_placeholder(self):
        val = _resolve_env_vars("${UNSET_VAR_XYZ}")
        assert val == "${UNSET_VAR_XYZ}"

    def test_no_var_no_change(self):
        assert _resolve_env_vars("plain text") == "plain text"

    def test_empty_string(self):
        assert _resolve_env_vars("") == ""

    def test_resolve_env_recursive(self):
        """${VAR} 在 dict/list 嵌套结构中也递归解析"""
        from src.config.loader import _resolve_env_vars_recursive

        os.environ["_TV"] = "x"
        assert _resolve_env_vars_recursive(
            {"a": "${_TV}", "b": ["${_TV}"], "c": {"d": "${_TV}"}}
        ) == {"a": "x", "b": ["x"], "c": {"d": "x"}}
        os.environ.pop("_TV", None)

    def test_resolve_env_recursive_leaf_passthrough(self):
        """非 str/dict/list 的叶子值原样返回"""
        from src.config.loader import _resolve_env_vars_recursive

        assert _resolve_env_vars_recursive(123) == 123
        assert _resolve_env_vars_recursive(None) is None


class TestPydanticModels:
    def test_llm_config_defaults(self):
        cfg = LLMConfig()
        assert cfg.provider == "deepseek"
        assert cfg.model == "deepseek-v4-flash"
        assert cfg.temperature == 0.3
        assert cfg.max_tokens == 2000

    def test_llm_config_provider_ollama(self):
        cfg = LLMConfig(provider="ollama", api_base="http://localhost:11434")
        assert cfg.provider == "ollama"
        assert cfg.api_base == "http://localhost:11434"

    def test_fetch_config_defaults(self):
        cfg = FetchConfig()
        assert cfg.sources == []  # 未配置任何数据源

    def test_source_config_holds_resolved_options(self):
        from src.network.source.arxiv import ArxivOptions

        sc = SourceConfig(source="arxiv", options=ArxivOptions(max_results=10))
        assert sc.source == "arxiv"
        assert isinstance(sc.options, ArxivOptions)
        assert sc.options.max_results == 10

    def test_source_config_collects_flat_params(self):
        """config.yaml 平铺写法（source 外同级键）→ 收集进 options dict"""
        sc = SourceConfig.model_validate(
            {"source": "arxiv", "max_results": 10, "lookback_days": 3, "sort_by": "submittedDate"}
        )
        assert sc.source == "arxiv"
        assert sc.options == {"max_results": 10, "lookback_days": 3, "sort_by": "submittedDate"}

    def test_server_config_defaults(self):
        cfg = ServerConfig()
        assert cfg.host == "127.0.0.1"
        assert cfg.port == 8899

    def test_server_config_type_coercion(self):
        """Pydantic 自动把字符串 port 强转为 int"""
        cfg = ServerConfig(port="8899")
        assert cfg.port == 8899
        assert isinstance(cfg.port, int)

    def test_email_config_defaults(self):
        cfg = EmailConfig()
        assert cfg.enabled is False
        assert cfg.smtp_port == 465

    def test_scheduler_config_defaults(self):
        cfg = SchedulerConfig()
        assert cfg.enabled is True
        assert cfg.fetch_time == "08:30"

    def test_scheduler_fetch_time_rejects_bad_format(self):
        """fetch_time 必须是 HH:MM 且取值合法（小时 0-23、分钟 0-59）；非法值加载即报错。"""
        import pytest

        with pytest.raises(ValueError):
            SchedulerConfig(fetch_time="09:00:00")
        with pytest.raises(ValueError):
            SchedulerConfig(fetch_time="not-a-time")
        with pytest.raises(ValueError):
            SchedulerConfig(fetch_time="99:99")  # 小时越界（曾可静默通过校验并压垮调度器）
        with pytest.raises(ValueError):
            SchedulerConfig(fetch_time="12:60")  # 分钟越界

    def test_keyword_entry_defaults(self):
        kw = KeywordItem(keyword="test")
        assert kw.keyword == "test"
        assert kw.categories == []
        assert kw.active is True

    def test_app_config_defaults(self):
        cfg = AppConfig()
        assert isinstance(cfg.llm, LLMConfig)
        assert isinstance(cfg.fetch, FetchConfig)
        assert isinstance(cfg.notification, EmailConfig)
        assert isinstance(cfg.server, ServerConfig)

    def test_llm_config_custom(self):
        cfg = LLMConfig(
            provider="openai",
            model="gpt-4",
            api_base="https://api.openai.com",
            api_key="sk-test",
            temperature=0.7,
            max_tokens=4000,
        )
        assert cfg.provider == "openai"
        assert cfg.api_key == "sk-test"


class TestLoadSettings:
    def test_load_settings_returns_appconfig(self):
        """load_settings 从 config.yaml 加载并返回 AppConfig"""
        config = load_settings()
        assert isinstance(config, AppConfig)
        assert isinstance(config.llm, LLMConfig)
        assert isinstance(config.fetch, FetchConfig)
        assert isinstance(config.server, ServerConfig)
        assert isinstance(config.notification, EmailConfig)

    def test_load_zotero_config(self):
        """zotero 配置从 config.yaml 正确加载"""
        config = load_settings()
        # config.yaml 中 zotero 段已配置实际值
        assert config.zotero.api_key != ""
        assert config.zotero.library_id != ""
        assert config.zotero.library_type == "user"

    def test_env_var_resolve_direct(self, monkeypatch):
        """_resolve_env_vars 单独测试（不依赖 config.yaml）"""
        monkeypatch.setenv("_TEST_KEY", "resolved-value")
        assert _resolve_env_vars("${_TEST_KEY}") == "resolved-value"

    def test_save_config_raw_preserves_env_placeholder(self, tmp_path, monkeypatch):
        """save_config_raw 原样写盘（保留 ${ENV} 字面量，不解析为明文密钥）。"""
        target = tmp_path / "config.yaml"
        monkeypatch.setattr(loader_mod, "CONFIG_PATH", target)
        cfg = {"llm": {"api_key": "${DEEPSEEK_API_KEY}"}, "keywords": []}
        loader_mod.save_config_raw(cfg)
        saved = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert saved == cfg
        assert saved["llm"]["api_key"] == "${DEEPSEEK_API_KEY}"

    def test_load_settings_has_configured_values(self):
        """从真实配置文件中读取的值"""
        config = load_settings()
        assert config.server.host == "127.0.0.1"
        assert config.server.port == 8899

    def test_load_keywords_from_yaml(self):
        """关键词从 config.yaml 的 keywords 字段加载"""
        config = load_settings()
        assert len(config.keywords) > 0
        kw = config.keywords[0]
        assert isinstance(kw, KeywordItem)
        assert kw.keyword
        assert isinstance(kw.active, bool)

    def test_load_scheduler_config(self):
        """scheduler 配置正确加载"""
        config = load_settings()
        assert isinstance(config.scheduler, SchedulerConfig)
        assert config.scheduler.fetch_time == "08:30"

    def test_load_sources_resolved_via_registry(self):
        """fetch.sources 通过 registry 解析为对应 Options dataclass"""
        config = load_settings()
        assert len(config.fetch.sources) > 0
        for sc in config.fetch.sources:
            assert sc.source
            # options 是注册表中对应源的 FetchOptions 子类实例
            from src.network import REGISTRY

            assert isinstance(sc.options, REGISTRY.options.get(sc.source))


class TestKeywords:
    def test_get_active_keywords(self, monkeypatch):
        """get_active_keywords 只返回 active=True 的"""
        # 注入测试用 keywords
        mock_keywords = [
            KeywordItem(keyword="ML", active=True),
            KeywordItem(keyword="CV", active=False),
            KeywordItem(keyword="NLP", active=True),
        ]
        monkeypatch.setattr(
            "src.config.settings.load_settings",
            lambda: AppConfig(
                llm=LLMConfig(),
                fetch=FetchConfig(),
                keywords=mock_keywords,
                notification=EmailConfig(),
                scheduler=SchedulerConfig(enabled=False),
                server=ServerConfig(),
            ),
        )

        active = get_active_keywords()
        assert len(active) == 2
        assert all(kw.active for kw in active)
        assert active[0].keyword == "ML"

    def test_get_active_keywords_no_config(self, monkeypatch):
        """YAML 中无 keywords 时返回空列表"""

        def _mock_load():
            return AppConfig(
                llm=LLMConfig(),
                fetch=FetchConfig(),
                keywords=[],
                notification=EmailConfig(),
                scheduler=SchedulerConfig(enabled=False),
                server=ServerConfig(),
            )

        monkeypatch.setattr("src.config.settings.load_settings", _mock_load)
        assert get_active_keywords() == []


class TestPaths:
    def test_root_paths_defined(self):
        """ROOT_DIR 等常量已定义"""
        assert ROOT_DIR is not None
        assert ROOT_DIR.exists()
