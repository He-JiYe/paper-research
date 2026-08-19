"""ChatProvider 抽象层测试：OpenAIProvider / OllamaProvider / build_provider。"""

from unittest.mock import MagicMock, patch

from src.core.config import LLMConfig
from src.scorer.provider import OllamaProvider, OpenAIProvider, build_provider


def _openai(api_key: str = "sk-test") -> OpenAIProvider:
    return OpenAIProvider(model="m", api_base="http://api.test", api_key=api_key)


def _ollama() -> OllamaProvider:
    p = OllamaProvider(model="llama3", api_base="http://localhost:11434")
    p._httpx = MagicMock()  # 替换真实 httpx，不触网
    return p


# ─── OpenAIProvider ──────────────────────────────────────


def test_openai_chat_success():
    p = _openai()
    resp = MagicMock()
    resp.choices[0].message.content = "ok"
    with patch("src.scorer.provider.OpenAIProvider._client", return_value=MagicMock()) as client:
        client.return_value.chat.completions.create.return_value = resp
        assert p.chat("s", "u") == "ok"


def test_openai_chat_failure_returns_none():
    p = _openai()
    with patch("src.scorer.provider.OpenAIProvider._client", side_effect=RuntimeError("boom")):
        assert p.chat("s", "u") is None


def test_openai_chat_no_key_returns_none():
    p = _openai(api_key="")
    assert p.chat("s", "u") is None


def test_openai_chat_json_mode_sends_response_format():
    """json_mode=True 时请求携带 response_format={"type":"json_object"}（DeepSeek JSON Output）"""
    p = OpenAIProvider(
        model="m", api_base="https://api.deepseek.com", api_key="sk-test", json_mode=True
    )
    resp = MagicMock()
    resp.choices[0].message.content = '{"summary": "ok"}'
    with patch("src.scorer.provider.OpenAIProvider._client", return_value=MagicMock()) as client:
        client.return_value.chat.completions.create.return_value = resp
        assert p.chat("s", "u") == '{"summary": "ok"}'
    kwargs = client.return_value.chat.completions.create.call_args.kwargs
    assert kwargs["response_format"] == {"type": "json_object"}


def test_openai_chat_default_no_response_format():
    """默认 json_mode=False 时不携带 response_format（兼容不支持该参数的端点）"""
    p = _openai()
    resp = MagicMock()
    resp.choices[0].message.content = "ok"
    with patch("src.scorer.provider.OpenAIProvider._client", return_value=MagicMock()) as client:
        client.return_value.chat.completions.create.return_value = resp
        p.chat("s", "u")
    assert "response_format" not in client.return_value.chat.completions.create.call_args.kwargs


def test_openai_check_success():
    p = _openai()
    with patch("src.scorer.provider.OpenAIProvider._client", return_value=MagicMock()) as client:
        client.return_value.models.list.return_value = object()
        assert p.check() is True


def test_openai_check_failure():
    p = _openai()
    with patch("src.scorer.provider.OpenAIProvider._client", side_effect=RuntimeError("no net")):
        assert p.check() is False


def test_openai_check_no_key_false():
    p = _openai(api_key="")
    assert p.check() is False


def test_openai_check_falls_back_to_chat_probe():
    """无 /models 端点的兼容服务（vLLM/LM Studio）→ models.list 失败时降级为最小 chat 探测（B9）。"""
    p = _openai()
    client = MagicMock()
    client.models.list.side_effect = RuntimeError("no /models endpoint")
    client.chat.completions.create.return_value = object()
    with patch("src.scorer.provider.OpenAIProvider._client", return_value=client):
        assert p.check() is True
    client.chat.completions.create.assert_called_once()


def test_openai_check_chat_probe_fails_returns_false():
    """/models 与 chat 探测都失败 → 判不可用。"""
    p = _openai()
    client = MagicMock()
    client.models.list.side_effect = RuntimeError("no /models endpoint")
    client.chat.completions.create.side_effect = RuntimeError("chat down")
    with patch("src.scorer.provider.OpenAIProvider._client", return_value=client):
        assert p.check() is False


# ─── OllamaProvider ──────────────────────────────────────


def test_ollama_chat_success():
    p = _ollama()
    resp = MagicMock()
    resp.raise_for_status = lambda: None
    resp.json.return_value = {"message": {"content": "本地回复"}}
    p._httpx.post.return_value = resp
    assert p.chat("system", "user") == "本地回复"
    # 请求体含 messages + stream:false
    body = p._httpx.post.call_args.kwargs["json"]
    assert body["model"] == "llama3"
    assert body["stream"] is False
    assert body["messages"][0]["role"] == "system"


def test_ollama_chat_sends_think_false_by_default():
    """默认关闭 thinking：请求体顶层携带 think:false（qwen3 不再先输出思考段）"""
    p = _ollama()
    resp = MagicMock()
    resp.raise_for_status = lambda: None
    resp.json.return_value = {"message": {"content": "ok"}}
    p._httpx.post.return_value = resp
    p.chat("system", "user")
    body = p._httpx.post.call_args.kwargs["json"]
    assert body["think"] is False


def test_ollama_chat_think_true_when_enabled():
    """think=True 时请求体顶层携带 think:true（开启 qwen3 思考模式）"""
    p = OllamaProvider(model="qwen3:8b", api_base="http://localhost:11434", think=True)
    p._httpx = MagicMock()
    resp = MagicMock()
    resp.raise_for_status = lambda: None
    resp.json.return_value = {"message": {"content": "ok"}}
    p._httpx.post.return_value = resp
    p.chat("system", "user")
    body = p._httpx.post.call_args.kwargs["json"]
    assert body["think"] is True


def test_ollama_chat_failure_returns_none():
    p = _ollama()
    p._httpx.post.side_effect = RuntimeError("ollama down")
    assert p.chat("s", "u") is None


def test_ollama_check_success_and_failure():
    """连接成功 → True；失败且自动启动后仍不就绪 → False"""
    p = _ollama()
    p._httpx.get.return_value = MagicMock(status_code=200)
    assert p.check() is True
    p._httpx.get.side_effect = RuntimeError("no service")
    with patch.object(OllamaProvider, "_ensure_started", return_value=None):
        with patch.object(OllamaProvider, "_wait_ready", return_value=False):
            assert p.check() is False


def test_ollama_check_auto_starts_when_down():
    """连接失败时自动拉起 ollama serve 并等待就绪 → check 返回 True"""
    p = _ollama()
    p._httpx.get.side_effect = RuntimeError("no service")
    with patch.object(OllamaProvider, "_ensure_started", return_value=None) as start:
        with patch.object(OllamaProvider, "_wait_ready", return_value=True) as ready:
            assert p.check() is True
    start.assert_called_once()
    ready.assert_called_once()


def test_ollama_check_auto_start_timeout_stays_false():
    """自动启动后仍未就绪 → check 返回 False（评分走 fallback）"""
    p = _ollama()
    p._httpx.get.side_effect = RuntimeError("no service")
    with patch.object(OllamaProvider, "_ensure_started", return_value=None):
        with patch.object(OllamaProvider, "_wait_ready", return_value=False) as ready:
            assert p.check() is False
    ready.assert_called_once()


def test_ensure_started_launches_serve():
    """_ensure_started 用 ollama serve 命令后台拉起"""
    p = _ollama()
    with patch("src.scorer.provider.shutil.which", return_value="C:/Ollama/ollama.exe") as which:
        with patch("src.scorer.provider.subprocess.Popen") as popen:
            p._ensure_started()
    which.assert_called_once_with("ollama")
    assert popen.call_count == 1
    cmd = popen.call_args.args[0]
    assert cmd[0] == "C:/Ollama/ollama.exe"
    assert cmd[1] == "serve"


def test_ensure_started_missing_ollama_noop():
    """未找到 ollama 命令时不抛错（仅告警，降级手动启动）"""
    p = _ollama()
    with patch("src.scorer.provider.shutil.which", return_value=None):
        p._ensure_started()  # 不应抛异常


def test_ollama_base_url_trailing_slash_stripped():
    p = OllamaProvider(model="m", api_base="http://localhost:11434/")
    assert p.api_base == "http://localhost:11434"


# ─── build_provider ──────────────────────────────────────


def test_build_provider_deepseek_returns_openai():
    assert isinstance(build_provider(LLMConfig(provider="deepseek")), OpenAIProvider)


def test_build_provider_ollama_returns_ollama():
    assert isinstance(build_provider(LLMConfig(provider="ollama")), OllamaProvider)


def test_build_provider_ollama_default_base():
    """ollama 未显式指定 api_base 时应回退本地默认端点，而非 deepseek 端点。"""
    p = build_provider(LLMConfig(provider="ollama"))
    assert p.api_base == "http://localhost:11434"


def test_build_provider_ollama_forwards_think():
    """build_provider 把 llm.think 透传给 OllamaProvider"""
    p = build_provider(LLMConfig(provider="ollama", think=True))
    assert p.think is True


def test_build_provider_deepseek_default_base():
    """deepseek 未显式指定 api_base 时应回退 deepseek 端点。"""
    p = build_provider(LLMConfig(provider="deepseek"))
    assert p.api_base == "https://api.deepseek.com"


def test_build_provider_deepseek_enables_json_mode():
    """provider=deepseek → 自动启用 DeepSeek JSON 输出"""
    p = build_provider(LLMConfig(provider="deepseek"))
    assert p.json_mode is True


def test_build_provider_deepseek_base_enables_json_mode():
    """provider=openai 但 api_base 指向 deepseek 端点 → 仍启用 JSON 输出（实际 config 写法）"""
    p = build_provider(LLMConfig(provider="openai", api_base="https://api.deepseek.com"))
    assert p.json_mode is True


def test_build_provider_other_base_disables_json_mode():
    """非 deepseek 端点（openai/vllm 等）默认不启用 JSON 输出"""
    p = build_provider(LLMConfig(provider="openai", api_base="https://api.openai.com/v1"))
    assert p.json_mode is False
