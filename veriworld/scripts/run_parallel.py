"""Parallel orchestrator — run one ablation across N models × M seeds concurrently.

Rationale
---------
Comparing two different models on **different** seeds is not a fair
comparison — each seed produces a different maze / terrain instance
with its own difficulty. For benchmark-grade statistics, every seed
must be run against **every** model being compared, and ideally those
runs should happen under the same wall-clock conditions (no API
quota drift, no UE startup-cache differences).

This script enforces that:

* Load the parallel-eligible models from ``model_configs.json``
  (entries with ``"parallel": true``, or the ``--models`` override).
* For every ``(seed, model)`` pair, spawn a UE instance on its own
  WebSocket port and run the task's ``run()`` coroutine.
* Respect ``--max-instances`` (default 6) — a single GPU's VRAM
  budget. Jobs are split into batches, each batch is a multiple of
  ``N_models`` so no seed ever spans batches (fairness preserved).
* Between batches: every UE in the previous batch is torn down before
  the next batch launches. No leftover state or orphaned ports.
* Write a cross-seed summary to ``runs/parallel_<TS>/summary.json``.
  Per-seed per-model artefacts are still written by the task itself
  into ``veriworld/results/<task_path>/seed_XXXX_TS/<model>/`` via
  :class:`RunLogger`, mirroring the ``veriworld/benchmark/`` tree so
  outputs sit next to the task they came from.

Usage
-----
::

    python -m veriworld.scripts.run_parallel \
        --task veriworld.benchmark.interactive.navigation.mazenavfps.vp_bf \
        --seeds 0,1 \
        --base-port 9003 \
        --build "C:/.../PackagedOutput/Windows/demo1.exe"
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import json
import logging
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Sequence

from veriworld.common import RunLogger, VLMClient, load_configs, task_path_from_module
from veriworld.common.vlm import ModelConfig
from veriworld.infra.computational.engine import ComputationalEngine
from veriworld.infra.interactive.engine import InteractiveEngine

log = logging.getLogger("run_parallel")


# ---------------------------------------------------------------------------
# Arg handling
# ---------------------------------------------------------------------------
def _parse_seeds(spec: str) -> List[int]:
    """'0,1,5-7' -> [0, 1, 5, 6, 7]."""
    out: List[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def _parse_bool(s: Any) -> Optional[bool]:
    if s is None:
        return None
    if isinstance(s, bool):
        return s
    return str(s).lower() in ("true", "1", "yes", "y")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run one ablation across all parallel-enabled models × seeds. "
                    "Defaults are read from run_defaults.json; CLI flags override.",
    )
    # Only --task is required — everything else falls back to run_defaults.json
    p.add_argument("--task", required=True,
                   help="Dotted path to the ablation module, e.g. "
                        "veriworld.benchmark.interactive.navigation.mazenavfps.vp_bf")
    p.add_argument("--defaults", type=Path, default=Path("run_defaults.json"),
                   help="Path to run_defaults.json (default: ./run_defaults.json). "
                        "Copy from run_defaults.example.json.")
    p.add_argument("--configs", type=Path, default=Path("model_configs.json"),
                   help="Path to model_configs.json (default: ./model_configs.json)")
    p.add_argument("--output-root", type=Path, default=Path.cwd(),
                   help="Parent dir where runs/ is created (default: CWD)")

    # Parallel block — unset means "take from run_defaults.json -> parallel"
    p.add_argument("--seeds", default=None,
                   help="Comma/range spec, e.g. '0,1' or '0-3'. "
                        "Overrides parallel.seeds in run_defaults.json.")
    p.add_argument("--base-port", type=int, default=None)
    p.add_argument("--max-instances", type=int, default=None)
    p.add_argument("--width", type=int, default=None)
    p.add_argument("--height", type=int, default=None)

    # Model selection
    p.add_argument("--models", default=None,
                   help="Override: comma-separated config names (e.g. 'gemini-3-pro,gpt-4.1'). "
                        "If unset, uses every entry with parallel=true.")

    # UE build / attach
    p.add_argument("--build", type=Path, default=None,
                   help="Path to demo1.exe. If unset, read from "
                        "run_defaults.json -> builds.{interactive|computational} based on "
                        "the task module path.")
    p.add_argument("--attach", action="store_true",
                   help="Interactive-only: skip UE launch/teardown and connect to "
                        "instances already running on ports base-port, base-port+1, ...  "
                        "Use this for debug iteration after launching UE manually. "
                        "Ignored for computational tasks.")

    # Task-specific pass-throughs — unset means "take from run_defaults.json -> tasks[<task>]"
    p.add_argument("--grid-size", type=int, default=None)
    p.add_argument("--materials", type=int, default=None)
    p.add_argument("--max-steps", type=int, default=None)
    p.add_argument("--initial-yaw", type=float, default=None)
    p.add_argument("--tunnel-radius", type=float, default=None)
    p.add_argument("--colorful", type=str, default=None)
    p.add_argument("--history-size", type=int, default=None)
    p.add_argument("--max-rounds", type=int, default=None)
    p.add_argument("--n-frames", type=int, default=None)
    p.add_argument("--settle-timeout", type=float, default=None)

    p.add_argument("--log-level", default="INFO")
    return p.parse_args()


def _load_defaults(path: Path) -> dict:
    """Load run_defaults.json or return empty dict if missing (all-CLI mode)."""
    if not path.exists():
        log.info("no %s — using CLI values and hardcoded fallbacks only", path)
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    # Strip top-level _comment sibling; nested _comment fields are ignored on read.
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _apply_defaults(args: argparse.Namespace, defaults: dict) -> None:
    """For every arg that's None, fill from defaults. CLI (non-None) wins."""
    parallel = defaults.get("parallel", {}) or {}
    tasks = defaults.get("tasks", {}) or {}
    builds = defaults.get("builds", {}) or {}

    # Global parallel block
    if args.seeds is None:
        s = parallel.get("seeds")
        if isinstance(s, list):
            args.seeds = ",".join(str(x) for x in s)
        elif isinstance(s, str):
            args.seeds = s
        else:
            args.seeds = "0"
    for field, fallback in [("base_port", 9003), ("max_instances", 6),
                            ("width", 640), ("height", 480)]:
        if getattr(args, field) is None:
            setattr(args, field, parallel.get(field, fallback))

    # Task-specific block
    task_cfg = tasks.get(args.task, {}) or {}
    for k, v in task_cfg.items():
        if k.startswith("_"):
            continue
        # Don't clobber CLI; only fill if unset.
        if hasattr(args, k) and getattr(args, k) is None:
            setattr(args, k, v)
        # Also tolerate hyphen/underscore mismatch via attr mapping.

    # Build path — pick by task path if --build not given
    if args.build is None:
        is_comp = _is_computational(args.task)
        key = "computational" if is_comp else "interactive"
        if key in builds:
            args.build = Path(builds[key])


def _select_models(cfgs: Sequence[ModelConfig], override: Optional[str]) -> List[ModelConfig]:
    if override:
        wanted = [s.strip() for s in override.split(",") if s.strip()]
        by_name = {c.name: c for c in cfgs}
        missing = [w for w in wanted if w not in by_name]
        if missing:
            raise SystemExit(f"--models: not in config: {missing}")
        return [by_name[w] for w in wanted]
    flagged = [c for c in cfgs if c.parallel]
    if not flagged:
        raise SystemExit("No models with parallel=true in config; set at least one or pass --models")
    return flagged


def _build_task_kwargs(task_run, args: argparse.Namespace) -> dict:
    """Collect non-None CLI overrides, then filter by task.run's signature."""
    raw = {
        "grid_size": args.grid_size,
        "materials": args.materials,
        "max_steps": args.max_steps,
        "initial_yaw": args.initial_yaw,
        "tunnel_radius": args.tunnel_radius,
        "colorful": _parse_bool(args.colorful),
        "history_size": args.history_size,
        "max_rounds": args.max_rounds,
        "n_frames": args.n_frames,
        "settle_timeout": args.settle_timeout,
    }
    raw = {k: v for k, v in raw.items() if v is not None}
    accepted = set(inspect.signature(task_run).parameters.keys())
    filtered = {k: v for k, v in raw.items() if k in accepted}
    dropped = set(raw) - set(filtered)
    if dropped:
        log.info("ignored kwargs not accepted by %s.run: %s", task_run.__module__, sorted(dropped))
    return filtered


def _is_computational(module_path: str, task_mod: Optional[Any] = None) -> bool:
    """Decide which engine class a task needs.

    Priority:
    1. Explicit ``ENGINE = "computational" | "interactive"`` on the task
       module (set by tasks that want an engine different from what
       their path would imply — e.g. a knowledge-accumulation harness
       living under ``interactive/navigation/`` but needing per-round
       UE restart).
    2. Path-based heuristic — any ``computational`` segment.
    """
    if task_mod is not None:
        declared = getattr(task_mod, "ENGINE", None)
        if isinstance(declared, str):
            return declared.lower() == "computational"
    return "computational" in module_path.split(".")


# ---------------------------------------------------------------------------
# Engine lifecycle per batch
# ---------------------------------------------------------------------------
async def _launch_interactive_engines(
    build: Path, ports: Sequence[int], width: int, height: int,
) -> List[InteractiveEngine]:
    async def one(port: int) -> InteractiveEngine:
        eng = InteractiveEngine(exe=build, port=port, width=width, height=height)
        await eng.start()
        return eng

    return await asyncio.gather(*(one(p) for p in ports))


async def _attach_interactive_engines(ports: Sequence[int]) -> List[InteractiveEngine]:
    return await asyncio.gather(
        *(InteractiveEngine.attach(f"ws://127.0.0.1:{p}") for p in ports)
    )


def _make_computational_engines(
    build: Path, ports: Sequence[int], width: int, height: int,
) -> List[ComputationalEngine]:
    # ComputationalEngine manages its own per-round lifecycle; no start() here.
    return [ComputationalEngine(exe=build, port=p, width=width, height=height) for p in ports]


async def _run_job(task_run, engine, seed: int, cfg: ModelConfig,
                   logger: RunLogger, kwargs: dict) -> dict:
    vlm = VLMClient.from_config(cfg)
    try:
        result = await task_run(engine, seed=seed, vlm=vlm, logger=logger, **kwargs)
        return {"seed": seed, "model": cfg.name, "ok": True, "result": _serialise(result)}
    except Exception as e:  # noqa: BLE001
        log.exception("seed=%d model=%s failed", seed, cfg.name)
        return {"seed": seed, "model": cfg.name, "ok": False, "error": str(e)}


def _serialise(x: Any) -> Any:
    if is_dataclass(x):
        return asdict(x)
    return x


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def _amain() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(name)s [%(levelname)s] %(message)s",
    )

    # --- Merge defaults file + CLI (CLI wins where non-None) ---
    defaults = _load_defaults(args.defaults)
    _apply_defaults(args, defaults)

    # --- Load task module ---
    task_mod = importlib.import_module(args.task)
    task_run = getattr(task_mod, "run", None)
    if task_run is None:
        raise SystemExit(f"{args.task} has no `run` callable — is this an ablation module?")
    is_comp = _is_computational(args.task, task_mod)

    # ``_apply_defaults`` picked args.build from the path-only heuristic;
    # re-resolve now that we can see the task module's ENGINE sentinel.
    # Only override if the answer actually flipped AND a build section
    # exists in run_defaults (we didn't get a CLI --build).
    if is_comp != _is_computational(args.task):
        builds = (defaults.get("builds", {}) or {})
        key = "computational" if is_comp else "interactive"
        if key in builds:
            args.build = Path(builds[key])

    # Validate --attach / --build combinations
    if is_comp and args.attach:
        log.warning("--attach is meaningless for computational tasks (UE is restarted per round); ignoring")
        args.attach = False
    if is_comp and args.build is None:
        raise SystemExit(
            "No build configured. Set builds.computational in run_defaults.json or pass --build.")
    if not is_comp and not args.attach and args.build is None:
        raise SystemExit(
            "No build configured. Set builds.interactive in run_defaults.json, pass --build, "
            "or pass --attach to connect to a pre-launched UE.")

    # --- Models + seeds ---
    cfgs = load_configs(args.configs)
    models = _select_models(cfgs, args.models)
    seeds = _parse_seeds(args.seeds)
    N = len(models)
    if N == 0:
        raise SystemExit("no models selected")

    # --- Batch sizing (keep each batch a multiple of N_models so seeds are atomic) ---
    if args.max_instances < N:
        raise SystemExit(
            f"--max-instances ({args.max_instances}) < N_models ({N}); "
            "can't run a single seed-group within one batch."
        )
    batch_size = (args.max_instances // N) * N
    if batch_size < args.max_instances:
        log.info("rounding batch size down to %d (multiple of %d models) for fair parallelism",
                 batch_size, N)

    jobs: List[tuple[int, ModelConfig]] = [(s, c) for s in seeds for c in models]
    batches = [jobs[i:i + batch_size] for i in range(0, len(jobs), batch_size)]

    log.info("task=%s  engine=%s  models=%s  seeds=%s  total_jobs=%d  batches=%d (size %d)",
             args.task, "computational" if is_comp else "interactive",
             [m.name for m in models], seeds, len(jobs), len(batches), batch_size)

    # --- Task kwargs ---
    task_kwargs = _build_task_kwargs(task_run, args)

    # --- Per-seed RunLoggers (reused across batches if the seed recurs) ---
    task_path = task_path_from_module(args.task)
    seed_loggers: dict[int, RunLogger] = {
        s: RunLogger(root=args.output_root, seed=s, task_path=task_path)
        for s in seeds
    }

    # --- Snapshot task sources + reproducer command into every seed dir ---
    # Each seed dir must be self-contained: task.md, api.md, example.py,
    # generate_params.py, the whole lean_verify/, and a reproduce.bat/sh.
    # Build a per-seed resolved_args so the reproducer runs exactly one seed.
    shared_args = {k: v for k, v in vars(args).items()
                   if k not in ("defaults", "output_root", "log_level")}
    for seed, seed_logger in seed_loggers.items():
        per_seed_args = {**shared_args, "seeds": str(seed)}
        seed_logger.snapshot_task(
            args.task,
            resolved_args=per_seed_args,
            invocation="parallel",
        )

    # Which seed went into which batch (for orchestrator.json in each seed dir)
    seed_to_batch: dict[int, int] = {}
    all_results: List[dict] = []
    invocation_ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    def _write_orchestrator_info(status: str) -> None:
        """Write a per-seed ``orchestrator.json`` into every seed dir.

        Called at startup (status="starting"), after each batch
        (status="running" / "completed") and on exit (status="interrupted"
        / "failed"). Everything orchestrator-level lives in the seed's own
        directory — no separate parallel_* dir."""
        for seed, logger in seed_loggers.items():
            info = {
                "task": args.task,
                "engine": "computational" if is_comp else "interactive",
                "invocation_ts": invocation_ts,
                "models_in_this_batch": [m.name for m in models],
                "all_seeds_in_this_invocation": seeds,
                "max_instances": args.max_instances,
                "batch_size_used": batch_size,
                "batch_num": seed_to_batch.get(seed),  # None until seed's batch starts
                "status": status,
            }
            (logger.root / "orchestrator.json").write_text(
                json.dumps(info, indent=2, default=str), encoding="utf-8")

    _write_orchestrator_info("starting")

    try:
        for b_idx, batch in enumerate(batches):
            log.info("=== batch %d/%d  (%d jobs) ===", b_idx + 1, len(batches), len(batch))
            ports = [args.base_port + i for i in range(len(batch))]

            # Tag every seed in this batch with its batch number
            for seed, _cfg in batch:
                seed_to_batch[seed] = b_idx + 1

            # Launch or attach to UEs for this batch
            if is_comp:
                engines: List[Any] = _make_computational_engines(
                    args.build, ports, args.width, args.height)
            elif args.attach:
                log.info("--attach: connecting to UEs already running on ports %s", ports)
                engines = await _attach_interactive_engines(ports)
            else:
                engines = await _launch_interactive_engines(
                    args.build, ports, args.width, args.height)

            try:
                job_tasks = [
                    _run_job(task_run, eng, seed, cfg, seed_loggers[seed], task_kwargs)
                    for eng, (seed, cfg) in zip(engines, batch)
                ]
                batch_results = await asyncio.gather(*job_tasks)
                all_results.extend(batch_results)
            finally:
                # Strict teardown between batches
                for eng in engines:
                    try:
                        if is_comp:
                            await eng.close()
                        else:
                            await eng.stop()
                    except Exception:  # noqa: BLE001
                        log.warning("teardown failed for port %d",
                                    getattr(eng, "port", "?"), exc_info=True)
                # Flush orchestrator.json after teardown — safe even on KeyboardInterrupt
                _write_orchestrator_info("running")

        _write_orchestrator_info("completed")
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.warning("interrupted — flushing partial orchestrator.json")
        _write_orchestrator_info("interrupted")
        raise
    except Exception:
        log.exception("orchestrator failed — flushing partial orchestrator.json")
        _write_orchestrator_info("failed")
        raise

    log.info("done. %d jobs across %d seed(s).", len(all_results), len(seeds))


if __name__ == "__main__":
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        sys.exit(130)
