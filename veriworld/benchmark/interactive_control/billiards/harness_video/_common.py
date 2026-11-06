"""Read only the recording returned by this runtime's StopRecording call."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import uuid
from .._transport import runtime_output


async def stop_recording(engine) -> dict:
    marker = 'VW_BILLIARDS_VIDEO_' + uuid.uuid4().hex + ' '
    reply = await engine.python_exec(
        'import unreal_runtime as ur, json\nprint(' + repr(marker) + '+json.dumps(ur.StopRecording()))')
    lines = [line.split(marker, 1)[1] for line in runtime_output(reply).splitlines() if marker in line]
    if len(lines) != 1:
        raise RuntimeError('Runtime did not return the current billiards recording')
    recording = json.loads(lines[0])
    if not isinstance(recording, dict) or not recording.get('full_path'):
        raise RuntimeError('Billiards recording response lacks its file path')
    return recording


def harvest_video(recording: dict, dest_mp4: Path) -> Path:
    """Copy/remux the explicitly named recording and retain the raw evidence."""
    if not isinstance(recording, dict) or not recording.get('full_path'):
        raise RuntimeError('Billiards recording path is missing')
    source = Path(recording['full_path'])
    if not source.is_file() or source.stat().st_size == 0:
        raise RuntimeError('Billiards recording is absent or empty')
    dest_mp4.parent.mkdir(parents=True, exist_ok=True)
    local = dest_mp4.with_suffix('.h264')
    if source.resolve() != local.resolve(): shutil.copy2(source, local)
    result = subprocess.run(['ffmpeg','-v','error','-y','-i',str(local),'-c:v','copy',str(dest_mp4)],
                            capture_output=True, timeout=60)
    if result.returncode or not dest_mp4.is_file() or dest_mp4.stat().st_size == 0:
        raise RuntimeError('Billiards recording could not be remuxed; raw input retained')
    receipt = dict(recording, raw_file=local.name, video_file=dest_mp4.name,
                   raw_sha256=hashlib.sha256(local.read_bytes()).hexdigest(),
                   video_sha256=hashlib.sha256(dest_mp4.read_bytes()).hexdigest())
    dest_mp4.with_suffix('.recording.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    return dest_mp4


__all__ = ['stop_recording', 'harvest_video']
