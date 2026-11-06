"""Transport checks shared by the billiards harnesses; no scoring rules."""
import json
import math
from pathlib import Path


def runtime_output(value):
    for _ in range(8):
        if isinstance(value, dict):
            if value.get('error') or value.get('success') is False:
                raise RuntimeError('Billiards runtime execution failed')
            for key in ('result', 'output', 'stdout'):
                if key in value:
                    value = value[key]
                    break
            else:
                raise RuntimeError('Missing billiards runtime output')
        elif isinstance(value, str):
            if 'Traceback (most recent call last):' in value:
                raise RuntimeError('Billiards runtime reported a Python exception')
            try:
                decoded = json.loads(value)
            except ValueError:
                return value
            if isinstance(decoded, dict) and any(k in decoded for k in ('result','output','stdout','error','success')):
                value = decoded
            else:
                return value
        else:
            raise RuntimeError('Invalid billiards runtime output')
    raise RuntimeError('Billiards runtime envelope exceeds nesting limit')


def save_raw_response(vlm, path: Path):
    response = getattr(vlm, 'last_response', None)
    if response is not None:
        path.write_text(json.dumps(response, ensure_ascii=False, default=str), encoding='utf-8')


def require_state(path: Path):
    """Missing engine state must not silently remove the structured channel."""
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
        balls = state['balls']
        count = state['alive_target']
        assert isinstance(balls, dict)
        assert type(count) is int and count >= 0
        assert type(state['cue_scratched']) is bool
        for position in balls.values():
            assert isinstance(position, list) and len(position) == 3
            assert all(type(value) in (int, float) and math.isfinite(value) for value in position)
    except (OSError, ValueError, KeyError, TypeError, AssertionError) as error:
        raise RuntimeError('Missing or invalid billiards structured state') from error
    return state
