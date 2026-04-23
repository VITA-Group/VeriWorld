"""CLI: ``python -m veriworld.benchmark.interactive.navigation.mazenavfps.vp_bf``."""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from veriworld.common import RunLogger, VLMClient, load_configs, task_path_from_module
from veriworld.infra.interactive.engine import InteractiveEngine

from .task import run


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="MazeNavFPS VP/Bf ablation")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--port", type=int, default=9003)
    p.add_argument("--configs", type=Path, default=Path("model_configs.json"))
    p.add_argument("--model", type=str, default=None)
    p.add_argument("--output-root", type=Path, default=Path.cwd())
    p.add_argument("--grid-size", type=int, default=4)
    p.add_argument("--materials", type=int, default=3, choices=[1, 2, 3])
    p.add_argument("--max-steps", type=int, default=30)
    p.add_argument("--initial-yaw", type=float, default=None)
    p.add_argument("--log-level", default="INFO")
    return p.parse_args()


async def _amain() -> None:
    a = _args()
    logging.basicConfig(level=getattr(logging, a.log_level.upper()))

    cfgs = load_configs(a.configs)
    cfg = next((c for c in cfgs if c.name == a.model), cfgs[0]) if a.model else cfgs[0]
    vlm = VLMClient.from_config(cfg)
    logger = RunLogger(root=a.output_root, seed=a.seed,
                       task_path=task_path_from_module(__package__))
    logger.snapshot_task(__package__, resolved_args=vars(a), invocation="single")

    engine = await InteractiveEngine.attach(f"ws://127.0.0.1:{a.port}")
    try:
        result = await run(
            engine, seed=a.seed, vlm=vlm, logger=logger,
            grid_size=a.grid_size, materials=a.materials,
            max_steps=a.max_steps, initial_yaw=a.initial_yaw,
        )
        print(result)
    finally:
        await engine.stop()


if __name__ == "__main__":
    asyncio.run(_amain())
