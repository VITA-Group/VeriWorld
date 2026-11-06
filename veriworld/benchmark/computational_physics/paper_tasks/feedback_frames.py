"""Six fixed-time samples from a recording started after scene construction."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess

def extract_feedback_frames(video: Path, folder: Path) -> list[Path]:
    receipt=json.loads((video.parent/'recording_start.json').read_text())
    if receipt.get('policy')!='after-scene-setup-before-verifier-tick':
        raise RuntimeError('Recording must begin at the explicit scene-ready boundary')
    probe=subprocess.run(['ffprobe','-v','error','-count_frames','-show_entries',
        'stream=nb_read_frames','-of','json',str(video)],capture_output=True,text=True,timeout=60)
    if probe.returncode:raise RuntimeError('Feedback video cannot be decoded')
    count=int(json.loads(probe.stdout)['streams'][0]['nb_read_frames'])
    # Discard two queued framebuffer captures. Selection depends only on time,
    # never on scene colors, target visibility, the action, or the verdict.
    if count<8:raise RuntimeError('Feedback video is too short for six distinct samples')
    selected=[round(2+i*(count-3)/5) for i in range(6)]
    images=[]
    for i,frame in enumerate(selected):
        dst=folder/f'frame_{i}.png'
        result=subprocess.run(['ffmpeg','-v','error','-threads','1','-y','-i',str(video),
            '-vf',f'select=eq(n\\,{frame})','-frames:v','1',str(dst)],capture_output=True,timeout=60)
        if result.returncode or not dst.exists():raise RuntimeError('Video frame decoding failed')
        images.append(dst)
    (folder/'feedback_frame_selection.json').write_text(json.dumps({
        'version':'scene-ready-recording-fixed-time-v3','total_frames':count,
        'discarded_start_frames':2,'selected_frames':selected,
        'images':[{'file':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in images],
    },indent=2),encoding='utf-8')
    return images
