"""Platform engine for **interactive** VeriWorld tasks.

Interactive tasks keep a single UE instance alive for the whole episode
and drive it per-tick: screenshot → think → act → screenshot. The engine
class here is a thin task-facing wrapper around :class:`UEClient` — it
adds process launch / teardown and a couple of convenience helpers, but
imposes **no schema** on what conditions a task observes or what actions
it takes. Those are defined inside each task.

Contract (what every interactive task can count on):

1. A packaged UE instance is running on ``port``.
2. The requested ``level`` has been loaded.
3. The task is handed an :class:`InteractiveEngine` and may call
   :meth:`python_exec`, :meth:`screenshot`, :meth:`switch_level`, or send
   arbitrary JSON-RPC via :attr:`ue` (the underlying :class:`UEClient`)
   to implement whatever observation/action pattern it wants.

See :mod:`veriworld.infra.interactive.task_template` for a skeleton
task that uses this engine.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from veriworld.common.ws import UEClient

log = logging.getLogger(__name__)


class InteractiveEngine:
    """Persistent-UE engine for interactive tasks.

    The instance launches the executable, waits for the WebSocket to
    accept connections, and exposes the underlying :class:`UEClient`
    plus a few primitives. It does **not** spawn actors, load levels,
    or take screenshots on its own — the task drives that.

    Example::

        async with InteractiveEngine.launch(
            exe=Path(r"...\\PackagedOutput\\Windows\\demo1.exe"),
            port=9003,
            level="Untitled",
        ) as engine:
            await engine.python_exec("print('hello')")
            resp = await engine.screenshot()
    """

    def __init__(
        self,
        exe: Path,
        port: int,
        *,
        width: int = 640,
        height: int = 480,
        extra_args: Optional[list[str]] = None,
    ) -> None:
        self.exe = Path(exe)
        self.port = port
        self.width = width
        self.height = height
        self.extra_args = list(extra_args or [])
        self._proc: Optional[subprocess.Popen] = None
        self._ue: Optional[UEClient] = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    async def start(self, *, connect_timeout: float = 60.0) -> None:
        """Launch the UE executable and connect a WebSocket client."""
        if not self.exe.exists():
            raise FileNotFoundError(self.exe)
        args = [
            str(self.exe),
            self.exe.stem,
            "-AudioMixer",
            f"-WebSocketPort={self.port}",
            "-windowed",
            f"-ResX={self.width}",
            f"-ResY={self.height}",
            "-ForceRes",
            "-nosplash",
            "-log",
            *self.extra_args,
        ]
        log.info("launching UE: port=%d exe=%s", self.port, self.exe)
        self._proc = subprocess.Popen(args)

        # Retry connect until the WS server is up.
        deadline = asyncio.get_event_loop().time() + connect_timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                self._ue = UEClient(f"ws://127.0.0.1:{self.port}")
                await self._ue.connect()
                return
            except Exception:  # noqa: BLE001
                await asyncio.sleep(2.0)
        raise RuntimeError(f"UE on port {self.port} did not accept WS within {connect_timeout}s")

    async def stop(self) -> None:
        """Close WS and terminate the UE process **tree**.

        The packaged demo1.exe is a launcher stub that spawns the actual
        game process as a child. A plain ``terminate()`` only kills the
        stub — the game keeps running, holds the port, and pins GPU
        memory. Across multiple parallel batches that accumulates (6
        workers × N batches = 6N ghost processes). Use ``taskkill /T``
        on Windows to take down the whole tree, then verify the PID is
        actually gone before returning.
        """
        if self._ue is not None:
            try:
                await self._ue.close()
            except Exception:  # noqa: BLE001
                pass
            self._ue = None
        if self._proc is not None:
            pid = self._proc.pid
            if sys.platform.startswith("win"):
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                               capture_output=True)
            else:
                self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            self._proc = None
            # Final synchronization — ``Popen.wait`` returns when the
            # parent handle is reaped, but on Windows detached children
            # of the launcher stub may take an extra moment to die. Poll
            # the OS process table until the pid is truly gone, else the
            # next batch's ``start()`` can race the still-finishing UE
            # for the port and the GPU.
            await self._wait_pid_dead(pid, timeout=15.0)

    async def _wait_pid_dead(self, pid: int, *, timeout: float) -> None:
        """Block until the OS reports ``pid`` is gone. Logs at WARNING
        and returns (does not raise) on timeout — the orchestrator's
        teardown loop shouldn't abort on a stuck UE; downstream symptoms
        (port conflict on next start) will surface the problem anyway."""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if sys.platform.startswith("win"):
                r = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                    capture_output=True, text=True,
                )
                if str(pid) not in r.stdout:
                    return
            else:
                try:
                    os.kill(pid, 0)
                except OSError:
                    return
            await asyncio.sleep(0.5)
        log.warning(
            "InteractiveEngine: pid %d on port %d still alive after %.0fs "
            "despite taskkill — next batch may race for the port",
            pid, self.port, timeout,
        )

    @classmethod
    async def launch(cls, **kw: object) -> "InteractiveEngine":
        level = kw.pop("level", None)
        eng = cls(**kw)  # type: ignore[arg-type]
        await eng.start()
        if level is not None:
            await eng.switch_level(str(level))
        return eng

    @classmethod
    async def attach(
        cls,
        uri: str,
        *,
        level: Optional[str] = None,
        connect_timeout: float = 60.0,
    ) -> "InteractiveEngine":
        """Connect to an **already running** UE instance.

        Used when the user launched ``demo1.exe`` by hand (the pattern
        documented in the original ``harness_win`` README). Skip the
        ``exe`` / process-launch machinery entirely.
        """
        port = int(uri.rsplit(":", 1)[-1])
        eng = cls(exe=Path("/dev/null"), port=port)  # exe unused in attach mode
        deadline = asyncio.get_event_loop().time() + connect_timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                eng._ue = UEClient(uri)
                await eng._ue.connect()
                if level is not None:
                    await eng.switch_level(level)
                return eng
            except Exception:  # noqa: BLE001
                await asyncio.sleep(2.0)
        raise RuntimeError(f"Could not attach to {uri} within {connect_timeout}s")

    async def __aenter__(self) -> "InteractiveEngine":
        if self._ue is None:
            await self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.stop()

    # ------------------------------------------------------------------
    # primitives (pass-through)
    # ------------------------------------------------------------------
    @property
    def ue(self) -> UEClient:
        if self._ue is None:
            raise RuntimeError("InteractiveEngine not started")
        return self._ue

    async def python_exec(self, code: str) -> dict:
        return await self.ue.python_exec(code)

    async def screenshot(self) -> dict:
        return await self.ue.screenshot(self.width, self.height)

    async def switch_level(self, level_name: str) -> dict:
        return await self.ue.switch_level(level_name)


__all__ = ["InteractiveEngine"]
