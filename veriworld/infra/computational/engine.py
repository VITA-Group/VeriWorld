"""Platform engine for **computational** VeriWorld tasks.

Computational tasks submit a complete artefact (parameter vector, Python
code, or Slang shader) once per round, let the engine run the simulation,
and receive video + structured log feedback. Between rounds the UE
process is **fully restarted** — this is the core difference from the
interactive engine — so that GPU / CUDA / shader state is clean each
time and rounds cannot leak side-effects into each other.

Contract:

1. On each :meth:`next_round` the engine kills any running UE instance
   on the same port, launches a fresh one, connects a WebSocket, and
   switches to the requested level.
2. Inside a round the task may call :meth:`python_exec`,
   :meth:`screenshot`, :meth:`record_video`, or send arbitrary JSON-RPC
   via :attr:`ue`.
3. On :meth:`close` everything is torn down.

See :mod:`veriworld.infra.computational.task_template` for a skeleton
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


class ComputationalEngine:
    """UE engine that **restarts per round**.

    Parameters
    ----------
    exe : Path
        Path to the packaged ``demo1.exe`` (the ``PackagedOutput_dev``
        build is what shipped VeriWorld computational tasks expect).
    port : int
        WebSocket port. Unique per agent when running multiple agents
        in parallel.
    process_image_name : str
        Executable base name, used when forcibly killing orphaned
        instances on the same port. Default ``"demo1.exe"``.
    """

    def __init__(
        self,
        exe: Path,
        port: int,
        *,
        width: int = 640,
        height: int = 480,
        process_image_name: str = "demo1.exe",
        extra_args: Optional[list[str]] = None,
    ) -> None:
        self.exe = Path(exe)
        self.port = port
        self.width = width
        self.height = height
        self.process_image_name = process_image_name
        self.extra_args = list(extra_args or [])
        self._proc: Optional[subprocess.Popen] = None
        self._ue: Optional[UEClient] = None

    # ------------------------------------------------------------------
    # per-round lifecycle
    # ------------------------------------------------------------------
    async def next_round(self, level: str, *, connect_timeout: float = 60.0) -> None:
        """Tear down any prior instance, launch a new one, load ``level``."""
        await self._kill()
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
        log.info("next_round: port=%d level=%s", self.port, level)
        self._proc = subprocess.Popen(args)

        deadline = asyncio.get_event_loop().time() + connect_timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                self._ue = UEClient(f"ws://127.0.0.1:{self.port}")
                await self._ue.connect()
                await self._ue.switch_level(level)
                return
            except Exception:  # noqa: BLE001
                await asyncio.sleep(2.0)
        raise RuntimeError(f"UE on port {self.port} did not accept WS within {connect_timeout}s")

    async def close(self) -> None:
        await self._kill()

    async def _kill(self) -> None:
        if self._ue is not None:
            try:
                await self._ue.close()
            except Exception:  # noqa: BLE001
                pass
            self._ue = None
        if self._proc is not None:
            pid = self._proc.pid
            # Per-PID with ``/T`` kills the whole process tree so that
            # the actual game process (spawned as a child by the demo1
            # launcher stub) is taken down together with its parent.
            # Without ``/T`` the game proc keeps running, accumulates
            # across rounds, and holds open file locks on the h264
            # scratch that the next round needs to read.
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
            # ``Popen.wait`` returns when the parent handle is reaped, but on
            # Windows the launcher stub may have spawned a detached child
            # that took a moment longer to die. Without this final sync the
            # next round's ``StartRecording`` could race a still-finishing
            # NVENC encoder writing to ``rec_dir``, satisfying the next
            # round's ``mtime > t_record_start`` filter and feeding stale
            # frames into the agent.
            await self._wait_pid_dead(pid, timeout=15.0)

    async def _wait_pid_dead(self, pid: int, *, timeout: float) -> None:
        """Block until the OS reports ``pid`` is gone. Raises
        :class:`RuntimeError` on timeout — caller is expected to log
        loudly; the next round will still launch a fresh UE on the same
        port, but a stuck descendant may contaminate ``rec_dir`` scratch
        until it dies."""
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
        raise RuntimeError(
            f"UE pid {pid} on port {self.port} still alive after {timeout}s "
            f"despite taskkill — leftover process may contaminate rec_dir scratch"
        )

    async def __aenter__(self) -> "ComputationalEngine":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # primitives (pass-through)
    # ------------------------------------------------------------------
    @property
    def ue(self) -> UEClient:
        if self._ue is None:
            raise RuntimeError("ComputationalEngine: no active round (call next_round first)")
        return self._ue

    async def python_exec(self, code: str) -> dict:
        return await self.ue.python_exec(code)

    async def screenshot(self) -> dict:
        return await self.ue.screenshot(self.width, self.height)

    @property
    def rec_dir(self) -> Path:
        """Absolute path to the packaged build's recording output dir.

        UE's AWStreamBridge writes NVENC recordings to
        ``<exe.parent>/<project>/Saved/Recordings/`` for a packaged
        build (not ``%LOCALAPPDATA%`` — that's the editor behaviour).

        Task code polls this to pick up the ``.h264`` it just asked UE
        to produce. Derived from ``self.exe`` so it works across
        machines regardless of where the user extracts the build.
        """
        return self.exe.parent / self.exe.stem / "Saved" / "Recordings"


__all__ = ["ComputationalEngine"]
