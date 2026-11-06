"""CLI: ``python -m veriworld.benchmark.photoreal_grounding.object_grounding.harness_grid_iterate``.

Auto-launches v2 build by default; ``--attach`` skips launch.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from veriworld.common.cli import select_config, default_build
from veriworld.common import RunLogger, VLMClient, load_configs, task_path_from_module
from veriworld.infra.interactive.engine import InteractiveEngine

from .task import run

log = logging.getLogger(__name__)

DEFAULT_V2_BUILD = default_build()



def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="object_ground harness_grid_iterate — grid-anchored "
                    "ball-placement probe")
    p.add_argument("--task", type=str, default="find_wooden_spoon_pot_view")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--port", type=int, default=9003)
    p.add_argument("--configs", type=Path, default=Path("model_configs.json"))
    p.add_argument("--model", type=str, default=None)
    p.add_argument("--output-root", type=Path, default=Path.cwd())
    p.add_argument("--max-rounds", type=int, default=6)
    p.add_argument("--max-balls", type=int, default=None,
                   help="Override the spec's max_balls (e.g. 1 for "
                        "single-ball iteration mode).")
    p.add_argument("--grid-n", type=int, default=3)
    p.add_argument("--grid-fov-frac", type=float, default=0.7)
    p.add_argument("--random-view", action="store_true",
                   help="Override the spec's camera_start_pose with a "
                        "random pose that looks at the target. Removes "
                        "view bias across tasks.")
    p.add_argument("--view-seed", type=int, default=None,
                   help="Seed for the random-view sampler (defaults to "
                        "a hash of seed+task_id so different tasks get "
                        "different angles).")
    p.add_argument("--build", type=Path, default=DEFAULT_V2_BUILD)
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=480)
    p.add_argument("--attach", action="store_true",
                   help="Skip launch; connect to UE already running on --port")
    p.add_argument("--no-cleanup", action="store_true",
                   help="Deprecated compatibility flag; only this launcher owns its UE process")
    p.add_argument("--log-level", default="INFO")
    return p.parse_args()


async def _amain() -> None:
    a = _args()
    logging.basicConfig(level=getattr(logging, a.log_level.upper()),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    cfgs = load_configs(a.configs)
    cfg = select_config(cfgs, a.model)
    vlm = VLMClient.from_config(cfg)
    logger = RunLogger(root=a.output_root, seed=a.seed,
                       task_path=task_path_from_module(__package__))
    logger.snapshot_task(__package__, resolved_args=vars(a), invocation="single")

    if a.attach:
        engine = await InteractiveEngine.attach(f"ws://127.0.0.1:{a.port}")
    else:
        if a.build is None:
            raise SystemExit("Pass --build with the runtime executable, or set VERIWORLD_BUILD")
        if not a.build.is_file():
            raise SystemExit(f"build not found: {a.build}")
        engine = await InteractiveEngine.launch(
            exe=a.build, port=a.port, width=a.width, height=a.height,
        )
    try:
        result = await run(
            engine, seed=a.seed, vlm=vlm, logger=logger,
            task_id=a.task, max_rounds=a.max_rounds,
            grid_n=a.grid_n, grid_fov_fraction=a.grid_fov_frac,
            max_balls_override=a.max_balls,
            random_view=a.random_view, view_seed=a.view_seed,
        )
        print(result)
    finally:
        if not a.attach:
            await engine.stop()


if __name__ == "__main__":
    asyncio.run(_amain())
