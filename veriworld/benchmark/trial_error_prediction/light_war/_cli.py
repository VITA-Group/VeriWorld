"""Run the paper's LightWar setting with an explicitly selected runtime."""
import argparse
import asyncio
import importlib
import logging
import os
from pathlib import Path

from veriworld.common import RunLogger, VLMClient, load_configs, task_path_from_module
from veriworld.infra.interactive.engine import InteractiveEngine


def main(package):
    parser = argparse.ArgumentParser(description=package.rsplit('.', 1)[-1])
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--port', type=int, default=9008)
    parser.add_argument('--configs', type=Path, default=Path('model_configs.json'))
    parser.add_argument('--model')
    parser.add_argument('--output-root', type=Path, default=Path.cwd())
    parser.add_argument('--max-rounds', type=int, default=8)
    parser.add_argument('--build', type=Path, default=os.environ.get('VERIWORLD_BUILD'))
    parser.add_argument('--width', type=int, default=640)
    parser.add_argument('--height', type=int, default=480)
    parser.add_argument('--attach', action='store_true')
    parser.add_argument('--no-cleanup', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--task', default='hit_forest_hilltop_beacon')
    parser.add_argument('--random-spawn', action='store_true')
    parser.add_argument('--view-seed', type=int)
    parser.add_argument('--intensity-profile', choices=['weak', 'medium', 'strong', 'random'], default='medium')
    parser.add_argument('--height-jitter-cm', type=float, default=0.0)
    parser.add_argument('--log-level', default='INFO')
    args = parser.parse_args()
    if not args.attach and (args.build is None or not args.build.is_file()):
        parser.error('Provide --build /path/to/runtime or set VERIWORLD_BUILD, or use --attach.')
    if args.max_rounds < 1:
        parser.error('--max-rounds must be positive')
    logging.basicConfig(level=getattr(logging, args.log_level.upper()))
    asyncio.run(run(args, package))


async def run(args, package):
    configs = load_configs(args.configs)
    selected = [c for c in configs if c.name == args.model] if args.model else configs[:1]
    if len(selected) != 1:
        raise ValueError('Select exactly one existing model configuration with --model.')
    vlm = VLMClient.from_config(selected[0])
    logger = RunLogger(root=args.output_root, seed=args.seed,
                       task_path=task_path_from_module(package))
    logger.snapshot_task(package, resolved_args=vars(args), invocation='single')
    engine = None
    try:
        if args.attach:
            engine = await InteractiveEngine.attach(f'ws://127.0.0.1:{args.port}')
        else:
            engine = InteractiveEngine(exe=args.build, port=args.port, width=args.width,
                height=args.height, extra_args=['-ForcePython', '-WebSocketAddress=127.0.0.1',
                    '-RenderOffscreen', '-unattended', '-NoCrashDialog'])
            await engine.start()
        task = importlib.import_module(package + '.task')
        print(await task.run(engine, seed=args.seed, vlm=vlm, logger=logger,
                             max_rounds=args.max_rounds, task_id=args.task,
                             random_spawn=args.random_spawn, view_seed=args.view_seed,
                             intensity_profile=args.intensity_profile,
                             tower_height_jitter_cm=args.height_jitter_cm))
    finally:
        # An attached engine has no owned process; stop only closes its socket.
        if engine is not None:
            await engine.stop()
