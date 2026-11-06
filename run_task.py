"""Portable launcher for one task, one model and one seed; no batch defaults."""
from __future__ import annotations
import argparse, asyncio, hashlib, json, os, runpy, shutil, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PREFIX = 'veriworld.benchmark.'
TASKS = {
 'PlaceOnBottom': ('interactive_control.place_on_bottom.harness_vision', 8),
 'BoxFold': ('trial_error_prediction.box_fold.harness_vision', 10),
 'MovingShooter': ('trial_error_prediction.moving_shooter.harness_vision_frames', 10),
 'LightWar': ('trial_error_prediction.light_war.harness_arc_visual_only', 10),
 'MazeNavFPS': ('interactive_control.mazenavfps.harness_structured.vp_bf', 50),
 'TunnelNav3D': ('interactive_control.tunnel_nav3d.harness_structured.vp_bf', 50),
 'Billiards': ('interactive_control.billiards.harness_image.shot_code', 10),
 'SceneGrounding': ('photoreal_grounding.scene_grounding.harness_walk', 10),
 'ObjectGrounding': ('photoreal_grounding.object_grounding.harness_grid_iterate_offcenter', 10),
 'DropToTarget': ('computational_physics.paper_tasks', 5),
 'CurvyBilliards': ('computational_physics.paper_tasks', 5),
}
ATTACHED = {'MazeNavFPS', 'TunnelNav3D', 'Billiards'}

def parse():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--task',choices=TASKS)
 p.add_argument('--seed',type=int,default=0)
 p.add_argument('--model')
 p.add_argument('--configs',type=Path,default=ROOT/'model_configs.json')
 p.add_argument('--build',type=Path)
 p.add_argument('--output',type=Path)
 p.add_argument('--port',type=int,default=19001)
 p.add_argument('--condition',choices=['V','S','C','V-pure'],default='V',help="V-pure: pure-visual control for PlaceOnBottom (image + anonymous target IDs), BoxFold (image + hinge IDs) and LightWar (image only); task-side numeric references removed")
 p.add_argument('--protocol',choices=['batch-eq','single'],help='MazeNavFPS equal primitive-move-budget protocol (batch-eq=vp_bf_eq, single=vp_sa)')
 p.add_argument('--move-budget',type=int,help='Shared primitive-move budget K for --protocol (default by grid 3/4/5 -> 125/170/210)')
 p.add_argument('--rounds',type=int)
 p.add_argument('--variant',choices=['standard','rotate90','mirror-x','wide-target'],default='standard')
 p.add_argument('--grid-size',type=int,default=5)
 p.add_argument('--level',choices=['living_room','aiue'],default='living_room')
 p.add_argument('--target',default='find_wooden_spoon_pot_view')
 p.add_argument('--smoke',action='store_true',help='One-round installation check, not a paper score')
 p.add_argument('--dry-run',action='store_true',help='Print CLI arguments without starting a runtime or API call')
 p.add_argument('--check',action='store_true',help='Check local dependencies and runtime paths without an API call')
 p.add_argument('--list',action='store_true')
 return p,p.parse_args()

def runtime_path(explicit):
 if explicit:return explicit.expanduser().resolve()
 if os.environ.get('VERIWORLD_BUILD'):return Path(os.environ['VERIWORLD_BUILD']).expanduser().resolve()
 names=['runtime/VeriWorld_paper_runtime/demo1/Binaries/Win64/demo1-Win64-Shipping.exe',
        'runtime/VeriWorld_paper_runtime/demo1/Binaries/Linux/demo1-Linux-Shipping']
 for name in names:
  if (ROOT/name).is_file():return ROOT/name
 raise ValueError('Runtime not found. Use --build with the packaged executable, or set VERIWORLD_BUILD.')

def arguments(a,build,out):
 module=PREFIX+TASKS[a.task][0]
 if a.condition=='V-pure' and a.task not in {'PlaceOnBottom','BoxFold','LightWar'}:
  raise ValueError('--condition V-pure is defined only for PlaceOnBottom, BoxFold and LightWar')
 if a.protocol and a.task!='MazeNavFPS':
  raise ValueError('--protocol applies only to MazeNavFPS (equal primitive-move-budget batch vs single)')
 if a.task in {'PlaceOnBottom','BoxFold','MovingShooter','LightWar'}:
  mapping={'V':TASKS[a.task][0].rsplit('.',1)[1], 'S':'harness_symbolic',
           'C':'harness_combined_frames' if a.task=='MovingShooter' else 'harness_combined'}
  mapping['V-pure']=mapping['V']  # pure-visual runs the V harness; the outbound transform is applied by the worker
  module=module.rsplit('.',1)[0]+'.'+mapping[a.condition]
 elif a.task=='MazeNavFPS' and a.protocol:
  module=module.rsplit('.',1)[0]+'.'+('vp_bf_eq' if a.protocol=='batch-eq' else 'vp_sa')
 elif a.task in {'MazeNavFPS','TunnelNav3D','Billiards','SceneGrounding','ObjectGrounding'} and a.condition!='V':
  raise ValueError('This quickstart exposes the named visual/VP harness. Use task_entrypoints.json for its other variants; no automatic V/S/C substitution.')
 rounds=1 if a.smoke else a.rounds
 if rounds is not None and rounds<1:raise ValueError('Rounds must be positive')
 K=None
 if a.task=='MazeNavFPS' and a.protocol:
  K=a.move_budget if a.move_budget is not None else {3:125,4:170,5:210}.get(a.grid_size)
  if K is None:raise ValueError('--move-budget is required for grids other than 3/4/5')
  if K<1:raise ValueError('--move-budget must be positive')
  if not a.smoke:rounds=K  # at most one primitive move per turn, so K turns cover the K-move budget
 args=['--seed',str(a.seed),'--port',str(a.port),'--model',a.model]
 physics=a.task in {'DropToTarget','CurvyBilliards'}
 if a.variant!='standard' and not physics:raise ValueError('Parameter variants apply only to the two physics tasks')
 args += ['--config' if physics else '--configs',str(a.configs.resolve())]
 if rounds is not None:args += ['--max-steps' if a.task in {'MazeNavFPS','TunnelNav3D'} else '--max-rounds',str(rounds)]
 if physics:
  if a.variant!='standard':args += ['--variant',a.variant]
  args += ['--build',str(build),'--output',str(out/'episode'),'--task','drop' if a.task=='DropToTarget' else 'curvy','--condition',a.condition]
 elif a.task=='Billiards':args += ['--exe',str(build)]
 else:
  args += ['--output-root',str(out/'episode')]
  if a.task not in ATTACHED:args += ['--build',str(build)]
 if a.task=='MazeNavFPS':args += ['--grid-size',str(a.grid_size)]
 if a.task=='MazeNavFPS' and a.protocol=='batch-eq':args += ['--move-budget',str(K)]
 if a.task=='SceneGrounding':args += ['--level',a.level]
 if a.task=='ObjectGrounding':args += ['--task',a.target,'--random-view']
 if a.task=='LightWar':args += ['--random-spawn','--intensity-profile','random','--height-jitter-cm','200']
 return module,args

def worker(receipt):
 """Record the public client's inputs and replies. The V/S/C task actions are
 unchanged; for the PlaceOnBottom pure-visual condition the outbound messages
 are rewritten here (the harness itself is untouched)."""
 from veriworld.common import VLMClient
 spec=json.loads(receipt.read_text(encoding='utf-8'))
 folder=receipt.parent/'api';folder.mkdir()
 original=VLMClient.chat
 pure=None
 if spec.get('pure_visual'):
  task=spec['task']
  if task=='PlaceOnBottom':
   from veriworld.benchmark.interactive_control.place_on_bottom.pure_visual import PureVisualOutbound
   pure=PureVisualOutbound()
  elif task=='BoxFold':
   from veriworld.benchmark.trial_error_prediction.box_fold.pure_visual import PureVisualOutbound
   pure=PureVisualOutbound()
  elif task=='LightWar':
   from veriworld.benchmark.trial_error_prediction.light_war.pixel_only import install
   from veriworld.benchmark.trial_error_prediction.light_war.harness_arc import task as arc
   install(arc,folder/'pixel_only')
  else:
   raise ValueError('pure-visual condition not defined for '+task)
 count=0
 def recorded(client,messages,*args,**kwargs):
  nonlocal count
  deadline=os.environ.get('VERIWORLD_DEADLINE_EPOCH')
  if deadline and time.time()>=float(deadline):raise RuntimeError('Run deadline reached before API call')
  count+=1
  p=folder/f'{count:03d}';p.mkdir()
  def save(name,value):
   data=json.dumps(value,ensure_ascii=False,indent=2,default=str)
   if client.api_key:data=data.replace(client.api_key,'[REDACTED]')
   (p/name).write_text(data,encoding='utf-8')
  send=messages
  if pure is not None:
   send,proof=pure.apply(messages)
   save('reference_v.json',messages)
   save('pure_visual_diff.json',proof)
  save('messages.json',send)
  row={'started':time.time(),'request_model':client.model,'status':'pending'}
  save('receipt.json',row)
  try:
   answer=original(client,send,*args,**kwargs)
   if pure is not None:pure.observe_reply(answer)
   row.update(status='returned',returned_model=client.last_returned_model)
   save('raw_response.json',client.last_response)
   return answer
  except Exception as e:
   row.update(status='error',error_type=type(e).__name__)
   raise
  finally:
   row['finished']=time.time();save('receipt.json',row)
 VLMClient.chat=recorded
 sys.argv=[spec['module']]+spec['arguments']
 runpy.run_module(spec['module'],run_name='__main__')

async def launch(a,build,out,spec):
 import psutil
 from veriworld.infra.interactive.engine import InteractiveEngine
 engine=None;process=None
 try:
  if a.task in ATTACHED:
   engine=InteractiveEngine(build,a.port,extra_args=['-UserDir='+str(out/'runtime'),'-ForcePython','-WebSocketAddress=127.0.0.1','-RenderOffscreen','-unattended','-NoCrashDialog'])
   await engine.start(connect_timeout=120,warmup_seconds=30)
  env=dict(os.environ,PYTHONPATH=str(ROOT),PYTHONDONTWRITEBYTECODE='1')
  process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'_worker',str(out/'invocation.json')],cwd=out,env=env)
  while process.poll() is None:
   deadline=env.get('VERIWORLD_DEADLINE_EPOCH')
   if deadline and time.time()>=float(deadline):raise TimeoutError('Run deadline reached')
   await asyncio.sleep(.5)
  return process.returncode
 finally:
  if process is not None and process.poll() is None:
   try:children=psutil.Process(process.pid).children(recursive=True)
   except psutil.NoSuchProcess:children=[]
   process.terminate()
   for child in reversed(children):
    try:child.terminate()
    except psutil.NoSuchProcess:pass
   try:process.wait(timeout=5)
   except subprocess.TimeoutExpired:process.kill()
  if engine is not None:await engine.stop()

def main():
 if len(sys.argv)>1 and sys.argv[1]=='_worker':return worker(Path(sys.argv[2]))
 parser,a=parse()
 if a.list:
  for task in TASKS:print(task)
  return 0
 build=runtime_path(a.build)
 if not build.is_file():parser.error('Runtime executable does not exist: '+str(build))
 if a.check:
  import requests,websockets,PIL,psutil
  from veriworld.common import load_configs
  result={'python':sys.version.split()[0],'runtime':str(build),'runtime_exists':True,
          'ffmpeg':shutil.which('ffmpeg'),'ffprobe':shutil.which('ffprobe'),'api_called':False}
  if a.configs.exists():result['configured_models']=[c.name for c in load_configs(a.configs)]
  print(json.dumps(result,indent=2));return 0
 if not a.task or not a.model:parser.error('--task and --model are required')
 if not a.configs.is_file():parser.error('Create model_configs.json from an example and set VERIWORLD_API_KEY')
 from veriworld.common import load_configs,VLMClient
 from veriworld.common.cli import select_config
 VLMClient.from_config(select_config(load_configs(a.configs),a.model))
 out=(a.output or ROOT/'runs'/f'{a.task}_{a.seed}_{time.time_ns()}').resolve()
 module,args=arguments(a,build,out)
 spec={'task':a.task,'seed':a.seed,'model_config':a.model,'module':module,'arguments':args,
       'qualification_only':a.smoke,'parameter_variant':a.variant,'created':time.time(),'runtime':str(build),'output':str(out),
       'pure_visual':a.condition=='V-pure','protocol':a.protocol}
 if a.dry_run:print(json.dumps(spec,indent=2));return 0
 if a.task in {'MovingShooter','DropToTarget','CurvyBilliards'} and not all(shutil.which(x) for x in ('ffmpeg','ffprobe')):
  parser.error('Install ffmpeg and ffprobe and put both on PATH before this task')
 out.mkdir(parents=True,exist_ok=False)
 (out/'invocation.json').write_text(json.dumps(spec,indent=2),encoding='utf-8')
 code=asyncio.run(launch(a,build,out,spec))
 (out/'exit.json').write_text(json.dumps({'exit_code':code,'finished':time.time(),'qualification_only':a.smoke}),encoding='utf-8')
 return code

if __name__=='__main__':raise SystemExit(main())
