"""serve 运行时上下文：可注入对象，取代旧的跨模块 import 的全局 state。

- 由 ``serve.__init__`` 构造并挂到 ``app.state.runtime``（进程级唯一实例，
  但各模块不直接 import 它）；
- 路由经 ``request.app.state.runtime`` 访问（显式依赖注入）；
- 提供轻量 SSE 事件流（import/events）：导入任务完成后由后端推送完成信号，
  前端无需高频轮询（批量导入完成后即刻刷新）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# SSE 流公共参数：2 小时整体超时 + 15 秒心跳间隔（防代理/网关超时断流）
# TODO(配置化)：两项目前是模块级常量，后续如需可调可迁入 ServerConfig。
_SSE_TIMEOUT = 7200
_SSE_HEARTBEAT = 15


def runtime_of(request) -> Runtime:
    """从请求上下文取运行时（显式依赖注入入口，各路由文件共用）。"""
    return request.app.state.runtime


@dataclass
class Runtime:
    """serve 进程内共享的运行时上下文。"""

    settings: object = None  # AppConfig
    zotero_client: object = None  # ZoteroClient | None
    zotero_import: object = None  # ZoteroImportManager | None
    scheduler: object = None  # FetchScheduler | None
    _sse_clients: list[tuple[asyncio.Queue, object]] = field(default_factory=list)

    def log(self, level: str, msg: str) -> None:
        """路由薄层的统一日志入口（未知级别降级为 info）。"""
        method = getattr(logger, level, logger.info)
        method(msg)

    # ── SSE 事件流（导入完成信号，单向 服务端 → 前端）────────

    def subscribe_sse(self) -> asyncio.Queue:
        """订阅事件流，返回结果队列（超时由调用方控制）。

        记录订阅时的事件循环：publish 可能来自其他线程（如测试/回调），
        用 ``call_soon_threadsafe`` 调度到该循环执行，保证跨线程安全。
        """
        queue: asyncio.Queue = asyncio.Queue()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        self._sse_clients.append((queue, loop))
        return queue

    def publish_sse(self, data: dict) -> None:
        """向所有订阅方推送事件（如导入完成/失败）。"""
        for queue, loop in self._sse_clients[:]:
            try:
                if loop is None:
                    queue.put_nowait(data)
                else:
                    loop.call_soon_threadsafe(queue.put_nowait, data)
            except Exception as e:
                logger.warning("SSE 推送失败（跳过该订阅）: %s", e)

    def unsubscribe_sse(self, queue: asyncio.Queue) -> None:
        self._sse_clients = [(q, loop) for q, loop in self._sse_clients if q is not queue]


async def sse_event_stream(runtime, *, forward, terminal, init=None):
    """公共 SSE 事件流：只转发 ``forward`` 集合内类型的事件。

    Args:
        runtime: Runtime 实例（提供 subscribe_sse / unsubscribe_sse）。
        forward: 允许转发的事件类型集合（其余事件丢弃，隔离不同事件域）。
        terminal: 收到即结束流的事件类型集合（如导入终态）；可为空（仅超时结束）。
        init: 可选 ``() -> (dict | None, done: bool)``，连接建立时先推一次当前状态
            （订阅前任务可能已完成，前端据此立即收尾）。

    统一行为：每 15s 心跳注释行保活；超过 2h 整体超时下发 timeout 事件并结束；
    结束（含取消）后自动退订。
    """
    queue = runtime.subscribe_sse()
    try:
        if init is not None:
            data, done = init()
            if data:
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                if done:
                    return
        deadline = time.monotonic() + _SSE_TIMEOUT
        while True:
            # 整体超时在每轮（含事件持续流入时）都检查，避免仅心跳超时分支才判定
            if time.monotonic() >= deadline:
                yield f"data: {json.dumps({'type': 'timeout'})}\n\n"
                return
            try:
                data = await asyncio.wait_for(queue.get(), timeout=_SSE_HEARTBEAT)
                event_type = data.get("type")
                if event_type in forward:
                    yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                    if event_type in terminal:
                        return
            except TimeoutError:
                yield ": ping\n\n"  # 心跳注释行（SSE 规范忽略），保活
    except asyncio.CancelledError:
        pass
    finally:
        runtime.unsubscribe_sse(queue)
