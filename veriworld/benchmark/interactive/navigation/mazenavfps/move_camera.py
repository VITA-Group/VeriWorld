"""
move_camera.py — Persistent tick_task for FPS navigation.

Supports two command formats:
  Legacy: "forward", "backward", "turn_left", "turn_right"
  Free:   {"cmd": "forward", "distance": 150}
          {"cmd": "backward", "distance": 50}
          {"cmd": "turn", "degrees": 45}

Blocked detection: stuck-based (consecutive ticks with no position change),
NOT tick-count-based. See handbook/camera_movement_notes.md.

Harness sets builtins._NAV_CMD (single command).
Tick_task processes it, sets _NAV_DONE=True and _NAV_LOG when complete.
"""
import unreal_runtime as ur
import builtins
import math

# ── Constants ──
Z_OFFSET   = 500.0
EYE_HEIGHT = 170.0
CAM_Z      = Z_OFFSET + EYE_HEIGHT
CELL_SIZE  = 200.0
MAX_SPEED  = 250.0

# Stuck detection params
STUCK_THRESHOLD = 1.0    # cm — below this per tick = not moving
STUCK_COUNT_LIMIT = 5    # consecutive stuck ticks = truly blocked
MAX_MOVE_TICKS = 300     # safety timeout
WARMUP_TICKS = 15        # skip stuck detection during acceleration phase
TURN_SPEED = 3.0         # degrees per tick for legacy turns


def _do_raycast(pawn, yaw_deg, max_distance, pitch_deg=0):
    """Fire a ray from pawn position at given yaw and pitch. Returns result dict."""
    loc = pawn.K2_GetActorLocation()
    yaw_rad = math.radians(yaw_deg)
    pitch_rad = math.radians(pitch_deg)

    cos_pitch = math.cos(pitch_rad)
    start = ur.CoreUObject.Vector()
    start.X, start.Y, start.Z = loc.X, loc.Y, loc.Z

    end = ur.CoreUObject.Vector()
    end.X = loc.X + math.cos(yaw_rad) * cos_pitch * max_distance
    end.Y = loc.Y + math.sin(yaw_rad) * cos_pitch * max_distance
    end.Z = loc.Z + math.sin(pitch_rad) * max_distance

    out_hit = ur.Engine.HitResult()
    tc = ur.CoreUObject.LinearColor()
    tc.R, tc.G, tc.B, tc.A = 1, 0, 0, 1
    hc = ur.CoreUObject.LinearColor()
    hc.R, hc.G, hc.B, hc.A = 0, 1, 0, 1

    ksm = ur.Engine.KismetSystemLibrary
    hit = ksm.LineTraceSingle(
        start, end,
        ur.Engine.ETraceTypeQuery.TraceTypeQuery1,
        False, [],
        ur.Engine.EDrawDebugTrace.ForOneFrame,
        out_hit, True, tc, hc, 1.0)

    if hit and out_hit.bBlockingHit:
        ip = out_hit.ImpactPoint
        return {
            "cmd": f"raycast(yaw={yaw_deg:.0f}, pitch={pitch_deg:.0f}, range={max_distance:.0f}cm)",
            "hit": True,
            "impact_x": round(ip.X, 1),
            "impact_y": round(ip.Y, 1),
            "impact_z": round(ip.Z, 1),
            "distance": round(out_hit.Distance, 1),
        }
    else:
        return {
            "cmd": f"raycast(yaw={yaw_deg:.0f}, pitch={pitch_deg:.0f}, range={max_distance:.0f}cm)",
            "hit": False,
            "impact_x": None,
            "impact_y": None,
            "impact_z": None,
            "distance": None,
        }


def _finish_cmd(state, to_x, to_y, blocked, dist_moved):
    """Complete a command: write log, handle legacy batch vs single cmd."""
    entry = {
        "cmd": state['cmd_str'],
        "from_x": round(state['from_x'], 1),
        "from_y": round(state['from_y'], 1),
        "to_x": round(to_x, 1),
        "to_y": round(to_y, 1),
        "yaw": round(state['yaw'] % 360, 1),
        "blocked": blocked,
        "distance_moved": round(dist_moved, 1),
    }

    if state.get('_legacy_batch'):
        # Legacy: accumulate in list, only set DONE when batch is empty
        if not hasattr(builtins, '_NAV_LOG_LIST'):
            builtins._NAV_LOG_LIST = []
        builtins._NAV_LOG_LIST.append(entry)

        remaining = getattr(builtins, '_NAV_MOVES', None)
        if not remaining:
            # Batch complete — copy list to _NAV_LOG for legacy harnesses
            builtins._NAV_LOG = builtins._NAV_LOG_LIST
            builtins._NAV_LOG_LIST = []
            builtins._NAV_DONE = True
    else:
        # New: single command → single dict
        builtins._NAV_LOG = entry
        builtins._NAV_DONE = True

    state['mode'] = 'idle'


def nav_tick(delta_time, elapsed_time, actors, params):
    """Persistent tick callback. Returns True to keep alive."""
    try:
        pawn = params['pawn']
        controller = params['controller']
        state = params['state']

        # ── IDLE: check for new command ──
        if state['mode'] == 'idle':
            new_cmd = getattr(builtins, '_NAV_CMD', None)

            # Legacy compatibility: _NAV_MOVES (list) → consume one at a time
            if new_cmd is None:
                legacy_moves = getattr(builtins, '_NAV_MOVES', None)
                if legacy_moves and len(legacy_moves) > 0:
                    new_cmd = legacy_moves.pop(0)
                    if not legacy_moves:
                        builtins._NAV_MOVES = None
                    # For legacy, accumulate results in a list
                    if not hasattr(builtins, '_NAV_LOG_LIST'):
                        builtins._NAV_LOG_LIST = []
                    state['_legacy_batch'] = True
                else:
                    return True
            else:
                state['_legacy_batch'] = False

            builtins._NAV_CMD = None
            builtins._NAV_DONE = False

            # Record from-position
            loc = pawn.K2_GetActorLocation()
            rot = controller.GetControlRotation()
            state['from_x'] = loc.X
            state['from_y'] = loc.Y
            state['from_z'] = loc.Z
            state['yaw'] = rot.Yaw
            state['prev_x'] = loc.X
            state['prev_y'] = loc.Y
            state['stuck_count'] = 0
            state['tick'] = 0
            state['dist_moved'] = 0.0

            # Parse command
            if isinstance(new_cmd, str):
                if new_cmd == "forward":
                    state['target_dist'] = CELL_SIZE
                    state['direction'] = 1
                    state['mode'] = 'moving'
                    state['cmd_str'] = "forward"
                elif new_cmd == "backward":
                    state['target_dist'] = CELL_SIZE
                    state['direction'] = -1
                    state['mode'] = 'moving'
                    state['cmd_str'] = "backward"
                elif new_cmd == "turn_left":
                    state['target_angle'] = 90.0
                    state['turn_remaining'] = 90.0
                    state['turn_dir'] = 1
                    state['mode'] = 'turning'
                    state['cmd_str'] = "turn_left"
                elif new_cmd == "turn_right":
                    state['target_angle'] = 90.0
                    state['turn_remaining'] = 90.0
                    state['turn_dir'] = -1
                    state['mode'] = 'turning'
                    state['cmd_str'] = "turn_right"

            elif isinstance(new_cmd, dict):
                cmd_type = new_cmd.get('cmd', '')
                if cmd_type == 'forward':
                    state['target_dist'] = new_cmd.get('distance', CELL_SIZE)
                    state['direction'] = 1
                    state['mode'] = 'moving'
                    state['cmd_str'] = f"forward({state['target_dist']:.0f}cm)"
                elif cmd_type == 'backward':
                    state['target_dist'] = new_cmd.get('distance', CELL_SIZE)
                    state['direction'] = -1
                    state['mode'] = 'moving'
                    state['cmd_str'] = f"backward({state['target_dist']:.0f}cm)"
                elif cmd_type == 'turn':
                    degrees = new_cmd.get('degrees', 90)
                    state['target_angle'] = abs(degrees)
                    state['turn_remaining'] = abs(degrees)
                    state['turn_dir'] = 1 if degrees > 0 else -1
                    state['mode'] = 'turning'
                    state['cmd_str'] = f"turn({degrees:.0f}deg)"
                elif cmd_type == 'raycast':
                    # Raycast is instant — supports pitch for vertical scanning
                    ray_yaw = new_cmd.get('yaw', 0)
                    ray_pitch = new_cmd.get('pitch', 0)
                    ray_dist = new_cmd.get('distance', 1000)
                    result = _do_raycast(pawn, ray_yaw, ray_dist, ray_pitch)
                    result['from_x'] = round(state['from_x'], 1)
                    result['from_y'] = round(state['from_y'], 1)
                    result['from_z'] = round(state['from_z'], 1)
                    result['yaw'] = round(state['yaw'] % 360, 1)
                    builtins._NAV_LOG = result
                    builtins._NAV_DONE = True
                elif cmd_type == 'move_z':
                    # Instant Z teleport
                    dz = new_cmd.get('distance', 0)
                    loc = pawn.K2_GetActorLocation()
                    new_loc = ur.CoreUObject.Vector()
                    new_loc.X, new_loc.Y, new_loc.Z = loc.X, loc.Y, loc.Z + dz
                    pawn.K2_SetActorLocation(new_loc, False)
                    loc2 = pawn.K2_GetActorLocation()
                    builtins._NAV_LOG = {
                        "cmd": f"move_z({dz:.0f}cm)",
                        "from_x": round(state['from_x'], 1),
                        "from_y": round(state['from_y'], 1),
                        "from_z": round(state['from_z'], 1),
                        "to_x": round(loc2.X, 1),
                        "to_y": round(loc2.Y, 1),
                        "to_z": round(loc2.Z, 1),
                        "yaw": round(state['yaw'] % 360, 1),
                        "blocked": False,
                        "distance_moved": round(abs(loc2.Z - state['from_z']), 1),
                    }
                    builtins._NAV_DONE = True

            return True

        # ── MOVING: forward/backward with stuck detection ──
        if state['mode'] == 'moving':
            state['tick'] += 1

            # Apply movement
            yaw_rad = math.radians(state['yaw'])
            direction = ur.CoreUObject.Vector()
            direction.X = math.cos(yaw_rad) * state['direction']
            direction.Y = math.sin(yaw_rad) * state['direction']
            direction.Z = 0.0
            pawn.AddMovementInput(direction, 1.0, False)

            # Measure position change
            loc = pawn.K2_GetActorLocation()
            delta = math.sqrt(
                (loc.X - state['prev_x'])**2 + (loc.Y - state['prev_y'])**2)
            state['dist_moved'] = math.sqrt(
                (loc.X - state['from_x'])**2 + (loc.Y - state['from_y'])**2)
            state['prev_x'] = loc.X
            state['prev_y'] = loc.Y

            # Stuck detection: real collision (skip during warmup/acceleration)
            if state['tick'] > WARMUP_TICKS:
                if delta < STUCK_THRESHOLD:
                    state['stuck_count'] += 1
                else:
                    state['stuck_count'] = 0

            # Check done conditions
            done = False
            blocked = False

            if state['tick'] > WARMUP_TICKS and state['stuck_count'] >= STUCK_COUNT_LIMIT:
                done = True
                blocked = True
            elif state['dist_moved'] >= state['target_dist'] * 0.95:
                done = True
                blocked = False
            elif state['tick'] >= MAX_MOVE_TICKS:
                done = True
                blocked = state['dist_moved'] < state['target_dist'] * 0.5

            if done:
                # Snap Z
                snap = ur.CoreUObject.Vector()
                snap.X, snap.Y, snap.Z = loc.X, loc.Y, CAM_Z
                pawn.K2_SetActorLocation(snap, False)

                _finish_cmd(state, loc.X, loc.Y, blocked, state['dist_moved'])
            return True

        # ── TURNING: rotate tick-by-tick ──
        if state['mode'] == 'turning':
            turn_step = min(TURN_SPEED, state['turn_remaining'])
            state['yaw'] += turn_step * state['turn_dir']
            state['turn_remaining'] -= turn_step

            cam_rot = ur.CoreUObject.Rotator()
            cam_rot.Pitch = -2.0
            cam_rot.Yaw = state['yaw']
            cam_rot.Roll = 0.0
            controller.SetControlRotation(cam_rot)

            state['tick'] += 1
            if state['turn_remaining'] <= 0.1:
                loc = pawn.K2_GetActorLocation()
                _finish_cmd(state, loc.X, loc.Y, False, 0)
            return True

        return True

    except Exception as e:
        print(f"  nav_tick error: {e}")
        import traceback
        traceback.print_exc()
        return True


# ── Main: find pawn, launch tick_task ──
def main():
    world = ur.Engine.GetDefaultWorld()
    game_mode = ur.Engine.GetGameMode()
    pawns = world.GetActorsOfClass(game_mode.DefaultPawnClass)

    if not pawns or len(pawns) == 0:
        print("[move_camera] ERROR: no player pawn")
        return

    pawn = pawns[0]
    controller = pawn.GetController()

    mc = pawn.MovementComponent
    mc.MaxSpeed     = MAX_SPEED
    mc.Acceleration = 1000.0
    mc.Deceleration = 2000.0

    builtins._NAV_CMD  = None
    builtins._NAV_DONE = True
    builtins._NAV_LOG  = {}

    params = {
        'pawn': pawn,
        'controller': controller,
        'state': {
            'mode': 'idle',
            'tick': 0,
            'yaw': 0.0,
            'from_x': 0, 'from_y': 0,
            'prev_x': 0, 'prev_y': 0,
            'target_dist': 0, 'direction': 1,
            'dist_moved': 0,
            'stuck_count': 0,
            'target_angle': 0, 'turn_remaining': 0, 'turn_dir': 1,
            'cmd_str': '',
        },
    }

    success = ur.submit_tick_task(
        name="nav_interactive",
        callback=nav_tick,
        actors=[],
        params=params,
        max_duration=7200.0,
    )

    if success:
        print("[move_camera] Tick_task launched (stuck detection mode).")
    else:
        print("[move_camera] Tick_task FAILED!")


main()
