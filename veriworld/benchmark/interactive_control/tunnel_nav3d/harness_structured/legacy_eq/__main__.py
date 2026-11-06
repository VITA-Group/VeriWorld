"""CLI: ``python -m veriworld.benchmark.interactive_control.tunnel_nav3d.harness_structured.legacy_eq``.

Legacy-faithful TunnelNav3D arm with an equal primitive-move budget. Select the
action protocol (AF/Bf/Sf) and the information condition (VP/VRP); every engine
command spends one move from the shared budget K (the paper's equal-budget study
uses K=80). Attaches to an already-running runtime, like the other TunnelNav3D
structured harnesses.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from veriworld.common.cli import select_config
from veriworld.common import RunLogger, VLMClient, load_configs, task_path_from_module
from veriworld.infra.interactive.engine import InteractiveEngine

from .task import run


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="TunnelNav3D legacy arm, equal primitive-move budget")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--port", type=int, default=9003)
    p.add_argument("--configs", type=Path, default=Path("model_configs.json"))
    p.add_argument("--model", type=str, default=None)
    p.add_argument("--output-root", type=Path, default=Path.cwd())
    p.add_argument("--protocol", choices=["AF", "Bf", "Sf"], default="Bf",
                   help="action format: AF (aim-and-fly), Bf (batch free), Sf (single free)")
    p.add_argument("--info", choices=["VP", "VRP"], default="VP",
                   help="information: VP (image + position log) or VRP (adds a raycast scan)")
    p.add_argument("--tunnel-radius", type=float, default=80.0)
    p.add_argument("--colorful", type=str, default="true", choices=["true", "false", "True", "False"])
    p.add_argument("--cardinal", type=str, default="true", choices=["true", "false", "True", "False"])
    p.add_argument("--move-budget", type=int, default=80,
                   help="shared primitive-move budget K (the paper's equal-budget study used 80)")
    p.add_argument("--max-calls", type=int, default=80,
                   help="safety cap on API calls; the run also stops at the goal or when the budget is spent")
    p.add_argument("--repeat", type=int, default=0)
    p.add_argument("--log-level", default="INFO")
    return p.parse_args()


async def _amain() -> None:
    a = _args()
    logging.basicConfig(level=getattr(logging, a.log_level.upper()))

    cfgs = load_configs(a.configs)
    cfg = select_config(cfgs, a.model)
    vlm = VLMClient.from_config(cfg)
    logger = RunLogger(root=a.output_root, seed=a.seed,
                       task_path=task_path_from_module(__package__))
    logger.snapshot_task(__package__, resolved_args=vars(a), invocation="single")

    engine = await InteractiveEngine.attach(f"ws://127.0.0.1:{a.port}")
    try:
        result = await run(
            engine, seed=a.seed, vlm=vlm, logger=logger,
            protocol=a.protocol, info=a.info,
            tunnel_radius=a.tunnel_radius,
            colorful=a.colorful.lower() == "true",
            cardinal=a.cardinal.lower() == "true",
            move_budget=a.move_budget, max_calls=a.max_calls, repeat=a.repeat,
        )
        print(result)
    finally:
        await engine.stop()


if __name__ == "__main__":
    asyncio.run(_amain())
