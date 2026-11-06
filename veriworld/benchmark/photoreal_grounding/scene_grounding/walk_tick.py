"""walk_tick.py — Persistent UE-side tick task for light_war.

Provides three real-physics movement primitives (collision-aware) plus
two instant primitives, all driven through `builtins._WALK_CMD` from
the host harness:

  Real-physics (uses CharacterMovement.AddMovementInput, stuck-detect):
    {"cmd": "walk",    "distance": <cm>}   -- forward in yaw direction
    {"cmd": "descend", "distance": <cm>}   -- straight down (-Z)

  Instant (K2_SetActorLocation / SetControlRotation, no collision):
    {"cmd": "teleport", "x": <cm>, "y": <cm>, "z": <cm>}
    {"cmd": "set_view", "yaw": <deg>, "pitch": <deg>}

The teleport escape hatch is what lets the agent bypass the canopy:
fly up to z=20000+, look down, screenshot to acquire a global view,
then `descend` back to the shoreline.

Walk and descend honour collision (trees, terrain, water surface),
so distance_moved < distance_requested means the pawn ran into
something. Stuck detection: STUCK_COUNT_LIMIT consecutive ticks with
position delta < STUCK_THRESHOLD = blocked.

Adapted from mazenavfps/move_camera.py — same stuck-detect machinery,
no Z snap (the agent owns altitude), no fixed cell-size step.
"""
import unreal_runtime as ur
import builtins
import math


# ── Tunables ──
MAX_SPEED          = 800.0    # cm/s — quicker than maze (250) so 30 m walk takes ~4 s
ACCELERATION       = 2000.0
DECELERATION       = 4000.0
STUCK_THRESHOLD    = 1.0      # cm/tick below this = not moving
STUCK_COUNT_LIMIT  = 6        # consecutive stuck ticks = blocked
MAX_MOVE_TICKS     = 600      # safety cap (10 s @ 60fps)
WARMUP_TICKS       = 12       # skip stuck detection during accel


def _finish(state, x, y, z, blocked, dist_moved):
    """Write a single result entry into _WALK_LOG and unblock the harness."""
    entry = {
        "cmd": state["cmd_str"],
        "from_x": round(state["from_x"], 1),
        "from_y": round(state["from_y"], 1),
        "from_z": round(state["from_z"], 1),
        "to_x": round(x, 1),
        "to_y": round(y, 1),
        "to_z": round(z, 1),
        "yaw": round(state["yaw"] % 360, 1),
        "pitch": round(state["pitch"], 1),
        "blocked": bool(blocked),
        "dist_moved": round(dist_moved, 1),
        "ticks": state["tick"],
    }
    builtins._WALK_LOG = entry
    builtins._WALK_DONE = True
    state["mode"] = "idle"


def _instant_finish(state, x, y, z, cmd_str):
    """Helper for the instant (no-tick) commands."""
    entry = {
        "cmd": cmd_str,
        "from_x": round(state["from_x"], 1),
        "from_y": round(state["from_y"], 1),
        "from_z": round(state["from_z"], 1),
        "to_x": round(x, 1),
        "to_y": round(y, 1),
        "to_z": round(z, 1),
        "yaw": round(state["yaw"] % 360, 1),
        "pitch": round(state["pitch"], 1),
        "blocked": False,
        "dist_moved": math.sqrt(
            (x - state["from_x"]) ** 2 +
            (y - state["from_y"]) ** 2 +
            (z - state["from_z"]) ** 2),
        "ticks": 0,
    }
    builtins._WALK_LOG = entry
    builtins._WALK_DONE = True
    state["mode"] = "idle"


def walk_tick(delta_time, elapsed_time, actors, params):
    """Persistent tick callback. Returns True to keep alive."""
    try:
        pawn = params["pawn"]
        controller = params["controller"]
        state = params["state"]

        # ── IDLE: pick up next command ──
        if state["mode"] == "idle":
            cmd = getattr(builtins, "_WALK_CMD", None)
            if cmd is None:
                return True

            builtins._WALK_CMD = None
            builtins._WALK_DONE = False

            loc = pawn.K2_GetActorLocation()
            rot = controller.GetControlRotation()
            state["from_x"] = loc.X
            state["from_y"] = loc.Y
            state["from_z"] = loc.Z
            state["yaw"] = rot.Yaw
            state["pitch"] = rot.Pitch
            state["prev_x"] = loc.X
            state["prev_y"] = loc.Y
            state["prev_z"] = loc.Z
            state["stuck_count"] = 0
            state["tick"] = 0
            state["dist_moved"] = 0.0

            cmd_type = cmd.get("cmd", "")

            if cmd_type == "walk":
                state["target_dist"] = float(cmd.get("distance", 1000.0))
                state["mode"] = "walking"
                state["cmd_str"] = f"walk({state['target_dist']:.0f}cm)"

            elif cmd_type == "descend":
                state["target_dist"] = float(cmd.get("distance", 1000.0))
                state["mode"] = "descending"
                state["cmd_str"] = f"descend({state['target_dist']:.0f}cm)"

            elif cmd_type == "teleport":
                tx = float(cmd.get("x", loc.X))
                ty = float(cmd.get("y", loc.Y))
                tz = float(cmd.get("z", loc.Z))
                v = ur.CoreUObject.Vector(); v.X = tx; v.Y = ty; v.Z = tz
                pawn.K2_SetActorLocation(v, False)
                _instant_finish(state, tx, ty, tz,
                                f"teleport({tx:.0f},{ty:.0f},{tz:.0f})")
                return True

            elif cmd_type == "set_view":
                yaw = float(cmd.get("yaw", rot.Yaw))
                pitch = float(cmd.get("pitch", rot.Pitch))
                new_rot = ur.CoreUObject.Rotator()
                new_rot.Pitch = pitch; new_rot.Yaw = yaw; new_rot.Roll = 0
                controller.SetControlRotation(new_rot)
                state["yaw"] = yaw
                state["pitch"] = pitch
                _instant_finish(state, loc.X, loc.Y, loc.Z,
                                f"set_view(yaw={yaw:.1f},pitch={pitch:.1f})")
                return True

            else:
                # Unknown command: ack as no-op so the host doesn't hang.
                _instant_finish(state, loc.X, loc.Y, loc.Z,
                                f"unknown_cmd({cmd_type!r})")
                return True

        # ── WALKING: AddMovementInput in forward XY direction ──
        if state["mode"] == "walking":
            state["tick"] += 1

            yaw_rad = math.radians(state["yaw"])
            d = ur.CoreUObject.Vector()
            d.X = math.cos(yaw_rad)
            d.Y = math.sin(yaw_rad)
            d.Z = 0.0
            pawn.AddMovementInput(d, 1.0, True)

            loc = pawn.K2_GetActorLocation()
            delta = math.sqrt(
                (loc.X - state["prev_x"]) ** 2 +
                (loc.Y - state["prev_y"]) ** 2)
            state["dist_moved"] = math.sqrt(
                (loc.X - state["from_x"]) ** 2 +
                (loc.Y - state["from_y"]) ** 2)
            state["prev_x"] = loc.X
            state["prev_y"] = loc.Y

            if state["tick"] > WARMUP_TICKS:
                if delta < STUCK_THRESHOLD:
                    state["stuck_count"] += 1
                else:
                    state["stuck_count"] = 0

            done = False
            blocked = False
            if state["tick"] > WARMUP_TICKS and state["stuck_count"] >= STUCK_COUNT_LIMIT:
                done = True; blocked = True
            elif state["dist_moved"] >= state["target_dist"] * 0.95:
                done = True; blocked = False
            elif state["tick"] >= MAX_MOVE_TICKS:
                done = True; blocked = state["dist_moved"] < state["target_dist"] * 0.5

            if done:
                _finish(state, loc.X, loc.Y, loc.Z, blocked, state["dist_moved"])
            return True

        # ── DESCENDING: AddMovementInput in -Z direction ──
        if state["mode"] == "descending":
            state["tick"] += 1

            d = ur.CoreUObject.Vector()
            d.X = 0.0; d.Y = 0.0; d.Z = -1.0
            pawn.AddMovementInput(d, 1.0, True)

            loc = pawn.K2_GetActorLocation()
            delta = abs(loc.Z - state["prev_z"])
            state["dist_moved"] = abs(state["from_z"] - loc.Z)
            state["prev_z"] = loc.Z

            if state["tick"] > WARMUP_TICKS:
                if delta < STUCK_THRESHOLD:
                    state["stuck_count"] += 1
                else:
                    state["stuck_count"] = 0

            done = False
            blocked = False
            if state["tick"] > WARMUP_TICKS and state["stuck_count"] >= STUCK_COUNT_LIMIT:
                done = True; blocked = True
            elif state["dist_moved"] >= state["target_dist"] * 0.95:
                done = True; blocked = False
            elif state["tick"] >= MAX_MOVE_TICKS:
                done = True; blocked = state["dist_moved"] < state["target_dist"] * 0.5

            if done:
                _finish(state, loc.X, loc.Y, loc.Z, blocked, state["dist_moved"])
            return True

        return True

    except Exception as e:
        print(f"  walk_tick error: {e}")
        import traceback
        traceback.print_exc()
        return True


def main():
    world = ur.Engine.GetDefaultWorld()
    gm = ur.Engine.GetGameMode()
    pawns = world.GetActorsOfClass(gm.DefaultPawnClass)
    if not pawns:
        print("[walk_tick] ERROR: no player pawn")
        return
    pawn = pawns[0]
    controller = pawn.GetController()

    # DefaultPawn exposes MovementComponent; the AIUE character exposes
    # CharacterMovement. Both must initialize before accepting commands.
    mc = getattr(pawn, "MovementComponent", None)
    if mc is not None:
        mc.MaxSpeed = MAX_SPEED
        mc.Acceleration = ACCELERATION
        mc.Deceleration = DECELERATION
    else:
        mc = getattr(pawn, "CharacterMovement", None)
        if mc is None:
            raise RuntimeError("Scene pawn has no supported movement component")
        mc.MaxWalkSpeed = MAX_SPEED
        mc.MaxAcceleration = ACCELERATION
        mc.BrakingDecelerationWalking = DECELERATION

    builtins._WALK_CMD = None
    builtins._WALK_DONE = True
    builtins._WALK_LOG = {}

    params = {
        "pawn": pawn,
        "controller": controller,
        "state": {
            "mode": "idle", "tick": 0,
            "yaw": 0.0, "pitch": 0.0,
            "from_x": 0.0, "from_y": 0.0, "from_z": 0.0,
            "prev_x": 0.0, "prev_y": 0.0, "prev_z": 0.0,
            "target_dist": 0.0, "dist_moved": 0.0,
            "stuck_count": 0, "cmd_str": "",
        },
    }
    ok = ur.submit_tick_task(
        name="light_war_walk", callback=walk_tick,
        actors=[], params=params, max_duration=7200.0,
    )
    print("[walk_tick] tick task launched" if ok else "[walk_tick] FAILED")


main()
