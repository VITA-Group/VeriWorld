"""object_ground — harness_grid_iterate.

Same target as ``harness_iterate`` (find a named scene object given only
its name + the camera pose), but Round 1 pre-spawns a NxN camera-grid
of *numbered colored balls* so the agent gets a concrete
pixel↔world-coord correspondence. This is much more grounded than
"start from an empty scene + a camera pose".

Flow per task:
  Round 1:
    - Harness fires N×N raycasts from the camera, spawns numbered
      colored spheres at each hit, and shows the agent:
        (a) the screenshot with the labelled grid
        (b) the table mapping {idx → color, world coord}
    - Agent inspects which idx visually lands on / nearest the target,
      and replies with a NEW ball list (refining around that anchor)
      plus ``closest_color``.
  Round 2..max:
    - Harness despawns the previous markers and spawns the agent's
      requested set, then asks for another refinement.
  Verify:
    - distance(ball whose colour == claimed_closest_color, target_alias_pose)
      <= tolerance_cm.

PASS = final claimed-closest ball within tolerance of the actual target.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import re
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from veriworld.common.api_receipt import save_api_receipt
from veriworld.common import RunLogger, VLMClient, format_prompt_txt, png_to_base64_url
from veriworld.infra.interactive.engine import InteractiveEngine

from .._action_json import load_action_json

from .._common import (
    OPEN_APARTMENT_CODE,
    QUERY_MARKER_COLORS,
    camera_grid_raycast_code,
    extract_thought,
    get_object_pose_code,
    load_aliases,
    parse_grid_raycast,
    parse_pose_line,
    reset_pawn_code,
    resolve_alias,
    resolve_camera_view,
    take_screenshot,
    unwrap,
)
from .._bbox_markers import clear_bbox_markers_code as clear_query_markers_code
from .._bbox_markers_v2 import spawn_bbox_markers_code_v2 as spawn_anchored_markers_code

HARNESS_VERSION = "object-grid-jsonc-list-ids-v2-20261102"

log = logging.getLogger(__name__)

HERE = Path(__file__).parent
GROUNDTRUTH = HERE.parent / "groundtruth.json"


@dataclass
class EpisodeResult:
    run_id: str
    model: str
    task: str
    result: str
    rounds: int
    target_alias: str
    target_pose: Optional[dict]
    final_balls: list
    claimed_closest_color: Optional[str]
    closest_ball_distance: Optional[float]
    tolerance_cm: float
    artifacts: dict = field(default_factory=dict)
    harness_version: str = HARNESS_VERSION


def load_groundtruth() -> dict:
    return json.loads(GROUNDTRUTH.read_text(encoding="utf-8"))


_JSON_FENCE = re.compile(r"```json\s*(.+?)\s*```", re.DOTALL)


def _extract_action(text: str) -> Optional[dict]:
    candidates: list[str] = []
    for m in _JSON_FENCE.finditer(text):
        candidates.append(m.group(1))
    for m in re.finditer(r"\{[\s\S]*?\"balls\"[\s\S]*?\}\s*\}", text):
        candidates.append(m.group(0))
    for c in candidates:
        try:
            d = load_action_json(c)
        except Exception:
            continue
        if isinstance(d, dict) and isinstance(d.get("balls"), list):
            return d
    return None


def _format_grid_table(grid: list[dict]) -> str:
    """Format ``parse_grid_raycast`` output into a readable table."""
    if not grid:
        return "(no grid hits — every ray missed)\n"
    out = ["idx  color    world (X, Y, Z)              dist_cm",
           "---  -------  ---------------------------   -------"]
    for e in grid:
        out.append(f"{e['idx']:>3}  {e['color']:<7}  "
                   f"({e['X']:>7.1f}, {e['Y']:>7.1f}, {e['Z']:>6.1f})   "
                   f"{e['distance_cm']:>6.1f}")
    return "\n".join(out)


def _target_anchor(spec: dict, target_pose: dict) -> dict:
    """Pick the world point to face for random-view generation.

    For a long object whose actor pivot sits at one end of the mesh,
    the centroid of ``extent_endpoints`` is a better aim point than the
    raw pivot — the camera ends up looking at the visible body of the
    spoon / spatula / knife rather than at empty handle space.
    """
    eps = spec.get("extent_endpoints") or []
    if eps:
        cx = sum(p["X"] for p in eps) / len(eps)
        cy = sum(p["Y"] for p in eps) / len(eps)
        cz = sum(p["Z"] for p in eps) / len(eps)
        return {"X": cx, "Y": cy, "Z": cz}
    return {"X": target_pose["X"], "Y": target_pose["Y"], "Z": target_pose["Z"]}


def _random_view_facing(target: dict, seed: int,
                         spec: Optional[dict] = None,
                         distance_range_cm: tuple = (90.0, 160.0),
                         pitch_range_deg: tuple = (-50.0, -25.0),
                         yaw_range_deg: tuple = (0.0, 360.0),
                         min_cam_z: float = 100.0,
                         max_cam_z: float = 260.0,
                         aim_offset_range_cm: Optional[tuple] = None,
                         max_tries: int = 64) -> dict:
    # Per-task overrides via spec["random_view"]; explicit ranges
    # always win over prior-jittered defaults below.
    rv = (spec or {}).get("random_view") or {}
    has_explicit_yaw = "yaw_range_deg" in rv
    has_explicit_pitch = "pitch_range_deg" in rv
    if "distance_range_cm" in rv: distance_range_cm = tuple(rv["distance_range_cm"])
    if "yaw_range_deg" in rv:     yaw_range_deg     = tuple(rv["yaw_range_deg"])
    if "pitch_range_deg" in rv:   pitch_range_deg   = tuple(rv["pitch_range_deg"])
    if "min_cam_z" in rv:         min_cam_z         = float(rv["min_cam_z"])
    if "max_cam_z" in rv:         max_cam_z         = float(rv["max_cam_z"])

    # Prior-jitter default: if the task ships a `camera_start_pose`
    # (kitchen tasks all do — those views were hand-picked), jitter
    # the new view around the prior's yaw/pitch instead of sampling
    # globally. Keeps the camera on the same side of the apartment
    # as the prior so we don't end up inside walls / over the
    # kitchen island. Explicit ranges in random_view still win.
    yaw_jitter = float(rv.get("yaw_jitter_deg", 45.0))
    pitch_jitter = float(rv.get("pitch_jitter_deg", 15.0))
    prior_pose = (spec or {}).get("camera_start_pose") or {}
    prior_rot = prior_pose.get("control_rotation") or {}
    prior_yaw = prior_rot.get("Yaw")
    prior_pitch = prior_rot.get("Pitch")
    if prior_yaw is not None and not has_explicit_yaw:
        yaw_range_deg = (float(prior_yaw) - yaw_jitter,
                          float(prior_yaw) + yaw_jitter)
    if prior_pitch is not None and not has_explicit_pitch:
        pp = float(prior_pitch)
        if pp > 180.0: pp -= 360.0
        pitch_range_deg = (pp - pitch_jitter, pp + pitch_jitter)
    """Sample a camera pose that LOOKS AT ``target`` from a random
    direction within the given ranges. Camera is placed at
    ``target - distance × forward(yaw, pitch)``, so its forward vector
    points exactly at the target.

    The default ranges are tuned for the apartment kitchen scenes:
    pitch -50..-25° gives a top-down-ish look at counter/cooktop
    objects; distance 90-160 cm keeps the target large enough in the
    640x480 viewport but with enough scene context to read off where
    things are.
    """
    rng = random.Random(seed)
    # Optional spec override for aim offset (overrides kwarg).
    if "aim_offset_range_cm" in rv:
        aim_offset_range_cm = tuple(rv["aim_offset_range_cm"])

    # If aim_offset_range_cm is set, displace the camera's aim point
    # to a random vector around the target. The camera will still
    # look at THAT point (so the optical-axis-center hit lands at the
    # offset, NOT on the target itself). This forces the agent to
    # actually localise the target visually instead of relying on
    # the gift-wrapped centre-of-screen ball.
    if aim_offset_range_cm is not None:
        lo, hi = aim_offset_range_cm
        # Uniform random unit vector via gaussian normalisation.
        gx = rng.gauss(0, 1); gy = rng.gauss(0, 1); gz = rng.gauss(0, 1)
        gn = math.sqrt(gx*gx + gy*gy + gz*gz) or 1.0
        gx, gy, gz = gx/gn, gy/gn, gz/gn
        # Bias offset to be mostly horizontal (utensil rows are flat
        # on a counter; offset down would push aim into the cabinet).
        # We project to a plane perpendicular to vertical, then add
        # a small Z component.
        gz *= 0.4
        gn2 = math.sqrt(gx*gx + gy*gy + gz*gz) or 1.0
        gx, gy, gz = gx/gn2, gy/gn2, gz/gn2
        mag = rng.uniform(lo, hi)
        tx_aim = target["X"] + gx * mag
        ty_aim = target["Y"] + gy * mag
        tz_aim = target["Z"] + gz * mag
    else:
        tx_aim = target["X"]; ty_aim = target["Y"]; tz_aim = target["Z"]
    tx, ty, tz = tx_aim, ty_aim, tz_aim
    for _ in range(max_tries):
        dist = rng.uniform(*distance_range_cm)
        pitch = rng.uniform(*pitch_range_deg)
        yaw = rng.uniform(*yaw_range_deg) % 360.0
        yr = math.radians(yaw); pr = math.radians(pitch)
        fx = math.cos(yr) * math.cos(pr)
        fy = math.sin(yr) * math.cos(pr)
        fz = math.sin(pr)
        cam_x = tx - dist * fx
        cam_y = ty - dist * fy
        cam_z = tz - dist * fz
        if cam_z < min_cam_z or cam_z > max_cam_z:
            continue
        return {
            "_view": (f"random seed={seed} target=({tx:.1f},{ty:.1f},{tz:.1f}) "
                      f"dist={dist:.1f} yaw={yaw:.2f} pitch={pitch:.2f}"),
            "location": {"X": cam_x, "Y": cam_y, "Z": cam_z},
            "control_rotation": {"Pitch": pitch, "Yaw": yaw, "Roll": 0.0},
        }
    raise RuntimeError(
        f"could not find valid random view for target {target} after "
        f"{max_tries} tries (range: dist={distance_range_cm}, "
        f"pitch={pitch_range_deg}, cam_z={min_cam_z}-{max_cam_z})")


def _screen_axis_mapping(yaw_deg: float) -> dict:
    """Resolve, for the given camera yaw, which world axis corresponds
    to which on-screen direction. Camera roll is assumed zero (always
    true for our scenes), so 'up in screen' is always +z. Forward (= the
    horizontal direction the camera looks) and 'left in screen' depend
    on yaw. Returns a dict of human-readable mappings the system prompt
    can paste in directly.
    """
    yaw = yaw_deg % 360
    if 45 <= yaw < 135:        # facing +Y
        fwd, left = "+y", "+x"
    elif 135 <= yaw < 225:     # facing -X
        fwd, left = "-x", "+y"
    elif 225 <= yaw < 315:     # facing -Y
        fwd, left = "-y", "-x"
    else:                       # facing +X
        fwd, left = "+x", "-y"
    flip = {"+x": "-x", "-x": "+x", "+y": "-y", "-y": "+y",
            "+z": "-z", "-z": "+z"}
    return {
        "up": "+z", "down": "-z",
        "forward": fwd, "backward": flip[fwd],
        "left": left, "right": flip[left],
    }


def _system_prompt(task_id: str, spec: dict, start_pose: dict,
                   target_alias: str, max_rounds: int,
                   grid_n: int) -> str:
    desc = spec.get("description", "")
    tol = spec.get("tolerance_cm", 10.0)
    max_balls = int(spec.get("max_balls", 4))
    available = list(QUERY_MARKER_COLORS.keys())
    avail_str = ", ".join(f'"{c}"' for c in available)

    loc = start_pose["location"]; rot = start_pose["control_rotation"]
    axis_map = _screen_axis_mapping(rot["Yaw"])
    # Pretty translations: "+y" -> "+y (positive y, dy > 0)" etc.
    def _verbose(axis: str) -> str:
        sign, ax = axis[0], axis[1]
        word = {"x": "x", "y": "y", "z": "z"}[ax]
        if sign == "+":
            return f"+{word} (i.e. INCREASE {word}: use d{word} > 0)"
        return f"-{word} (i.e. DECREASE {word}: use d{word} < 0)"

    cam_blurb = (
        f"Camera (pawn) world pose:\n"
        f"  location:  X={loc['X']:.1f}, Y={loc['Y']:.1f}, Z={loc['Z']:.1f}\n"
        f"  rotation:  Pitch={rot['Pitch']:.2f}, Yaw={rot['Yaw']:.2f}, "
        f"Roll={rot['Roll']:.2f}\n"
        f"  (this camera is fixed for the whole episode)\n\n"
        f"DIRECT SCREEN ↔ WORLD AXIS MAPPING (precomputed for this exact\n"
        f"camera pose — use these literally instead of trying to derive\n"
        f"directions from yaw/pitch yourself):\n"
        f"  - To move the ball UP in the screen     → {_verbose(axis_map['up'])}\n"
        f"  - To move the ball DOWN in the screen   → {_verbose(axis_map['down'])}\n"
        f"  - To move the ball LEFT in the screen   → {_verbose(axis_map['left'])}\n"
        f"  - To move the ball RIGHT in the screen  → {_verbose(axis_map['right'])}\n"
        f"  - To move the ball FORWARD (deeper into\n"
        f"    the scene, away from the camera)      → {_verbose(axis_map['forward'])}\n"
        f"  - To move the ball BACKWARD (closer to\n"
        f"    the camera)                           → {_verbose(axis_map['backward'])}\n"
        f"  These mappings are exact for this camera (roll=0); use them\n"
        f"  before consulting the per-ball axis-bbox edges.\n"
    )

    if max_balls == 1:
        action_block = (
            "SINGLE-BALL MODE — you may emit AT MOST 1 ball this episode.\n"
            "Place that ball directly on / next to the target idx in\n"
            "round 1, then nudge it toward the target each subsequent\n"
            "round. The ball INHERITS the grid anchor's idx and colour\n"
            "(so it stays visually identifiable as the scaffold ball you\n"
            "chose) — do NOT specify `color` or `idx` in round 1.\n"
            "```json\n"
            "{\n"
            '  "anchor_idx": 6,\n'
            '  "balls": [\n'
            '    {"dx": 0, "dy": 0, "dz": 0}\n'
            "  ],\n"
            '  "closest_idx": 6\n'
            "}\n"
            "```\n\n"
            "ROUND 2..N — refer to the ball by its persistent `idx` (=\n"
            "your round-1 anchor_idx, e.g. 6). The harness adds your\n"
            "(dx,dy,dz) to its previous absolute position and respawns it:\n"
            "```json\n"
            "{\n"
            '  "balls": [\n'
            '    {"idx": 6, "dx": 5, "dy": -3, "dz": 0}\n'
            "  ],\n"
            '  "closest_idx": 6\n'
            "}\n"
            "```\n\n"
        )
        strategy_block = (
            "STRATEGY (single-ball mode):\n"
            "  Round 1: pick the grid idx whose ball is visually closest\n"
            "    to the target; emit one ball with all-zero offsets at\n"
            "    that anchor.\n"
            "  Round 2+: nudge the ball by 5-15 cm along whichever axis\n"
            "    the target lies in — use the screen→world mapping above\n"
            "    to choose dx/dy/dz signs. Make SMALL incremental moves;\n"
            "    if the ball overshoots, halve the step in the next round.\n\n"
        )
    else:
        action_block = (
            "```json\n"
            "{\n"
            '  "anchor_idx": 6,\n'
            '  "balls": [\n'
            '    {"color": "red",    "dx": 0,  "dy": 0,  "dz": 0},   // idx 0\n'
            '    {"color": "blue",   "dx": 8,  "dy": 0,  "dz": 0},   // idx 1\n'
            '    {"color": "yellow", "dx": 0,  "dy": 8,  "dz": 0},   // idx 2\n'
            '    {"color": "green",  "dx": -8, "dy": 0,  "dz": 0}    // idx 3\n'
            "  ],\n"
            '  "closest_idx": 0\n'
            "}\n"
            "```\n\n"
            "ROUND 2..N — refer to each ball by its persistent `idx`. The\n"
            "harness adds your (dx,dy,dz) to that ball's previous absolute\n"
            "position and respawns it (colour stays the same — you set it\n"
            "in round 1):\n"
            "```json\n"
            "{\n"
            '  "balls": [\n'
            '    {"idx": 0, "dx":  3, "dy": -2, "dz":  0},\n'
            '    {"idx": 1, "dx": -1, "dy":  4, "dz":  0},\n'
            '    {"idx": 2, "dx":  0, "dy":  0, "dz":  0}\n'
            "  ],\n"
            '  "closest_idx": 0\n'
            "}\n"
            "```\n"
            "(Setting `dx=dy=dz=0` keeps a ball still. Drop an idx from\n"
            "`balls` to remove that ball. New ball? include absolute\n"
            "`x,y,z` + fresh idx and colour as an escape hatch.)\n\n"
        )
        strategy_block = (
            "STRATEGY — IMPORTANT:\n"
            "  - DO NOT reset coords each round; always make SMALL relative\n"
            "    moves along the axis the visual evidence supports.\n"
            "  - DO NOT translate ALL balls by the same (dx,dy,dz). Move\n"
            "    ONE ball at a time, or use different deltas to tighten\n"
            "    the bracket around the target.\n"
            "  - The bbox face labels / screen→world mapping above tell you\n"
            "    how to convert 'target is in this direction' into\n"
            "    (dx,dy,dz) signs.\n\n"
            "  Round 1: pick the grid idx visually closest to the target;\n"
            "    emit a 3-4 ball cluster around it — one at the anchor\n"
            "    (all-zero offsets), others ±5..10 cm along DIFFERENT\n"
            "    axes to bracket the target.\n"
            "  Round 2+: identify which ball is currently closest;\n"
            "    nudge IT toward the target by 5-15 cm; leave others.\n\n"
        )
    return (
        "You are a visual-grounding probe. The screenshot below was taken\n"
        "in a photoreal UE5 kitchen scene. Somewhere in the scene there is\n"
        f"a target object: **{target_alias}** — described as\n"
        f"'{desc}'. You are NOT told its world coordinates — only its\n"
        "name and description. Your job is to drop colored balls in the\n"
        "world such that at least one of them lands as close as possible\n"
        "to the target.\n\n"
        "*Important kitchen-object disambiguation* (these are easy to\n"
        "confuse from a single screenshot):\n"
        "  - A SAUCEPAN (\"pan\") has ONE long side handle that sticks\n"
        "    out, a flat round bottom, and is shallow and wide.\n"
        "  - A SAUCEPOT (\"pot\") has TWO small loop handles on either\n"
        "    side, a tall cylindrical body, and a lid.\n"
        "  - A WOODEN UTENSIL is made of solid wood: LIGHT YELLOW / TAN\n"
        "    colour with visible WOOD GRAIN texture along the handle\n"
        "    and head.\n"
        "  - A BLACK UTENSIL is SOLID MATTE BLACK plastic / silicone:\n"
        "    no grain, uniform dark colour from end to end.\n"
        "  - A SPATULA has a FLAT WIDE BLADE (paddle-shaped) with\n"
        "    visible SLOTS / vents cut into it, used for flipping.\n"
        "  - A SPOON has a CONCAVE OVAL SCOOP (closed bowl, no slots),\n"
        "    used for stirring.\n"
        "  Examples: a 'wooden spoon' is light-yellow with wood grain\n"
        "  AND has an oval scoop; a 'black spatula' is matte black AND\n"
        "  has a slotted flat blade. If you see a dark utensil with a\n"
        "  CLOSED oval head, that's the black SPOON, not the spatula.\n\n"
        f"TASK: {task_id}\n"
        f"target_alias: {target_alias}\n"
        f"tolerance:    {tol} cm  (PASS = your claimed-closest ball is\n"
        f"              within {tol} cm of the actual target)\n\n"
        f"{cam_blurb}\n"
        f"You have {max_rounds} rounds.\n\n"
        f"VISUALISATION (every round, including round 1) — instead of a\n"
        "solid sphere, every ball is rendered as a 12-edge wireframe\n"
        "AXIS-BBOX (~16 cm cube) centred on the ball's world coord:\n"
        "  - 4 RED   edges run along world +X / -X (parallel to X axis)\n"
        "  - 4 GREEN edges run along world +Y / -Y\n"
        "  - 4 BLUE  edges run along world +Z / -Z\n"
        "    Together these 12 edges form an axis-aligned cube around\n"
        "    the ball's centre, so the cube faces directly read the\n"
        "    world axes.\n"
        "  - the ball's PERSISTENT IDX (0,1,2…) is rendered as text at\n"
        "    the cube centre, in the ball's identifying colour, so even\n"
        "    though every cube shares the same RED/GREEN/BLUE edge\n"
        "    palette, the centre label tells multiple bboxes apart.\n"
        "    Round-1 grid balls show their grid idx (0..N²-1). After\n"
        "    round 1, each ball you place keeps the same idx for the\n"
        "    rest of the episode (use this idx to refer to that ball).\n"
        "  - small '+X' / '+Y' / '+Z' text labels sit just past the\n"
        "    corresponding +face of the cube to disambiguate sign:\n"
        "      RED   '+X' label → POSITIVE world X (`dx > 0` moves toward it)\n"
        "      GREEN '+Y' label → POSITIVE world Y (`dy > 0` moves toward it)\n"
        "      BLUE  '+Z' label → POSITIVE world Z (`dz > 0` moves toward it)\n"
        "  Reading the bbox: to move a ball TOWARD where the target sits\n"
        "  in the screenshot, find the bbox face whose +label is closest\n"
        "  to the target direction (e.g. the +X label is to the right of\n"
        "  the bbox → use POSITIVE `dx`). The opposite face implies the\n"
        "  negative sign on that axis.\n"
        "  Don't try to translate 'screen left/right/up/down' to your\n"
        "  internal X/Y conventions — just compare the screen direction\n"
        "  of the +X / +Y / +Z labels to the target's screen direction.\n\n"
        "DEPTH (IMPORTANT — balls are RAYCAST, not floating in 3D):\n"
        "  When a ball is spawned, the harness fires a ray from the\n"
        "  CAMERA through the requested (x,y,z) and snaps the ball onto\n"
        "  the FIRST SOLID MESH the ray hits. Two consequences:\n"
        "    1. Different idx in the round-1 grid often have very\n"
        "       different DEPTHS even when they look adjacent on screen\n"
        "       (one may sit on a counter, the next on the floor 1 m\n"
        "       behind it). Judge depth from the world (X,Y,Z) in the\n"
        "       table, not from screen position alone.\n"
        "    2. If a ball you placed last round is GONE in this round's\n"
        "       screenshot (or visibly clipping into a surface), it has\n"
        "       almost certainly been swallowed by a mesh — slid inside\n"
        "       a cabinet, under a counter lip, or behind a wall.\n"
        "       FIX: lift the ball by RAISING dz (e.g. +10..+20 cm) so\n"
        "       the ray now hits the surface ABOVE the occluder. Don't\n"
        "       keep nudging dx/dy on a ball you can't see — lift first,\n"
        "       then re-aim laterally.\n\n"
        f"ROUND 1 — the harness pre-spawns a {grid_n}×{grid_n} grid of\n"
        "colored numbered balls (each labelled 0..N²-1) by raycasting\n"
        "from the camera at evenly-spaced screen-space angles. You see:\n"
        "  - the screenshot, with all grid balls drawn as 12-edge\n"
        "    XYZ-coloured axis-bboxes (see VISUALISATION above)\n"
        "  - a table mapping grid_idx → colour name → world (X,Y,Z) → distance\n"
        "Pick whichever grid_idx visually lands closest to the target,\n"
        "then reply with your initial ball placement as RELATIVE OFFSETS\n"
        "from that grid idx (`anchor_idx` + `dx,dy,dz`). Each ball you\n"
        "emit gets a PERSISTENT idx based on its list position and a\n"
        "fixed colour; both stick with that ball for the whole episode.\n\n"
        + action_block
        + f"Available colors (max {max_balls} balls per round, each unique):\n"
        + f"  {avail_str}\n\n"
        + strategy_block
        + "The verifier reads ONLY the `closest_idx` from your final\n"
        + "round and checks the distance from THAT ball's actual world\n"
        + "coord to the target's actual world coord.\n\n"
        + "RESPONSE FORMAT each round:\n"
        + "  Brief THOUGHT (plain text, optional), then ONE JSON code fence.\n"
    )


async def run(
    engine: InteractiveEngine,
    seed: int,
    vlm: VLMClient,
    logger: RunLogger,
    *,
    task_id: str = "find_wooden_spoon_pot_view",
    max_rounds: int = 6,
    grid_n: int = 3,
    grid_fov_fraction: float = 0.7,
    max_balls_override: Optional[int] = None,
    random_view: bool = False,
    view_seed: Optional[int] = None,
) -> EpisodeResult:
    gt = load_groundtruth()
    if task_id not in gt["tasks"]:
        raise ValueError(f"unknown task: {task_id!r}; choices: {list(gt['tasks'])}")
    spec = dict(gt["tasks"][task_id])
    if max_balls_override is not None:
        spec["max_balls"] = int(max_balls_override)
    aliases = load_aliases()
    target_alias = spec["target_alias"]
    target_leaf = resolve_alias(target_alias, aliases)
    tolerance = float(spec.get("tolerance_cm", 10.0))
    # `start_pose` is set after target lookup if random_view is on.
    start_pose = resolve_camera_view(spec, "default", gt["scene_start_pose"])

    run_id = f"{vlm.model}_{task_id}_gridoff{grid_n}_bboxv2_r{max_rounds}"
    workspace = logger.model_dir(run_id)
    cfg = {
        "run_id": run_id, "model": vlm.model, "seed": seed, "task": task_id,
        "ablation": "harness_grid_iterate", "info": "V",
        "action": "BallPlacement+ClosestColor (grid round-1 seed)",
        "target_alias": target_alias, "tolerance_cm": tolerance,
        "max_rounds": max_rounds, "grid_n": grid_n,
        "grid_fov_fraction": grid_fov_fraction, "spec": spec,
    }
    logger.snapshot_model(
        task_module=__package__, model_name=run_id, seed=seed,
        resolved_args={"seed": seed, "model": vlm.model, "task": task_id,
                       "max_rounds": max_rounds, "grid_n": grid_n},
        extra_config=cfg,
    )

    out = unwrap(await engine.python_exec(OPEN_APARTMENT_CODE))
    log.info("open level: %s", out.strip())
    if "LEVEL_LOADING" in out:
        await asyncio.sleep(12.0)
    else:
        await asyncio.sleep(1.0)

    # Look up the target's actual world pose FIRST (no pawn dependency)
    # — needed both for verification and for random-view computation.
    target_pose = parse_pose_line(unwrap(
        await engine.python_exec(get_object_pose_code(target_leaf))))
    log.info("target alias=%s leaf=%s pose=%s",
             target_alias, target_leaf, target_pose)
    if target_pose is None:
        raise RuntimeError(f"target object not found: {target_alias!r} "
                           f"(mesh leaf {target_leaf!r})")

    # If random-view is requested, replace the spec's fixed
    # camera_start_pose with one that points at the target's anchor
    # (centroid of extent_endpoints, or the actor pivot otherwise).
    # The view seed defaults to the harness seed combined with the
    # task name so different tasks at the same seed get different
    # angles.
    if random_view:
        anchor = _target_anchor(spec, target_pose)
        # zlib.crc32 is deterministic across processes (Python's
        # builtin hash() randomises string hashes per-interpreter, so
        # 12 parallel models would otherwise see 12 different views
        # for the "same" seed+task — defeating fair comparison).
        vs = view_seed if view_seed is not None else (
            (seed * 1009 + zlib.crc32(task_id.encode())) & 0x7FFFFFFF)
        # Off-centre aim: optical-axis hits a point 20-50 cm from
        # the actual target so the central grid anchor (idx=4) is
        # NOT gift-wrapped on the target, but the target stays
        # comfortably in frame.
        start_pose = _random_view_facing(
            anchor, vs, spec=spec,
            aim_offset_range_cm=(20.0, 50.0))
        log.info("random view (off-centre) seed=%d anchor=%s pose=%s",
                 vs, anchor, start_pose)

    out = unwrap(await engine.python_exec(reset_pawn_code(start_pose)))
    log.info("reset pawn: %s", out.strip())
    await asyncio.sleep(0.7)

    # Wipe any leftover markers from previous runs.
    unwrap(await engine.python_exec(clear_query_markers_code()))

    # Round-1 grid: fire NxN rays, parse the hits, then spawn each as
    # a 12-edge axis-bbox marker (red X edges, green Y edges, blue Z
    # edges, idx label at centre) so the round-1 screenshot matches
    # every later round visually.
    grid_out = unwrap(await engine.python_exec(
        camera_grid_raycast_code(grid_n=grid_n,
                                  fov_fraction=grid_fov_fraction)))
    grid = parse_grid_raycast(grid_out)
    log.info("grid hits=%d", len(grid))
    (workspace / "grid_raycast.txt").write_text(grid_out, encoding="utf-8")
    if grid:
        grid_queries = [
            {"i": e["idx"], "x": e["X"], "y": e["Y"], "z": e["Z"],
             "color": e["color"]}
            for e in grid
        ]
        unwrap(await engine.python_exec(
            spawn_anchored_markers_code(grid_queries)))
    # Settle a frame so spawned spheres render before the screenshot.
    await asyncio.sleep(0.4)

    SYSTEM = _system_prompt(task_id, spec, start_pose, target_alias,
                            max_rounds, grid_n)
    history: list = []
    current_balls: list = []  # [{idx, color, x, y, z}, ...]
    claimed_closest: Optional[str] = None
    claimed_closest_idx: Optional[int] = None

    for round_idx in range(1, max_rounds + 1):
        shot = await take_screenshot(engine, f"round_{round_idx:03d}", workspace)

        parts: list = []
        if shot and shot.exists():
            parts.append({"type": "image_url",
                          "image_url": {"url": png_to_base64_url(shot)}})
        text = f"**Round {round_idx}/{max_rounds}**\n\n"
        if round_idx == 1:
            text += (f"Round-1 anchor grid ({grid_n}×{grid_n}, "
                     f"hits={len(grid)}):\n\n")
            text += _format_grid_table(grid)
            text += ("\n\n"
                     "Use the screenshot + this table to locate the target.\n"
                     "Reply with `anchor_idx` (the grid idx visually closest\n"
                     "to the target) and `balls` as a list of (dx, dy, dz)\n"
                     "offsets around that anchor; each ball you emit gets a\n"
                     "PERSISTENT idx based on its position in your list\n"
                     "(first ball = idx 0, second = idx 1, ...). The colour\n"
                     "and idx you assign here stay with each ball for the\n"
                     "rest of the episode and are the keys you use to refer\n"
                     "back to them in later rounds.\n\n")
        else:
            if current_balls:
                text += ("Current balls in the scene (absolute world coords;\n"
                         "this round's `dx`,`dy`,`dz` will be ADDED to these):\n")
                for b in current_balls:
                    text += (f"  idx={b['idx']:<2d} {b['color']:<8s} -> "
                             f"({b['x']:.1f}, {b['y']:.1f}, {b['z']:.1f})\n")
                text += "\n"
                if claimed_closest_idx is not None:
                    text += (f"You previously claimed idx={claimed_closest_idx} "
                             f"({claimed_closest}) was closest.\n\n")
            else:
                text += ("(no balls in scene — emit ABSOLUTE x/y/z "
                         "for each ball this round.)\n\n")
        text += ("Reply with ONE JSON code fence containing `balls` "
                 "(relative `dx,dy,dz` from previous round, refer to each "
                 "ball by its persistent `idx`) and `closest_idx`. "
                 "Round 1 also needs `anchor_idx`.")
        parts.append({"type": "text", "text": text})

        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": parts},
        ]
        (workspace / f"round_{round_idx:03d}_prompt.txt").write_text(
            format_prompt_txt(messages, [shot.name] if shot else []),
            encoding="utf-8")

        t0 = time.time()
        VLM_ROUND_TIMEOUT_S = max(40.0, float(getattr(vlm, "timeout", 35.0)) + 5.0)
        try:
            response = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None, vlm.chat, messages),
                timeout=VLM_ROUND_TIMEOUT_S,
            )
        except asyncio.TimeoutError as api_error:
            save_api_receipt(vlm, messages, workspace, round_idx, error=api_error)
            raise RuntimeError("VLM API call failed; episode remains unscored") from api_error
        except Exception as api_error:
            save_api_receipt(vlm, messages, workspace, round_idx, error=api_error)
            raise RuntimeError("VLM API call failed; episode remains unscored") from api_error
        save_api_receipt(vlm, messages, workspace, round_idx)
        log.info("round %d: VLM %.1fs", round_idx, time.time() - t0)
        (workspace / f"round_{round_idx:03d}_response.txt").write_text(
            response, encoding="utf-8")
        thought = extract_thought(response)
        (workspace / f"round_{round_idx:03d}_thought.txt").write_text(
            thought, encoding="utf-8")

        action = _extract_action(response)
        if not action:
            log.warning("round %d: no valid action JSON", round_idx)
            history.append({"round": round_idx, "thought": thought,
                            "action": None, "parse_error": True,
                            "current_balls": list(current_balls),
                            "claimed_closest": claimed_closest})
            continue

        # Resolve each ball entry against the harness's coord space:
        #
        # Persistent ID: every ball has an integer ``idx`` that stays the
        # same across rounds (this is the action key). Round 1 sets idxs
        # by list position (the agent's first ball is idx 0, second is 1,
        # ...); colour is also fixed in round 1 and carried forward.
        #
        # Round 1 entries: ``{"color", "dx", "dy", "dz", optional "anchor_idx"}``
        #   → pos = grid[anchor_idx].pos + (dx,dy,dz); colour from agent.
        #
        # Round 2+ entries: ``{"idx", "dx", "dy", "dz"}``
        #   (legacy ``{"color", "dx", "dy", "dz"}`` is also accepted and
        #   resolves the same ball by its R1 colour)
        #   → pos = prev_by_idx[idx].pos + (dx,dy,dz); colour unchanged.
        #
        # Absolute ``{"x", "y", "z"}`` is an escape hatch in any round.
        new_balls_raw = action.get("balls") or []
        round1_anchor_idx = action.get("anchor_idx")
        new_balls: list = []
        seen_idxs = set()
        prev_by_idx = {b["idx"]: b for b in current_balls}
        prev_by_color = {b["color"]: b for b in current_balls}
        grid_by_idx = {e["idx"]: e for e in grid}
        max_balls_cap = int(spec.get("max_balls", 4))
        for list_pos, b in enumerate(new_balls_raw):
            if len(new_balls) >= max_balls_cap:
                log.info("round %d: hit max_balls=%d cap, dropping rest",
                         round_idx, max_balls_cap)
                break
            if not isinstance(b, dict):
                continue
            try:
                # Resolve persistent (idx, color) for this ball.
                if round_idx == 1:
                    # In single-ball mode, the first ball inherits the
                    # chosen grid anchor ID and colour. Multi-ball mode
                    # keeps list-position IDs, matching the prompt. This keeps
                    # the persistent label above the ball matches the
                    # grid_idx the agent picked, making it visually
                    # obvious which scaffold ball was kept. Agent can
                    # still override by explicitly setting `idx` /
                    # `color` in this entry.
                    ai_default = (round1_anchor_idx
                                  if round1_anchor_idx is not None
                                  else (grid_n * grid_n) // 2)
                    ai = int(b.get("anchor_idx", ai_default))
                    if list_pos == 0 and ai in grid_by_idx:
                        idx_default = ai if max_balls_cap == 1 else list_pos
                        color_default = grid_by_idx[ai]["color"]
                    else:
                        idx_default = list_pos
                        color_default = "white"
                    idx = int(b.get("idx", idx_default))
                    color = str(b.get("color", color_default)).lower()
                    base: Optional[dict] = None
                else:
                    if "idx" in b:
                        idx = int(b["idx"])
                        base = prev_by_idx.get(idx)
                        # Fallback: if R1 failed (parse error / timeout)
                        # current_balls is empty; the agent may still
                        # reference a grid_idx as if it were the first
                        # round. Resolve against the round-1 grid in
                        # that case so the episode can recover.
                        if base is None and idx in grid_by_idx:
                            ge = grid_by_idx[idx]
                            base = {"idx": idx, "color": ge["color"],
                                    "x": ge["X"], "y": ge["Y"],
                                    "z": ge["Z"]}
                    elif "color" in b:
                        cn = str(b["color"]).lower()
                        base = prev_by_color.get(cn)
                        if base is None:
                            log.warning("round %d: color %r not in prev "
                                        "balls; skipping ball %s",
                                        round_idx, cn, b)
                            continue
                        idx = base["idx"]
                    else:
                        log.warning("round %d: ball entry %s lacks "
                                    "idx/color; skipping", round_idx, b)
                        continue
                    if base is None:
                        log.warning("round %d: idx %d not in prev balls; "
                                    "skipping ball %s",
                                    round_idx, idx, b)
                        continue
                    color = base["color"]
                if idx in seen_idxs:
                    log.warning("round %d: duplicate idx %d; skipping",
                                round_idx, idx)
                    continue
                # Resolve absolute (x, y, z).
                if "x" in b and "y" in b and "z" in b:
                    x = float(b["x"]); y = float(b["y"]); z = float(b["z"])
                else:
                    dx = float(b.get("dx", 0))
                    dy = float(b.get("dy", 0))
                    dz = float(b.get("dz", 0))
                    if round_idx == 1:
                        ai = int(b.get("anchor_idx", round1_anchor_idx
                                        if round1_anchor_idx is not None
                                        else (grid_n * grid_n) // 2))
                        if ai not in grid_by_idx:
                            log.warning("round 1: anchor_idx %d not in grid; "
                                        "skipping ball %s", ai, b)
                            continue
                        anchor = grid_by_idx[ai]
                        x = anchor["X"] + dx
                        y = anchor["Y"] + dy
                        z = anchor["Z"] + dz
                    else:
                        x = base["x"] + dx
                        y = base["y"] + dy
                        z = base["z"] + dz
                seen_idxs.add(idx)
                new_balls.append({"idx": idx, "color": color,
                                  "x": x, "y": y, "z": z})
            except (KeyError, TypeError, ValueError) as e:
                log.warning("round %d: malformed ball %s: %s",
                            round_idx, b, e)
                continue

        # Despawn whatever's currently in the scene (round-1 grid OR
        # last-round balls), then spawn the agent's new set. Each ball
        # carries its persistent idx (cross-round stable) which the
        # spawn helper renders as the visible label, plus its colour
        # and 3 XYZ axis arrows.
        unwrap(await engine.python_exec(clear_query_markers_code()))
        if new_balls:
            spawn_queries = [
                {"i": b["idx"], "x": b["x"], "y": b["y"], "z": b["z"],
                 "color": b["color"]}
                for b in new_balls
            ]
            unwrap(await engine.python_exec(
                spawn_anchored_markers_code(spawn_queries)))
        log.info("round %d: replaced balls (count=%d)",
                 round_idx, len(new_balls))

        current_balls = new_balls
        # Closest is identified by idx (persistent key); accept the
        # legacy `closest_color` field as a fallback.
        idx_to_color = {b["idx"]: b["color"] for b in current_balls}
        color_to_idx = {b["color"]: b["idx"] for b in current_balls}
        new_closest_idx = action.get("closest_idx")
        new_closest_color = action.get("closest_color")
        if new_closest_idx is not None:
            try:
                ci = int(new_closest_idx)
                if ci in idx_to_color:
                    claimed_closest_idx = ci
                    claimed_closest = idx_to_color[ci]
                else:
                    log.warning("round %d: closest_idx %s not in current "
                                "balls %s", round_idx, ci, list(idx_to_color))
            except (TypeError, ValueError):
                log.warning("round %d: closest_idx %r not parseable",
                            round_idx, new_closest_idx)
        elif isinstance(new_closest_color, str):
            cn = new_closest_color.lower()
            if cn in color_to_idx:
                claimed_closest = cn
                claimed_closest_idx = color_to_idx[cn]
            else:
                log.warning("round %d: closest_color %r not in current "
                            "colors %s", round_idx, cn,
                            list(color_to_idx))
        history.append({"round": round_idx, "thought": thought,
                        "action": action,
                        "current_balls": list(current_balls),
                        "claimed_closest": claimed_closest,
                        "claimed_closest_idx": claimed_closest_idx})

        # Early-stop: same pattern as mazenavfps.harness_structured/vp_bf —
        # if the harness can already see (via ground truth) that the
        # claimed-closest ball is inside the tolerance, declare PASS and
        # break out of the loop instead of dragging the model through
        # the remaining rounds (where it often oscillates and makes the
        # final-round verdict worse than an intermediate one).
        if claimed_closest_idx is not None:
            cb = next((b for b in current_balls
                       if b["idx"] == claimed_closest_idx), None)
            if cb is not None:
                bp = (cb["x"], cb["y"], cb["z"])
                tp = (target_pose["X"], target_pose["Y"], target_pose["Z"])
                early_pivot = math.sqrt(sum((a-b)**2 for a, b in zip(bp, tp)))
                eps = spec.get("extent_endpoints") or []
                if len(eps) >= 2:
                    a = (eps[0]["X"], eps[0]["Y"], eps[0]["Z"])
                    b = (eps[1]["X"], eps[1]["Y"], eps[1]["Z"])
                    abx, aby, abz = b[0]-a[0], b[1]-a[1], b[2]-a[2]
                    ab2 = abx*abx + aby*aby + abz*abz
                    if ab2 == 0:
                        early_seg = early_pivot
                    else:
                        apx, apy, apz = bp[0]-a[0], bp[1]-a[1], bp[2]-a[2]
                        t = max(0.0, min(1.0,
                            (apx*abx + apy*aby + apz*abz) / ab2))
                        cx = a[0]+abx*t; cy = a[1]+aby*t; cz = a[2]+abz*t
                        early_seg = math.sqrt((bp[0]-cx)**2+(bp[1]-cy)**2+(bp[2]-cz)**2)
                    early_dist = early_seg
                else:
                    early_dist = early_pivot
                if early_dist <= tolerance:
                    log.info("round %d: claimed ball within tolerance "
                             "(%.2f cm <= %.2f) — early stop",
                             round_idx, early_dist, tolerance)
                    # Take a final screenshot showing the post-spawn
                    # state so the user can see where the ball actually
                    # landed when early-stop triggered (the regular
                    # `round_NNN.png` is taken at the top of the round
                    # BEFORE spawn, so it would otherwise show the
                    # previous state).
                    try:
                        await take_screenshot(
                            engine, f"round_{round_idx:03d}_final", workspace)
                    except Exception:
                        pass
                    break

    # Verify — prefer idx (persistent key), fall back to color.
    closest_distance: Optional[float] = None
    closest_ball: Optional[dict] = None
    if claimed_closest_idx is not None:
        for b in current_balls:
            if b["idx"] == claimed_closest_idx:
                closest_ball = b
                break
    if closest_ball is None and claimed_closest:
        for b in current_balls:
            if b["color"] == claimed_closest:
                closest_ball = b
                break

    def _point_dist(p, q):
        return math.sqrt((p[0]-q[0])**2 + (p[1]-q[1])**2 + (p[2]-q[2])**2)

    def _segment_dist(p, a, b):
        """Min distance from point p to line segment a-b (all 3-tuples)."""
        ax, ay, az = a; bx, by, bz = b
        abx, aby, abz = bx-ax, by-ay, bz-az
        ab_len2 = abx*abx + aby*aby + abz*abz
        if ab_len2 == 0:
            return _point_dist(p, a)
        apx, apy, apz = p[0]-ax, p[1]-ay, p[2]-az
        t = max(0.0, min(1.0, (apx*abx + apy*aby + apz*abz) / ab_len2))
        cx = ax + abx * t; cy = ay + aby * t; cz = az + abz * t
        return _point_dist(p, (cx, cy, cz))

    if closest_ball:
        bp = (closest_ball["x"], closest_ball["y"], closest_ball["z"])
        # Distance to actor pivot (target_pose) — original semantics.
        pivot_dist = _point_dist(bp, (target_pose["X"], target_pose["Y"],
                                       target_pose["Z"]))
        # Distance to extent line segment, if defined. For a long mesh
        # like a spoon the actor pivot sits at one end of the handle
        # while the scoop is ~30 cm out — agents that visually land on
        # the scoop end up "far" from the pivot but visually correct.
        # Reporting both lets us distinguish placement-on-the-mesh from
        # placement-on-the-pivot.
        endpoints = spec.get("extent_endpoints") or []
        seg_dist: Optional[float] = None
        if len(endpoints) >= 2:
            a = (endpoints[0]["X"], endpoints[0]["Y"], endpoints[0]["Z"])
            b = (endpoints[1]["X"], endpoints[1]["Y"], endpoints[1]["Z"])
            seg_dist = _segment_dist(bp, a, b)
        # Final closest_distance for verdict: segment distance if
        # available (tighter / matches the visible mesh), else pivot.
        closest_distance = seg_dist if seg_dist is not None else pivot_dist

    passed = (closest_distance is not None and closest_distance <= tolerance)
    result = "PASS" if passed else "FAIL"

    summary = {
        "run_id": run_id, "model": vlm.model, "task": task_id,
        "target_alias": target_alias, "target_pose": target_pose,
        "tolerance_cm": tolerance, "grid_n": grid_n,
        "grid_fov_fraction": grid_fov_fraction,
        "extent_endpoints": spec.get("extent_endpoints"),
        "result": result, "rounds": len(history),
        "round_1_grid": grid,
        "final_balls": current_balls,
        "claimed_closest_idx": claimed_closest_idx,
        "claimed_closest_color": claimed_closest,
        "closest_ball_world_coord": closest_ball,
        "closest_ball_distance_cm": (round(closest_distance, 2)
                                     if closest_distance is not None else None),
        "closest_ball_pivot_distance_cm": (round(pivot_dist, 2)
                                           if closest_ball else None),
        "closest_ball_segment_distance_cm": (round(seg_dist, 2)
                                             if (closest_ball and seg_dist is not None) else None),
        "history": history,
    }
    (workspace / "episode_summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8")
    log.info("%s — claimed=%s dist=%s tol=%s",
             result, claimed_closest, closest_distance, tolerance)

    try:
        unwrap(await engine.python_exec(clear_query_markers_code()))
    except Exception:
        pass

    return EpisodeResult(
        run_id=run_id, model=vlm.model, task=task_id, result=result,
        rounds=len(history),
        target_alias=target_alias, target_pose=target_pose,
        final_balls=current_balls,
        claimed_closest_color=claimed_closest,
        closest_ball_distance=(round(closest_distance, 2)
                               if closest_distance is not None else None),
        tolerance_cm=tolerance,
        artifacts={"workspace": str(workspace)},
    )


__all__ = ["run", "EpisodeResult", "load_groundtruth"]
