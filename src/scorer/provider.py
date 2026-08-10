"""LLM Chat Provider：统一 OpenAI 兼容（deepseek）与 ollama 本地模型接口。

- ``ChatProvider``：协议，PaperScorer 只依赖它，不关心具体厂商；
- ``OpenAIProvider``：deepseek / 任意 OpenAI 兼容端点（OpenAI SDK）；
- ``OllamaProvider``：本地模型，原生 ``POST {base}/api/chat``（httpx），
  messages 格式与 OpenAI 一致，输出 ``data["message"]["content"]``；
- ``build_provider``：按 ``LLMConfig.provider`` 构造。

Prompt 构造、few-shot、parse、fallback、重试逻辑均与 provider 无关（messages 格式统一）。
"""

import logging
import os
import shutil
import subprocess
import time
from typing import Protocol

logger = logging.getLogger(__name__)

# 各 provider 的默认端点（config 未显式指定 api_base 时由 build_provider 回退到此）
DEEPSEEK_DEFAULT_BASE = "https://api.deepseek.com"
OLLAMA_DEFAULT_BASE = "http://localhost:11434"

# Ollama 连接失败时自动启动后等待就绪的超时上限（秒）
_START_TIMEOUT = 20.0

# 其余超时（秒）：单轮对话 / /api/tags 探测 / OpenAI check 的最小 chat 探测
_CHAT_TIMEOUT = 180.0
_TAGS_TIMEOUT = 10.0
_TAGS_POLL_TIMEOUT = 3.0  # _wait_ready 轮询单次探测
_PROBE_CHAT_TIMEOUT = 10.0


class ChatProvider(Protocol):
    """LLM 对话接口（PaperScorer 的依赖抽象）。"""

    model: str
    api_base: str
    api_key: str
    requires_key: bool  # 是否需要 API Key（ollama 本地部署无需）

    def check(self) -> bool:
        """可用性检测（连接/鉴权）。"""
        ...

    def chat(self, system: str, user: str) -> str | None:
        """单轮对话，返回 assistant 文本；失败返回 None。"""
        ...


class OpenAIProvider:
    """OpenAI SDK 兼容 provider（deepseek / openai / 任意 base_url）。

    client 惰性创建：api_key 为空时构造不抛错（PaperScorer._check_llm 会短路判定
    无 Key → fallback；此处的 check/chat 也会在无 key 时直接返回 False/None）。
    """

    requires_key = True

    def __init__(
        self,
        *,
        model: str,
        api_base: str,
        api_key: str,
        temperature: float = 0.3,
        max_tokens: int = 2000,
    ):
        from openai import OpenAI

        self._OpenAI = OpenAI
        self.model = model
        self.api_base = api_base
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client_obj = None  # 惰性缓存客户端，避免 check/chat 每次重建（省握手开销）

    def _client(self):
        if self._client_obj is None:
            self._client_obj = self._OpenAI(api_key=self.api_key, base_url=self.api_base)
        return self._client_obj

    def check(self) -> bool:
        if not self.api_key:
            return False
        try:
            self._client().models.list()
            return True
        except Exception:
            # 兼容不支持 /models 的端点（vLLM/LM Studio 等）：降级为一次最小 chat 探测
            return self._probe_chat()

    def _probe_chat(self) -> bool:
        try:
            self._client().chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
                timeout=_PROBE_CHAT_TIMEOUT,
            )
            return True
        except Exception as e:
            logger.warning("LLM 连接失败: %s", e)
            return False

    def chat(self, system: str, user: str) -> str | None:
        if not self.api_key:
            return None
        try:
            resp = self._client().chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            return resp.choices[0].message.content
        except Exception as e:
            logger.warning("LLM API call failed: %s", e)
            return None


class OllamaProvider:
    """ollama 本地模型 provider（原生 ``/api/chat``，无需 API Key）。"""

    requires_key = False
    api_key = ""

    def __init__(
        self,
        *,
        model: str,
        api_base: str = OLLAMA_DEFAULT_BASE,
        temperature: float = 0.3,
        max_tokens: int = 2000,
        think: bool = False,
    ):
        import httpx

        self.model = model
        self.api_base = (api_base or OLLAMA_DEFAULT_BASE).rstrip("/")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.think = think  # qwen3 等推理模型：顶层 think=false 关闭思考（放 options 会被忽略）
        self._httpx = httpx
        self._start_attempted = False  # 自动启动只尝试一次，避免反复拉起进程

    def check(self) -> bool:
        try:
            r = self._httpx.get(f"{self.api_base}/api/tags", timeout=_TAGS_TIMEOUT)
            if r.status_code == 200:
                return True
        except Exception as e:
            logger.warning("Ollama 连接失败: %s", e)

        # 连接失败 → 尝试自动拉起一次，等待服务就绪后重新判定
        if not self._start_attempted:
            self._start_attempted = True
            self._ensure_started()
            if self._wait_ready():
                logger.info("Ollama 已自动启动并就绪")
                return True
            logger.warning(
                "Ollama 自动启动后 %s 秒内未就绪，评分将走 fallback", _START_TIMEOUT
            )
        return False

    def _ensure_started(self) -> None:
        """自动启动 Ollama（后台运行 ``ollama serve``，不阻塞等待结果）。

        找不到 ollama 命令时不抛错，仅记录 warning（降级为手动启动）。
        """
        exe = shutil.which("ollama")
        if not exe:
            logger.warning("未找到 ollama 命令，无法自动启动（请手动运行 ollama serve）")
            return
        logger.info("Ollama 未运行，尝试自动启动: %s serve", exe)
        try:
            kwargs: dict = {}
            if os.name == "nt":  # Windows 避免弹出控制台窗口
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            else:  # POSIX detach，随父进程退出独立存活
                kwargs["start_new_session"] = True
            subprocess.Popen(
                [exe, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **kwargs,
            )
        except OSError as e:
            logger.warning("自动启动 ollama serve 失败: %s", e)

    def _wait_ready(self, timeout: float = _START_TIMEOUT, interval: float = 0.5) -> bool:
        """轮询 /api/tags 直到服务就绪，超时返回 False。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                r = self._httpx.get(f"{self.api_base}/api/tags", timeout=_TAGS_POLL_TIMEOUT)
                if r.status_code == 200:
                    return True
            except Exception:
                pass  # 服务尚未就绪，继续轮询
            time.sleep(interval)
        return False

    def chat(self, system: str, user: str) -> str | None:
        try:
            r = self._httpx.post(
                f"{self.api_base}/api/chat",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "stream": False,
                    "think": self.think,  # 顶层字段：qwen3 识别，非推理模型忽略
                    "options": {
                        "temperature": self.temperature,
                        "num_predict": self.max_tokens,
                    },
                },
                timeout=_CHAT_TIMEOUT,
            )
            r.raise_for_status()
            data = r.json()
            return (data.get("message") or {}).get("content")
        except Exception as e:
            logger.warning("Ollama 调用失败: %s", e)
            return None


def build_provider(llm) -> ChatProvider:
    """按 ``LLMConfig``（provider/model/api_base/api_key/...）构造 provider。"""
    if llm.provider.lower() == "ollama":
        return OllamaProvider(
            model=llm.model,
            api_base=llm.api_base or OLLAMA_DEFAULT_BASE,
            temperature=llm.temperature,
            max_tokens=llm.max_tokens,
            think=llm.think,
        )
    return OpenAIProvider(
        model=llm.model,
        api_base=llm.api_base or DEEPSEEK_DEFAULT_BASE,
        api_key=llm.api_key,
        temperature=llm.temperature,
        max_tokens=llm.max_tokens,
    )
