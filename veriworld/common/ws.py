"""Async WebSocket client for a running UE packaged build.

The build exposes a JSON-RPC 2.0 endpoint on a configurable port (set at
launch via ``-WebSocketPort=9003``). This module is the thin transport
layer the engines and tasks use to talk to it.

Typical usage::

    async with UEClient("ws://127.0.0.1:9003") as ue:
        await ue.python_exec("print(1+1)")
        resp = await ue.screenshot()
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

import websockets

log = logging.getLogger(__name__)


class UEClient:
    """Async JSON-RPC client for a packaged UE instance.

    Parameters
    ----------
    uri : str
        WebSocket URI, e.g. ``"ws://127.0.0.1:9003"``.
    timeout : float | None
        Response timeout in seconds. ``None`` waits indefinitely (needed for
        long-running ``python_exec`` calls like level switches).
    max_size : int
        Max message size in bytes. Default 100 MiB; screenshots + Slang
        sources can be large.
    """

    def __init__(
        self,
        uri: str = "ws://127.0.0.1:9003",
        timeout: Optional[float] = None,
        max_size: int = 100 * 1024 * 1024,
    ) -> None:
        self.uri = uri
        self.timeout = timeout
        self.max_size = max_size
        self._ws: Optional[websockets.WebSocketClientProtocol] = None

    async def connect(self) -> None:
        # Disable keepalive pings entirely. UE runs agent-submitted Python
        # synchronously on its game thread, and a heavy call (80x80 grid
        # gaussian eval, SetShader, ReadBackFloatBuffer + UploadFloatArray)
        # can block the thread for 30-120 s. During that window UE can't
        # reply to pings, so any non-zero ping_timeout drops the connection
        # mid-RPC with "1011 keepalive ping timeout". Matches the private
        # harness_win_billiards/parallel_harness.py which also passes no
        # keepalive.
        self._ws = await websockets.connect(
            self.uri,
            max_size=self.max_size,
            ping_interval=None,
            ping_timeout=None,
        )
        log.info("connected %s", self.uri)

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def __aenter__(self) -> "UEClient":
        await self.connect()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    async def send(self, method: str, params: Optional[dict] = None) -> dict:
        """Send a JSON-RPC method; return the parsed response envelope.

        Raises ``RuntimeError`` if not connected or on timeout.
        """
        if self._ws is None:
            raise RuntimeError("UEClient.send called before connect()")

        msg = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": int(time.time() * 1000),
        }
        await self._ws.send(json.dumps(msg))

        if self.timeout is None:
            raw = await self._ws.recv()
        else:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=self.timeout)

        return self._parse(raw)

    @staticmethod
    def _parse(raw: bytes | str) -> dict:
        if isinstance(raw, bytes) and len(raw) > 4:
            text = raw[4:].decode("utf-8")
        elif isinstance(raw, bytes):
            text = raw.decode("utf-8")
        else:
            text = raw
        return json.loads(text)

    # ------------------------------------------------------------------
    # Convenience wrappers
    # ------------------------------------------------------------------
    async def python_exec(self, code: str, is_save: bool = False) -> dict:
        """Run a Python snippet inside the UE instance's ``unreal_runtime``."""
        return await self.send("python_exec", {"code": code, "is_save": is_save})

    async def screenshot(self, width: int = 640, height: int = 480) -> dict:
        """Request a screenshot from the current camera. Format depends on
        the engine build — some builds return a file path, others return
        base64 bytes. Inspect ``result`` on the returned envelope."""
        return await self.send("screenshot", {"width": width, "height": height})

    async def switch_level(self, level_name: str, *, wait_tick: float = 0.5) -> dict:
        """Open a new level via ``GameplayStatics.OpenLevel``. Blocks until
        the engine acknowledges."""
        code = (
            "import unreal_runtime as ur\n"
            f'current = ur.Engine.GameplayStatics.GetCurrentLevelName(True)\n'
            f'if current != "{level_name}":\n'
            f'    ur.Engine.GameplayStatics.OpenLevel("{level_name}", True, "")\n'
            f'ur.time.sleep({wait_tick})\n'
            'print("level:", ur.Engine.GameplayStatics.GetCurrentLevelName(True))\n'
        )
        return await self.python_exec(code)


__all__ = ["UEClient"]
