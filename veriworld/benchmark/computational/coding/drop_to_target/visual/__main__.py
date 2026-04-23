"""CLI: ``python -m veriworld.benchmark.computational.coding.drop_to_target.visual``."""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from veriworld.common import RunLogger, VLMClient, load_configs, task_path_from_module
from veriworld.infra.computational.engine import ComputationalEngine

from .task import CONDITION_KEYS, run


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run DropToTarget (pure-visual ablation) for one seed against one model",
    )
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--port", type=int, default=9003)
    p.add_argument("--exe", type=Path, required=True,
                   help="Path to PackagedOutput_dev/Windows/demo1.exe")
    p.add_argument("--configs", type=Path, default=Path("model_configs.json"))
    p.add_argument("--model", type=str, default=None)
    p.add_argument("--output-root", type=Path, default=Path.cwd())
    for key, desc in CONDITION_KEYS.items():
        p.add_argument(f"--{key.replace('_', '-')}", default=None, help=desc)
    p.add_argument("--log-level", default="INFO")
    return p.parse_args()


def _parse_condition(raw: str):
    if raw in ("true", "True"):
        return True
    if raw in ("false", "False"):
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


async def _amain() -> None:
    args = _parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper()))

    cfgs = load_configs(args.configs)
    cfg = next((c for c in cfgs if c.name == args.model), cfgs[0]) if args.model else cfgs[0]
    vlm = VLMClient.from_config(cfg)
    logger = RunLogger(root=args.output_root, seed=args.seed,
                       task_path=task_path_from_module(__package__))
    logger.snapshot_task(__package__, resolved_args=vars(args), invocation="single")

    conditions = {
        key: _parse_condition(getattr(args, key))
        for key in CONDITION_KEYS
        if getattr(args, key) is not None
    }

    engine = ComputationalEngine(exe=args.exe, port=args.port, width=1280, height=720)
    try:
        result = await run(engine, seed=args.seed, vlm=vlm, logger=logger, **conditions)
        print(result)
    finally:
        await engine.close()


if __name__ == "__main__":
    asyncio.run(_amain())
