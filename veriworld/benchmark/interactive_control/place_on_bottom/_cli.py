"""Run the paper's PlaceOnBottom setting with an explicitly selected runtime."""
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
    parser.add_argument('--port', type=int, default=9006)
    parser.add_argument('--configs', type=Path, default=Path('model_configs.json'))
    parser.add_argument('--model')
    parser.add_argument('--output-root', type=Path, default=Path.cwd())
    parser.add_argument('--max-rounds', type=int, default=None)
    parser.add_argument('--build', type=Path, default=os.environ.get('VERIWORLD_BUILD'))
    parser.add_argument('--width', type=int, default=640)
    parser.add_argument('--height', type=int, default=480)
    parser.add_argument('--attach', action='store_true')
    parser.add_argument('--no-cleanup', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--pad-words', type=int, default=750)
    parser.add_argument('--max-total-actions', type=int, default=6)
    parser.add_argument('--batch-cap', type=int, default=5)
    parser.add_argument('--log-level', default='INFO')
    args = parser.parse_args()
    if not args.attach and (args.build is None or not args.build.is_file()):
        parser.error('Provide --build /path/to/runtime or set VERIWORLD_BUILD, or use --attach.')
    if args.max_rounds is None:
        args.max_rounds = (3 if package.endswith('_batch') else
                           6 if package.endswith(('_padded', '_padded_natural')) else 8)
    if args.max_total_actions < 1 or args.batch_cap < 1 or args.pad_words < 1:
        parser.error('Action budgets and padding length must be positive')
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
        extra = {}
        if package.endswith(('_padded', '_padded_natural')):
            extra['pad_words'] = args.pad_words
        if package.endswith('_batch'):
            extra.update(max_total_actions=args.max_total_actions, batch_cap=args.batch_cap)
        print(await task.run(engine, seed=args.seed, vlm=vlm, logger=logger,
                             max_rounds=args.max_rounds, **extra))
    finally:
        # An attached engine has no owned process; stop only closes its socket.
        if engine is not None:
            await engine.stop()
