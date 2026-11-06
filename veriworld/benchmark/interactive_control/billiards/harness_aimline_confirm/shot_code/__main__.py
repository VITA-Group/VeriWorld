"""CLI: ``python -m veriworld.benchmark.interactive_control.billiards.harness_aimline_confirm.shot_code``."""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from veriworld.common import RunLogger, VLMClient, load_configs, task_path_from_module
from veriworld.infra.interactive.engine import InteractiveEngine

from .task import run


def _main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--exe", type=Path, required=True,
                   help="Path to demo1.exe (used only to locate the UE screenshot dir)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--port", type=int, default=9003)
    p.add_argument("--max-rounds", type=int, default=5)
    p.add_argument("--max-adjusts-per-round", type=int, default=3)
    p.add_argument("--settle-timeout", type=float, default=None)
    p.add_argument("--difficulty", choices=["standard", "hard"],
                   default="standard")
    p.add_argument("--configs", type=Path, default=Path("model_configs.json"))
    p.add_argument("--model", default="gemini-2.5-flash")
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args()

    logging.basicConfig(level=args.log_level.upper(),
                        format="%(asctime)s %(name)s [%(levelname)s] %(message)s")

    cfgs = load_configs(args.configs)
    cfg = next((c for c in cfgs if c.name == args.model), None)
    if cfg is None:
        raise SystemExit(f"--model {args.model!r} not in {args.configs}")
    vlm = VLMClient.from_config(cfg)

    task_path = task_path_from_module(__package__)
    logger = RunLogger(root=Path.cwd(), seed=args.seed, task_path=task_path)

    async def _go() -> None:
        engine = await InteractiveEngine.attach(f"ws://127.0.0.1:{args.port}")
        try:
            kwargs = dict(
                engine=engine, seed=args.seed, vlm=vlm, logger=logger,
                exe=args.exe,
                max_rounds=args.max_rounds,
                max_adjusts_per_round=args.max_adjusts_per_round,
                difficulty=args.difficulty)
            if args.settle_timeout is not None:
                kwargs["settle_timeout"] = args.settle_timeout
            res = await run(**kwargs)
            print(f"RESULT: {res.result}  pocketed={res.final_pocketed}"
                  f"  cue_out={res.cue_pocketed}  rounds={res.rounds_used}")
        finally:
            try: await engine.stop()
            except Exception: pass  # noqa: BLE001

    asyncio.run(_go())


if __name__ == "__main__":
    _main()
