"""Run one paper physics episode using an explicit API configuration."""
import argparse
import asyncio
from pathlib import Path
from veriworld.common import VLMClient
from veriworld.common.vlm import load_configs
from veriworld.infra.computational.engine import ComputationalEngine
from .task import run
from .variants import VARIANTS

async def main(a):
    configs=load_configs(a.config)
    cfg=next(c for c in configs if c.name==a.model)
    output=a.output.resolve()
    home=output.parent/(output.name+'_runtime');home.mkdir(parents=True,exist_ok=True)
    engine=ComputationalEngine(a.build.resolve(),a.port,width=640,height=480,
        extra_args=['-UserDir='+str(home),'-unattended','-NoCrashDialog'])
    try:
        await run(engine,a.task,a.seed,a.condition,VLMClient.from_config(cfg,timeout=a.api_timeout),output,a.max_rounds,variant=a.variant)
    finally:await engine.close()

p=argparse.ArgumentParser()
p.add_argument('--build',type=Path,required=True);p.add_argument('--config',type=Path,required=True)
p.add_argument('--model',required=True);p.add_argument('--task',choices=['curvy','drop'],required=True)
p.add_argument('--condition',choices=['V','S','C'],required=True);p.add_argument('--seed',type=int,required=True)
p.add_argument('--port',type=int,default=19550);p.add_argument('--max-rounds',type=int,default=5)
p.add_argument('--api-timeout',type=float,default=600)
p.add_argument('--variant',choices=VARIANTS,default='standard')
p.add_argument('--output',type=Path,required=True)
asyncio.run(main(p.parse_args()))
