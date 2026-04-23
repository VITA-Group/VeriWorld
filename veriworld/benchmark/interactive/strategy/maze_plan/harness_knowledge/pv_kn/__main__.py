"""Per-ablation entry point for harness_knowledge/pv_kn.

For canonical fair-comparison runs invoke via the orchestrator:

    python -m veriworld.scripts.run_parallel \
        --task veriworld.benchmark.interactive.navigation.mazenavfps.harness_knowledge.pv_kn

This ``__main__`` exists so ``python -m <dotted-path>`` works too
(useful for quick single-seed / single-model iteration during
harness development).
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from veriworld.common import RunLogger, VLMClient, load_configs, task_path_from_module
from veriworld.infra.computational.engine import ComputationalEngine

from .task import run


def _main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--exe", type=Path, required=True,
                   help="Path to the computational UE build (demo1.exe)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--port", type=int, default=9003)
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=480)
    p.add_argument("--max-rounds", type=int, default=5)
    p.add_argument("--settle-timeout", type=float, default=60.0)
    p.add_argument("--configs", type=Path, default=Path("model_configs.json"))
    p.add_argument("--model", default="gemini-2.5-flash-video",
                   help="Config name from model_configs.json")
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args()

    import logging
    logging.basicConfig(level=args.log_level.upper(),
                        format="%(asctime)s %(name)s [%(levelname)s] %(message)s")

    cfgs = load_configs(args.configs)
    cfg = next((c for c in cfgs if c.name == args.model), None)
    if cfg is None:
        raise SystemExit(f"--model: {args.model!r} not in {args.configs}")
    vlm = VLMClient.from_config(cfg)

    task_path = task_path_from_module(__package__)
    logger = RunLogger(root=Path.cwd(), seed=args.seed, task_path=task_path)
    engine = ComputationalEngine(exe=args.exe, port=args.port,
                                 width=args.width, height=args.height)

    async def _go() -> None:
        try:
            res = await run(engine, seed=args.seed, vlm=vlm, logger=logger,
                            max_rounds=args.max_rounds,
                            settle_timeout=args.settle_timeout)
            print(f"RESULT: {res.result} in {res.rounds_used} rounds")
        finally:
            try:
                await engine.close()
            except Exception:
                pass

    asyncio.run(_go())


if __name__ == "__main__":
    _main()
