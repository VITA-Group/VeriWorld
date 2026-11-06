"""CLI: ``python -m veriworld.benchmark.photoreal_grounding.scene_grounding.harness_walk``.

Auto-launches a v2 build by default; pass ``--attach`` to connect to an
already-running UE.
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

LEVELS = ["living_room", "aiue"]



def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="scene_ground harness_walk - recover a target view "
                    "by walking the scene")
    p.add_argument("--level", type=str, default="living_room", choices=LEVELS,
                   help="which photoreal v2 level to load")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--port", type=int, default=9003)
    p.add_argument("--configs", type=Path, default=Path("model_configs.json"))
    p.add_argument("--model", type=str, default=None)
    p.add_argument("--output-root", type=Path, default=Path.cwd())
    p.add_argument("--max-rounds", type=int, default=15)
    p.add_argument("--tolerance-xy-cm", type=float, default=None,
                   help="position tolerance for PASS (default from groundtruth.json)")
    p.add_argument("--tolerance-yaw-deg", type=float, default=None,
                   help="yaw tolerance for PASS (default from groundtruth.json)")
    p.add_argument("--view-seed", type=int, default=None,
                   help="override deterministic view-seed (default = "
                        "hash(level, seed))")
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
        log.info("--attach: connecting to existing UE on port %d", a.port)
        engine = await InteractiveEngine.attach(f"ws://127.0.0.1:{a.port}")
    else:
        if a.build is None:
            raise SystemExit("Pass --build with the runtime executable, or set VERIWORLD_BUILD")
        if not a.build.is_file():
            raise SystemExit(f"build not found: {a.build}")
        log.info("launching UE on port %d (build=%s)", a.port, a.build)
        engine = await InteractiveEngine.launch(
            exe=a.build, port=a.port, width=a.width, height=a.height,
        )
    try:
        result = await run(
            engine, seed=a.seed, vlm=vlm, logger=logger,
            level=a.level, max_rounds=a.max_rounds,
            tolerance_xy_cm=a.tolerance_xy_cm,
            tolerance_yaw_deg=a.tolerance_yaw_deg,
            view_seed=a.view_seed,
        )
        print(result)
    finally:
        if not a.attach:
            await engine.stop()


if __name__ == "__main__":
    asyncio.run(_amain())
