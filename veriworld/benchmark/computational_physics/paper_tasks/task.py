"""Portable V/S/C runner for the original rolling-ball and surface-design tasks."""
from __future__ import annotations
import asyncio
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import time
from websockets.exceptions import ConnectionClosed

from veriworld.common import png_to_base64_url, format_prompt_txt
from veriworld.common.screenshot import capture_runtime_screenshot
from .feedback_frames import extract_feedback_frames
from .action_parsing import surface_source, curvy_action, PARSER_VERSION
from .variants import parameters as variant_parameters

HERE=Path(__file__).parent

def unwrap(value):
    for _ in range(8):
        if isinstance(value,dict):
            for key in ('output','stdout','result'):
                if key in value:value=value[key];break
            else:return str(value)
        elif isinstance(value,str):
            try:value=json.loads(value)
            except ValueError:return value
        else:return str(value)
    return str(value)

def clean_state(params):
    return {k:v for k,v in params.items() if k not in ('solution','reference_action','witness')}

def submission(task,folder,response,params):
    source=(HERE/task/'submission_template.py').read_text(encoding='utf-8').replace('__PAPER_CASE_DIR__',repr(folder.as_posix()))
    if task=='curvy':
        action,angle,speed=curvy_action(response,params['max_speed'])
        source=source.replace('__AGENT_ANGLE__',repr(angle)).replace('__AGENT_SPEED__',repr(speed))
        return source,action
    function=surface_source(response)
    env={'math':math,'SURFACE_Z':params['surface_z'],'__builtins__':{'abs':abs,'min':min,'max':max}}
    exec(compile(function,'agent_surface','exec'),env)
    n=params['grid_n'];spacing=params['grid_spacing']
    for i in range(n):
        for j in range(n):
            z=env['surface_height']((i-n/2)*spacing,(j-n/2)*spacing)
            if not isinstance(z,(int,float)) or not math.isfinite(z) or abs(z)>100000:
                raise ValueError('Invalid surface height')
    return source.replace('__AGENT_SURFACE_FUNCTION__',function),{'surface_height':function}

async def execute(engine,source,folder):
    reply=await engine.python_exec(source)
    (folder/'execution.json').write_text(json.dumps(reply,indent=2),encoding='utf-8')
    out=unwrap(reply)
    if '[Exception]' in out or 'Traceback' in out or 'SetShader: False' in out or reply.get('error'):
        raise RuntimeError('Runtime execution failed; inspect execution.json')
    return reply

async def _rollout_once(engine,source,folder):
    await engine.next_round('/Game/Levels/Axis',connect_timeout=150)
    await asyncio.sleep(1)
    # Both templates register the verifier tick only after constructing all
    # geometry, materials and the camera. Start recording at this explicit
    # boundary instead of recording the loading/setup frames. Do not require
    # the target to stay visible: an agent's surface may legitimately occlude it.
    marker='ur.submit_tick_task('
    if source.count(marker)!=1:raise RuntimeError('Missing unique scene-ready boundary')
    recording="print('VW_RECORD '+json.dumps(ur.StartRecording(width=640,height=480,fps=30,bitrate=3000000,codec='h264')))\n"
    reply=await execute(engine,source.replace(marker,recording+marker,1),folder)
    record_lines=[x.split('VW_RECORD ',1)[1] for x in unwrap(reply).splitlines() if 'VW_RECORD ' in x]
    if not record_lines:raise RuntimeError('Scene-ready recording did not start')
    (folder/'recording_start.json').write_text(json.dumps({'policy':'after-scene-setup-before-verifier-tick','reply':json.loads(record_lines[-1])},indent=2),encoding='utf-8')
    log=folder/'lean_verify/log_for_verify.txt'
    deadline=time.monotonic()+110
    text=''
    while time.monotonic()<deadline:
        if log.exists():text=log.read_text(encoding='utf-8')
        if re.search(r'^# RESULT: (PASS|FAIL)',text,re.M):break
        if engine._proc is not None and engine._proc.poll() is not None:
            raise RuntimeError('Physics process exited before a terminal verdict')
        await asyncio.sleep(.5)
    else:raise RuntimeError('No terminal physics verdict; do not score as model failure')
    response=await engine.python_exec("import unreal_runtime as ur,json\nprint('VW_VIDEO '+json.dumps(ur.StopRecording()))")
    out=unwrap(response)
    lines=[x.split('VW_VIDEO ',1)[1] for x in out.splitlines() if 'VW_VIDEO ' in x]
    if not lines:raise RuntimeError('Recording did not return its file')
    video=Path(json.loads(lines[-1])['full_path'])
    if not video.is_file() or video.stat().st_size==0:raise RuntimeError('Empty recording')
    local=folder/('rollout'+video.suffix);shutil.copy2(video,local)
    images=extract_feedback_frames(local,folder)
    verdict=re.findall(r'^# RESULT: (PASS|FAIL)',text,re.M)[-1]
    return verdict,text,images

async def rollout(engine,source,folder):
    # Replay the identical committed action after a process or bridge failure, without
    # another model call or another scored attempt. Preserve failed evidence.
    for attempt in range(3):
        try:return await _rollout_once(engine,source,folder)
        except Exception as error:
            proc=engine._proc
            transport_closed=isinstance(error,ConnectionClosed)
            process_exited=proc is not None and proc.poll() is not None
            if not (transport_closed or process_exited) or attempt==2:raise
            failure=folder/f'runtime_crash_{attempt+1}'
            failure.mkdir(exist_ok=False)
            (failure/'error.json').write_text(json.dumps({'error':str(error),'error_type':type(error).__name__,'exit_code':None if proc is None else proc.returncode,'transport_closed':transport_closed,'same_action_replay':True}),encoding='utf-8')
            await engine.close()
            for path in [folder/'execution.json',folder/'recording_start.json',*list((folder/'lean_verify').glob('log*.txt'))]:
                if path.exists():shutil.move(str(path),str(failure/path.name))
            await asyncio.sleep(2)

async def run(engine,task,seed,condition,vlm,workspace,max_rounds=5,variant='standard'):
    if task not in ('curvy','drop') or condition not in ('V','S','C'):raise ValueError('Unknown task or condition')
    instance=HERE/task/f'seed{seed}'
    if not instance.exists():raise ValueError('Use a reference-validated released seed')
    workspace=Path(workspace);workspace.mkdir(parents=True,exist_ok=False)
    params=json.loads((instance/'params.json').read_text(encoding='utf-8'))
    params=variant_parameters(task,params,variant)
    (workspace/'params.json').write_text(json.dumps(params,indent=2),encoding='utf-8')
    if variant!='standard':
        (workspace/'variant.json').write_text(json.dumps({'base_seed':seed,'variant':variant,'scope':'Separate parameter study, not an original paper instance'},indent=2),encoding='utf-8')
    state=clean_state(params)
    common={k:state[k] for k in ['grid_n','grid_spacing','ball_radius','max_speed','friction','start','surface_z','ball_start'] if k in state}
    if task=='curvy':
        instruction='Aim a ball from the fixed start so it settles inside the red target on the curved surface. Return one JSON object with v_angle in radians and v_speed in cm/s. The start is fixed.'
    else:
        instruction='Design the surface so the falling ball first touches the ground within the red target circle. Return only a Python function def surface_height(x, y) giving the surface height in cm. You may use numeric arithmetic, if statements, abs/min/max and math functions. SURFACE_Z is the initial surface height. Grid coordinates and physics remain fixed.'
    system=instruction+'\nYou have '+str(max_rounds)+' attempts. Each simulation runs for at most '+('90' if task=='curvy' else '60')+' seconds.\nKnown setup: '+json.dumps(common)
    if condition in ('S','C'):system+='\nStructured scene state: '+json.dumps(state)
    obs=workspace/'observe';shutil.copytree(instance,obs)
    if variant!='standard':(obs/'params.json').write_text(json.dumps(params,indent=2),encoding='utf-8')
    (obs/'lean_verify').mkdir(exist_ok=True)
    for p in obs.rglob('log*.txt'):p.unlink()
    await engine.next_round('/Game/Levels/Axis',connect_timeout=150)
    observe=(HERE/task/'observe.py').read_text(encoding='utf-8').replace('__PAPER_CASE_DIR__',repr(obs.as_posix()))
    await execute(engine,observe,obs);await asyncio.sleep(3)
    initial=await capture_runtime_screenshot(engine,'initial',workspace)
    images=[initial];feedback='';history=[];verdict='FAIL'
    for round_no in range(1,max_rounds+1):
        folder=workspace/f'round_{round_no:02d}';shutil.copytree(instance,folder)
        if variant!='standard':(folder/'params.json').write_text(json.dumps(params,indent=2),encoding='utf-8')
        (folder/'lean_verify').mkdir(exist_ok=True)
        for p in folder.rglob('log*.txt'):p.unlink()
        text=f'Attempt {round_no}/{max_rounds}.\n'+feedback
        parts=[{'type':'text','text':text}]
        if condition in ('V','C'):
            parts += [{'type':'image_url','image_url':{'url':png_to_base64_url(p)}} for p in images]
        messages=[{'role':'system','content':system},*history,{'role':'user','content':parts}]
        (folder/'prompt.txt').write_text(format_prompt_txt(messages,[p.name for p in images] if condition in ('V','C') else []),encoding='utf-8')
        response=await asyncio.get_running_loop().run_in_executor(None,vlm.chat,messages)
        (folder/'response.txt').write_text(response,encoding='utf-8')
        history.extend([{'role':'user','content':text},{'role':'assistant','content':response}])
        try:source,action=submission(task,folder,response,params)
        except (ValueError,KeyError,NameError,SyntaxError,TypeError,OverflowError,ZeroDivisionError) as error:
            feedback='Invalid action: '+str(error)
            (folder/'invalid_action.json').write_text(json.dumps({'error':str(error)}));continue
        (folder/'action.json').write_text(json.dumps(action,indent=2),encoding='utf-8')
        (folder/'submission.py').write_text(source,encoding='utf-8')
        verdict,trajectory,images=await rollout(engine,source,folder)
        feedback='Verifier result: '+verdict
        if condition in ('S','C'):feedback+='\nTrajectory:\n'+trajectory
        if verdict=='PASS':break
    result={'task':task,'seed':seed,'condition':condition,'model':vlm.model,'result':verdict,'rounds':round_no,
            'valid':True,'harness_version':'paper-physics-vsc-20261102-v3-parserfix2','action_parser_version':PARSER_VERSION,'max_rounds':max_rounds}
    if variant!='standard':result['parameter_variant']=variant
    (workspace/'episode_summary.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result
