"""lightwar.harness_arc — iterative arc-projectile grounding."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from veriworld.common import RunLogger, VLMClient, format_prompt_txt, png_to_base64_url
from veriworld.infra.interactive.engine import InteractiveEngine

# Truncation observability, shared with the MovingShooter rebuttal
# harnesses so tallies can EXCLUDE token-limit-truncated episodes instead
# of scoring them as failures.
from .._response_diagnostics import (
    is_truncated, vlm_call_info)

# Ceiling on one model call. 180 s was below the honest latency of the
# slowest panel members (claude-sonnet-4-6 ~152 s, qwen3-vl-30b ~265 s
# measured 2026-07-26); a timeout must mean "stuck", not "slow model".
VLM_ROUND_TIMEOUT_S = 420.0

from .._common import (
    OPEN_FOREST_CODE,
    evenly_spaced_waypoints,
    find_landing,
    ground_z_at_code,
    load_groundtruth,
    parse_ground_z,
    randomize_task_pose,
    reset_pawn_pose_code,
    spawn_beacon_code,
    stack_frames,
    take_screenshot,
    unwrap,
    velocity_to_yaw_pitch,
    view_seed_for,
)

log = logging.getLogger(__name__)


@dataclass
class EpisodeResult:
    run_id: str
    model: str
    task: str
    result: str
    rounds: int
    target_xyz: tuple
    final_landing: Optional[tuple]
    closest_distance_cm: Optional[float]
    tolerance_cm: float
    artifacts: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Action parsing
# ---------------------------------------------------------------------------

_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_action(response: str) -> Optional[dict]:
    """Pull the first JSON code fence out of the model response and parse
    it as a dict. Returns ``None`` if no fence parses."""
    if not response:
        return None
    matches = _JSON_FENCE.findall(response)
    if not matches:
        # Permit a bare {...} block if the model forgot the fence.
        m = re.search(r"\{[^{}]*\"yaw_deg\"[^{}]*\}", response, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    for raw in matches:
        try:
            return json.loads(raw)
        except Exception:
            continue
    return None


def _extract_thought(response: str) -> str:
    """Return everything before the first ``` fence (or the whole text)."""
    if not response:
        return ""
    idx = response.find("```")
    return (response[:idx] if idx >= 0 else response).strip()


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

def _system_prompt(spec: dict, max_rounds: int, stack_layout: tuple,
                   *, give_miss_distance: bool = True,
                   beacon_color_name: str = "glowing",
                   give_state: bool = False,
                   give_images: bool = True,
                   target_apex: Optional[tuple] = None,
                   ground_z_beacon: Optional[float] = None) -> str:
    desc = spec.get("description", "")
    tol = spec.get("tolerance_cm", 3000.0)
    launch = spec["launch_pose"]
    rows, cols = stack_layout
    v0_lo, v0_hi = spec["v0_range_cm_s"]
    # NOTE (V arms): deliberately do NOT include the beacon's world coords
    # in the prompt. The point of the visual conditions is whether the
    # agent can estimate bearing, range and elevation of the target by EYE
    # from the launch-site preview + the FPV stack feedback. Giving it
    # (X, Y, Z) of the beacon would degrade the task to closed-form
    # ballistics — WHICH IS EXACTLY WHAT THE STRUCTURED (S) CONDITION
    # DOES, deliberately: S discloses the target apex and symbolic shot
    # feedback so the S-vs-V gap quantifies what perception is worth on
    # this task. The residual S difficulty is terrain interception (the
    # global heightfield is never disclosed — only raycast samples along
    # arcs actually flown).
    if give_state:
        assert target_apex is not None
        knows = (
            "WHAT YOU KNOW (structured state — exact, no images needed):\n"
            f"  launch site:  X={launch['X']:.0f}, Y={launch['Y']:.0f}, "
            f"Z={launch['Z']:.0f} cm\n"
            f"  TARGET (beacon apex): X={target_apex[0]:.0f}, "
            f"Y={target_apex[1]:.0f}, Z={target_apex[2]:.0f} cm\n"
            f"  terrain height at the beacon's XY: {ground_z_beacon:.0f} cm\n"
            "  The terrain between launch and target is hilly forest. You\n"
            "  are NOT given a height map — an intervening hill can\n"
            "  intercept your arc early. Each shot's feedback includes\n"
            "  sampled ground heights under the flown arc; use them to\n"
            "  detect and clear ridges.\n\n"
        )
    else:
        knows = (
            "WHAT YOU KNOW (only this — no target coords):\n"
            f"  launch site: X={launch['X']:.0f}, Y={launch['Y']:.0f}, "
            f"Z={launch['Z']:.0f} cm\n"
            "  the launch site is in a forest clearing; the beacon sits on\n"
            "  hilly terrain a few hundred metres away in the bearing shown\n"
            "  by the round-1 preview image.\n\n"
        )
    return (
        "You are an artillery agent in a UE5 forest scene. Your goal is\n"
        "to land a kinematic-parabolic projectile within a given tolerance\n"
        f"of a glowing **{beacon_color_name} beacon tower** standing on a distant hilltop.\n"
        + ("You are given the target's exact world coordinates and\n"
           "symbolic feedback for every shot — no images.\n\n"
           if give_state and not give_images else
           "You are given the target's exact world coordinates, plus the\n"
           "launch-site preview (round 1) and the per-shot FPV feedback\n"
           "stack (rounds 2+).\n\n"
           if give_state else
           "You are NOT told the beacon's world coordinates — you must\n"
           "estimate its bearing, range and elevation by EYE from the\n"
           "launch-site preview (round 1) and from the per-shot feedback\n"
           "stack (rounds 2+).\n\n")
        + f"TASK: {desc}\n"
        f"tolerance: {tol:.0f} cm  (PASS = your final-round landing point\n"
        f"           is within {tol:.0f} cm of the beacon's apex)\n"
        f"max_rounds: {max_rounds}\n\n"
        + knows
        + "ACTION FORMAT (one JSON code fence per round):\n"
        "```json\n"
        "{\n"
        # Example values are deliberately arbitrary — the previous example
        # (32.7 / 17.0 / 9500) happened to be the near-solution of the
        # fixed default task (groundtruth "hint" triple), which a reviewer
        # could read as a leak even though panels randomize the scene.
        '  "yaw_deg":  245.0,       // compass yaw of the launch direction (0=+X, 90=+Y)\n'
        '  "pitch_deg": 30.0,       // elevation above horizon (0=horizontal, 90=straight up)\n'
        f'  "v0_cm_s":   {(v0_lo + v0_hi) / 2:.1f},     // initial speed; valid {v0_lo:.0f}..{v0_hi:.0f}\n'
        '  "stop": false            // optional, true ends the episode\n'
        "}\n"
        "```\n\n"
        "TRAJECTORY MODEL — analytical, no UE physics:\n"
        "  pos(t) = launch + v0_vec * t + 0.5 * (0,0,-980) * t²\n"
        "  v0_vec = v0 * (cos(pitch)cos(yaw), cos(pitch)sin(yaw), sin(pitch))\n"
        "  Landing = first t where pos.z <= terrain_height(pos.xy).\n"
        "  The terrain is hilly; the harness raycasts to find the actual\n"
        "  ground intersection. Realistic ranges for hitting a hilltop\n"
        "  several hundred metres away tend to need yaw aligned within a\n"
        "  few degrees of the beacon's bearing, pitch ~10-25°, and\n"
        f"  v0 in the {v0_lo:.0f}-{v0_hi:.0f} cm/s envelope.\n\n"
        + (
            # Structured feedback description (S and C arms).
            ("FEEDBACK each round — symbolic shot telemetry: the landing\n"
             "point (X, Y, Z, t), the miss vector to the target (dx, dy,\n"
             "dz) with the scalar miss distance, and sampled points along\n"
             "the flown arc, each with the raycast terrain height under\n"
             "it (so you can see where the ground rose to meet the arc).\n"
             + (f"You ALSO receive a {rows}×{cols} stacked image of "
                f"{rows*cols} first-person\n"
                "frames along the arc (camera on the projectile).\n\n"
                if give_images else "\n"))
            if give_state else
            # Original visual feedback description (V arms).
            (f"FEEDBACK each round — a {rows}×{cols} stacked image of {rows*cols}\n"
             "first-person frames sampled at evenly-spaced waypoints along the\n"
             "previous shot's parabolic arc (camera placed on the projectile,\n"
             "facing along the velocity vector). Frame labels read 't=...s'.\n"
             "Use the stack to judge whether your shot fell SHORT, LONG, LEFT,\n"
             "or RIGHT of the beacon, and how high it flew vs the hilltop.\n"
             + ("You will ALSO receive a scalar miss distance (in metres) for\n"
                "each prior shot to help calibrate adjustments.\n\n"
                if give_miss_distance else
                "VISUAL-ONLY ABLATION: you will NOT receive a numerical miss\n"
                "distance. Judge over/under/left/right purely from the cyan\n"
                "beacon's position in the FPV stack and how it moves across\n"
                "the 6 frames.\n\n")))
        + "STRATEGY:\n"
        + (
            # Structured strategy (S; C gets both). Deliberately neutral:
            # the state is disclosed but the ballistic solution is the
            # model's job — no worked solution steps, no suggested values
            # beyond what the V prompt also gives.
            ("  - You know both endpoints exactly; compute your own firing\n"
             "    solution from the trajectory model above.\n"
             "  - The terrain can intercept a low arc before the target.\n"
             "    If a shot lands far short with its sampled ground\n"
             "    heights rising under the arc, you clipped a ridge —\n"
             "    raise pitch (a higher lob) rather than adding speed.\n"
             if give_state else
             f"  - Round 1: read the launch-site preview. The {beacon_color_name} beacon glow\n"
             "    is visible somewhere in the frame; estimate its compass\n"
             "    bearing (yaw) from its left/right position and a rough\n"
             "    range from how small the glow is. Take your best opening\n"
             "    guess; pitch around 15° and a mid-range v0 are reasonable\n"
             "    starting defaults.\n"
             f"  - Round 2+: read the 6-frame stack. If the {beacon_color_name} glow stays\n"
             "    ABOVE the horizon in late frames, you fell short — bump\n"
             "    pitch or v0. If it drops BELOW the horizon early, you flew\n"
             "    over — drop pitch or v0. If the glow drifts LEFT across the\n"
             "    sequence, your yaw was too LOW (CW rotation needed:\n"
             "    increase yaw); drift to the RIGHT means yaw was too HIGH.\n"))
        + "  - Make moderate adjustments at first (±5° / ±1000 cm/s); fine\n"
        "    tune as the landing closes in.\n"
        "  - Set `\"stop\": true` only if you're confident the shot landed\n"
        "    on or against the beacon. Otherwise the harness will fire\n"
        "    again with the next round's parameters.\n\n"
        "RESPONSE FORMAT each round:\n"
        "  Brief THOUGHT (plain text, optional), then ONE JSON code fence.\n"
    )


# ---------------------------------------------------------------------------
# Per-round trajectory capture
# ---------------------------------------------------------------------------

async def _capture_arc_frames(
    engine: InteractiveEngine,
    workspace: Path,
    round_idx: int,
    waypoints: list,
    *,
    width: int, height: int,
    back_offset_cm: float, lookahead_cm: float,
) -> list:
    """For each waypoint (t, x, y, z, vx, vy, vz), pose the pawn camera
    on the projectile (slightly behind the body so the lens isn't
    occluded) facing along the velocity vector and snap a screenshot.
    Returns a list of (frame_path, label) pairs."""
    frames = []
    for k, wp in enumerate(waypoints):
        t, x, y, z, vx, vy, vz = wp
        speed = math.sqrt(vx*vx + vy*vy + vz*vz) or 1.0
        # Unit velocity direction
        ux, uy, uz = vx / speed, vy / speed, vz / speed
        cam_x = x - ux * back_offset_cm
        cam_y = y - uy * back_offset_cm
        cam_z = z - uz * back_offset_cm
        yaw, pitch = velocity_to_yaw_pitch(vx, vy, vz)
        unwrap(await engine.python_exec(
            reset_pawn_pose_code(cam_x, cam_y, cam_z, pitch=pitch, yaw=yaw)))
        await asyncio.sleep(0.25)
        name = f"round_{round_idx:03d}_arc_f{k}"
        path = await take_screenshot(engine, name, workspace,
                                     width=width, height=height)
        label = f"t={t:.2f}s"
        frames.append((path, label))
    return frames


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

async def run(
    engine: InteractiveEngine,
    seed: int,
    vlm: VLMClient,
    logger: RunLogger,
    *,
    task_id: str = "hit_forest_hilltop_beacon",
    max_rounds: Optional[int] = None,
    give_miss_distance: bool = True,
    give_state: bool = False,
    give_images: bool = True,
    state_first: str = "",
    tolerance_cm: Optional[float] = None,
    random_spawn: bool = False,
    view_seed: Optional[int] = None,
    intensity_profile: str = "medium",
    tower_height_jitter_cm: float = 0.0,
) -> EpisodeResult:
    """Run one episode of arc-projectile grounding.

    ``give_miss_distance``: when True (default) the agent receives a
    SCALAR miss distance (in metres) every round 2+ and in the
    accumulated-knowledge block. When False (visual-only ablation),
    the agent only sees the FPV stack image as feedback — it must
    judge over/under/left/right purely by reading the beacon's
    on-screen position relative to its own flight path. The harness
    still computes the actual miss internally for PASS/FAIL scoring;
    it just doesn't tell the agent.

    ``random_spawn``: when True, randomize the launch position, the
    beacon position, and the beacon light colour per (task_id, seed)
    using the same view_seed contract as object_ground (so 12 parallel
    models all see the SAME randomized task and the comparison stays
    fair). Both points stay inside hand-tuned safe zones, separated
    by 300-600 m horizontally, with the beacon clearing a 5°
    elevation angle from the launch site. Defaults to False (uses
    the spec's hardcoded launch + beacon).
    """
    gt = load_groundtruth()
    if task_id not in gt["tasks"]:
        raise ValueError(f"unknown task: {task_id!r}; choices: {list(gt['tasks'])}")
    spec = dict(gt["tasks"][task_id])
    consts = gt["constants"]
    if max_rounds is None:
        max_rounds = int(spec.get("max_rounds", 8))
    # The default 3000 cm was calibrated for VISUAL aiming. The structured
    # condition, given exact target coordinates plus the analytic
    # trajectory model, clears it on the first or second shot (0.93 pass
    # rate measured 2026-07-27), so a tightened tolerance is used as a
    # secondary analysis to recover model discrimination. Table rows keep
    # the canonical 3000 cm.
    tol = float(tolerance_cm if tolerance_cm is not None
                else spec.get("tolerance_cm", 3000.0))
    spec = dict(spec)
    spec["tolerance_cm"] = tol
    width, height = consts["frame_render_size"]
    rows = int(consts["stack_grid_rows"])
    cols = int(consts["stack_grid_cols"])
    n_frames = int(consts["frames_per_round"])
    if rows * cols != n_frames:
        raise ValueError(f"stack {rows}x{cols} != n_frames {n_frames}")

    launch = spec["launch_pose"]
    beacon = spec["beacon"]
    v0_lo, v0_hi = spec["v0_range_cm_s"]
    rand_meta: Optional[dict] = None    # filled if random_spawn

    if give_state and not give_images:
        arm = "harness_symbolic"
        arm_tag = "_sym"
    elif give_state and state_first == "neutral":
        # C with the S arm's round framing, verbatim. The image is still
        # attached; nothing in the text points at it. Isolates the image
        # channel from the instruction that C's framing carries.
        arm = "harness_combined_neutral"
        arm_tag = "_combn"
    elif give_state and state_first == "ignore":
        # C with the S arm's round framing plus an explicit instruction to
        # disregard the attached image. Tests whether the C < S drop can be
        # recovered by telling the model not to use the visual channel.
        arm = "harness_combined_ignorevis"
        arm_tag = "_combi"
    elif give_state:
        arm = "harness_combined"
        arm_tag = "_comb"
    elif not give_miss_distance:
        arm = "harness_arc_visual_only"
        arm_tag = "_visonly"
    else:
        arm = "harness_arc"
        arm_tag = ""
    tol_tag = "" if tolerance_cm is None else f"_tol{int(tol)}"
    run_id = (f"{vlm.model}_{task_id}_arc"
              f"{'_rand' if random_spawn else ''}"
              f"{arm_tag}{tol_tag}_r{max_rounds}")
    workspace = logger.model_dir(run_id)
    cfg = {
        "run_id": run_id, "model": vlm.model, "seed": seed, "task": task_id,
        "ablation": arm,
        "give_state": give_state, "give_images": give_images,
        "action": "ParabolaLaunch (yaw, pitch, v0)",
        "tolerance_cm": tol,
        "max_rounds": max_rounds,
        "give_miss_distance": give_miss_distance,
        "random_spawn": random_spawn,
        "spec": spec,
    }
    logger.snapshot_model(
        task_module=__package__, model_name=run_id, seed=seed,
        resolved_args={"seed": seed, "model": vlm.model,
                       "task": task_id, "max_rounds": max_rounds,
                       "random_spawn": random_spawn,
                       "view_seed": view_seed,
                       "give_miss_distance": give_miss_distance,
                       "give_state": give_state,
                       "give_images": give_images},
        extra_config=cfg,
    )

    # Stub written before round 1: an episode whose process dies mid-flight
    # leaves this behind instead of vanishing (or being scored FAIL). The
    # real summary overwrites it on completion; a surviving
    # final_status == "UNFINISHED" marks a crash and tallies must exclude
    # it. Same convention as the MovingShooter rebuttal harnesses.
    (workspace / "episode_summary.json").write_text(json.dumps({
        "run_id": run_id, "model": vlm.model, "seed": seed, "task": task_id,
        "ablation": arm, "result": "UNFINISHED",
        "final_status": "UNFINISHED", "crashed": True,
        "rounds": 0, "history": [],
    }, indent=2), encoding="utf-8")

    # ---- 1. Open forest, settle, raycast ground at beacon XY ----
    out = unwrap(await engine.python_exec(OPEN_FOREST_CODE))
    log.info("open level: %r", out.strip())
    if "LEVEL_LOADING" in out:
        # OpenLevel was just kicked off — the world handle is dead and the
        # forest landscape needs ~6s for streaming + GI to settle.
        await asyncio.sleep(8.0)
    else:
        await asyncio.sleep(1.5)
    # Verify forest is actually loaded — re-exec since the prior context
    # was invalidated by OpenLevel.
    cur_name = unwrap(await engine.python_exec(
        "import unreal_runtime as ur\n"
        "cur = ur.Engine.GameplayStatics.GetCurrentLevelName(True)\n"
        "print(f'CUR_LEVEL {cur}')\n"))
    log.info("current level: %r", cur_name.strip())
    if "Showcase" not in cur_name:
        # A second OpenLevel poke if we somehow still aren't on Showcase.
        log.warning("not on Showcase yet, re-issuing OpenLevel and waiting")
        await engine.python_exec(OPEN_FOREST_CODE)
        await asyncio.sleep(8.0)

    # ---- 1b. Optional per-task randomization. Fixed view_seed formula
    # so 12 parallel models all see the SAME randomized task for the
    # same (task_id, seed). ----
    if random_spawn:
        vs = view_seed if view_seed is not None else view_seed_for(task_id, seed)
        rand_meta = await randomize_task_pose(
            engine, spec, vs,
            intensity_profile=intensity_profile,
            tower_height_jitter_cm=tower_height_jitter_cm,
        )
        log.info(
            "random_spawn: launch=(%.0f,%.0f,%.0f) beacon=(%.0f,%.0f) "
            "color=%s range=%.0f cm elev=%.1f° (%d tries)",
            rand_meta["launch_pose"]["X"], rand_meta["launch_pose"]["Y"],
            rand_meta["launch_pose"]["Z"], rand_meta["beacon"]["X"],
            rand_meta["beacon"]["Y"], rand_meta["beacon_color_name"],
            rand_meta["horizontal_range_cm"], rand_meta["elevation_angle_deg"],
            rand_meta["attempts"])
        # Override the local handles used downstream.
        spec = dict(spec)
        spec["launch_pose"] = rand_meta["launch_pose"]
        spec["beacon"] = rand_meta["beacon"]
        launch = spec["launch_pose"]
        beacon = spec["beacon"]
        cfg["random_spawn_meta"] = {
            k: v for k, v in rand_meta.items() if k != "beacon"
        }

    ground_z_beacon = None
    for attempt in range(4):
        g_out = unwrap(await engine.python_exec(
            ground_z_at_code(beacon["X"], beacon["Y"])))
        ground_z_beacon = parse_ground_z(g_out)
        if ground_z_beacon is not None:
            break
        log.warning("ground raycast attempt %d at beacon XY missed; "
                    "out=%r — sleeping then retrying", attempt + 1, g_out)
        await asyncio.sleep(2.0)
    if ground_z_beacon is None:
        raise RuntimeError(
            f"ground raycast at beacon XY ({beacon['X']}, {beacon['Y']}) "
            f"failed after 4 attempts; current level={cur_name.strip()!r}, "
            f"last output={g_out!r}")
    apex_z = ground_z_beacon + beacon["tower_height_cm"] + beacon["light_gap_cm"]
    target_apex = (beacon["X"], beacon["Y"], apex_z)
    log.info("beacon ground_z=%.1f apex=%s", ground_z_beacon, target_apex)

    # ---- 2. Spawn beacon (idempotent — wipes prior _LightwarBeacon_*) ----
    spawn_out = unwrap(await engine.python_exec(
        spawn_beacon_code(beacon, ground_z_beacon)))
    log.info("beacon spawn: %s", spawn_out.strip().splitlines()[-1] if spawn_out else "")
    await asyncio.sleep(0.4)

    # ---- 3. Pose pawn at the launch site facing the beacon for the
    #         round-1 preview ----
    bearing_yaw = math.degrees(math.atan2(
        beacon["Y"] - launch["Y"], beacon["X"] - launch["X"]))
    bearing_pitch = math.degrees(math.atan2(
        apex_z - launch["Z"],
        math.hypot(beacon["X"] - launch["X"], beacon["Y"] - launch["Y"])))
    unwrap(await engine.python_exec(reset_pawn_pose_code(
        launch["X"], launch["Y"], launch["Z"],
        pitch=max(-30.0, min(30.0, bearing_pitch + 5.0)), yaw=bearing_yaw)))
    await asyncio.sleep(0.4)

    # Preview frame for round 1 (camera is at launch site facing the
    # beacon's bearing — set up above). The agent uses this image to
    # estimate the beacon's yaw + how high the hilltop sits. The
    # symbolic arm skips it — its round 1 is fully specified by the
    # STATE block, and skipping keeps S episodes image-free end to end.
    LX = launch["X"]; LY = launch["Y"]
    preview_path = None
    if give_images:
        preview_path = await take_screenshot(
            engine, "round_001_preview", workspace, width=width, height=height)

    # ---- 4. VLM round loop ----
    color_name = (rand_meta["beacon_color_name"] if rand_meta is not None
                  else "cyan")
    SYSTEM = _system_prompt(spec, max_rounds, (rows, cols),
                            give_miss_distance=give_miss_distance,
                            beacon_color_name=color_name,
                            give_state=give_state,
                            give_images=give_images,
                            target_apex=target_apex,
                            ground_z_beacon=ground_z_beacon)
    history: list = []
    early_stopped = False
    early_stop_reason: Optional[str] = None
    last_landing: Optional[tuple] = None
    last_distance: Optional[float] = None
    prev_stack_path: Optional[Path] = None
    prev_symbolic_feedback: Optional[str] = None

    # Knowledge accumulation — single-turn dialogue, clean text history.
    # Each round's ``vlm.chat`` call sees only:
    #   [SYSTEM, user(current_image + current_text + knowledge_block)]
    # The knowledge block is built from ``knowledge_entries``, a clean
    # structured summary of every prior round (Thought + Action +
    # Result). We deliberately do NOT keep a multi-turn message
    # history with past images — that scales token cost linearly with
    # rounds and triggered 60s VLM timeouts on gemini-3.1-pro by
    # round 4. The text block carries the same information in ~50x
    # fewer tokens.
    knowledge_entries: list = []

    def _render_knowledge_block() -> str:
        if not knowledge_entries:
            return ""
        out = ["", "**Accumulated knowledge** (your own prior shots in this episode):", ""]
        for e in knowledge_entries:
            out.append(f"## Round {e['round']}")
            t = (e['thought'] or '').strip()
            if t:
                out.append(f"**Thought**: {t}")
            a = e['action']
            if a:
                out.append(f"**Action**: yaw={a['yaw_deg']:.2f}°, "
                           f"pitch={a['pitch_deg']:.2f}°, v0={a['v0_cm_s']:.0f} cm/s")
            r = e.get('result')
            if r:
                out.append(f"**Result**: {r}")
            out.append("")
        return "\n".join(out)

    for round_idx in range(1, max_rounds + 1):
        # Build user message: round 1 sees the launch-site preview, rounds
        # 2+ see the previous shot's stacked frames + accumulated history.
        parts: list = []
        attached_image_name = "(none)"
        if round_idx == 1:
            if give_images:
                parts.append({"type": "image_url",
                              "image_url": {"url": png_to_base64_url(preview_path)}})
                attached_image_name = preview_path.name
            if give_images and not state_first:
                user_text = (
                    f"**Round 1/{max_rounds}** — gun-camera preview.\n\n"
                    "This image is taken from the launch site facing the\n"
                    "beacon's compass bearing. Use it to estimate yaw and\n"
                    "the visible elevation gap to the hilltop, then emit\n"
                    "your first launch parameters."
                )
                if give_state:
                    user_text += (
                        "\nYou also have the exact target coordinates in the\n"
                        "system prompt — cross-check your visual estimate\n"
                        "against them."
                    )
            else:
                # Also the state_first arms: same words the S arm sees, with
                # the image attached above but unmentioned.
                user_text = (
                    f"**Round 1/{max_rounds}**.\n\n"
                    "Compute your first launch parameters from the\n"
                    "structured state in the system prompt."
                )
                if state_first == "ignore":
                    user_text += (
                        "\nA launch-site image is attached. Ignore it: the\n"
                        "target's exact coordinates are already in the system\n"
                        "prompt, so the bearing follows from arithmetic alone."
                    )
        elif last_landing is None:
            # No shot has been fired yet — every prior round failed to
            # parse (or timed out). There is no stack image, no telemetry
            # and no miss distance to report; referencing any of them
            # crashes the episode. Re-issue the round-1 framing instead.
            if give_images and preview_path is not None:
                parts.append({"type": "image_url",
                              "image_url": {"url": png_to_base64_url(preview_path)}})
                attached_image_name = preview_path.name
            user_text = (
                f"**Round {round_idx}/{max_rounds}** — your previous reply\n"
                "could not be parsed as a launch action, so no shot was\n"
                "fired and there is no feedback yet.\n\n"
                "Emit your launch parameters as ONE JSON code fence, e.g.\n"
                "```json\n"
                '{"yaw_deg": 245.0, "pitch_deg": 30.0, "v0_cm_s": 11000.0}\n'
                "```\n"
            )
            user_text += _render_knowledge_block()
        else:
            if give_images:
                parts.append({"type": "image_url",
                              "image_url": {"url": png_to_base64_url(prev_stack_path)}})
                attached_image_name = prev_stack_path.name
            if give_images and not state_first:
                user_text = (
                    f"**Round {round_idx}/{max_rounds}** — feedback from your\n"
                    f"previous shot.\n\n"
                    f"Stacked image (read top-left → top-right → bottom-right):\n"
                    f"  6 first-person frames along the parabolic arc you fired\n"
                    f"  last round (camera on the projectile, looking forward).\n\n"
                )
            else:
                user_text = (
                    f"**Round {round_idx}/{max_rounds}** — feedback from your\n"
                    f"previous shot.\n\n"
                )
                if state_first == "ignore":
                    user_text += (
                        "An FPV frame stack is attached. Ignore it and use\n"
                        "the symbolic telemetry below.\n\n"
                    )
            if give_state and prev_symbolic_feedback:
                user_text += prev_symbolic_feedback + "\n"
            if give_miss_distance and last_distance is not None:
                user_text += (
                    f"Your previous shot missed by **{last_distance/100.0:.1f} m**\n"
                    f"(tolerance {tol/100.0:.0f} m)."
                    + (" Use the stack to figure\n"
                       "out direction (short / long, left / right, too high / too\n"
                       "low) and adjust yaw / pitch / v0 accordingly.\n"
                       if give_images and not give_state else
                       " Adjust yaw / pitch / v0 accordingly.\n")
                )
            elif give_images:
                user_text += (
                    "VISUAL-ONLY: you will NOT be told how far you missed.\n"
                    "Judge whether the shot fell short / long / left / right /\n"
                    "too high / too low purely by reading the beacon glow's\n"
                    "position relative to your projectile's flight path in\n"
                    "the 6 frames, then adjust yaw / pitch / v0 accordingly.\n"
                )
            user_text += _render_knowledge_block()
            user_text += "\nReply with one JSON code fence."
        parts.append({"type": "text", "text": user_text})

        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": parts},
        ]

        prompt_attachments = [attached_image_name]
        (workspace / f"round_{round_idx:03d}_prompt.txt").write_text(
            format_prompt_txt(messages, prompt_attachments),
            encoding="utf-8")

        t0 = time.time()
        try:
            response = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None, vlm.chat, messages),
                timeout=VLM_ROUND_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            # Record and CONTINUE — one flaky API round must not end the
            # episode (MovingShooter-rebuttal semantics, uniform across
            # all lightwar arms since every arm reruns fresh).
            log.warning("round %d: VLM no response in %.0fs",
                        round_idx, VLM_ROUND_TIMEOUT_S)
            history.append({"round": round_idx, "thought": "(VLM timeout)",
                            "action": None, "timeout": True})
            continue
        except Exception as e:  # noqa: BLE001
            log.error("VLM error at round %d: %s", round_idx, e)
            history.append({"round": round_idx, "thought": "(VLM error)",
                            "action": None, "vlm_error": str(e)[:200]})
            continue
        log.info("round %d: VLM %.1fs", round_idx, time.time() - t0)
        vlm_info = vlm_call_info(vlm)

        (workspace / f"round_{round_idx:03d}_response.txt").write_text(
            response, encoding="utf-8")
        thought = _extract_thought(response)
        (workspace / f"round_{round_idx:03d}_thought.txt").write_text(
            thought, encoding="utf-8")

        action = _extract_action(response)
        if not action:
            log.warning("round %d: no valid action JSON%s", round_idx,
                        " (TRUNCATED at token limit)"
                        if is_truncated(vlm_info) else "")
            history.append({"round": round_idx, "thought": thought,
                            "action": None, "parse_error": True,
                            "truncated": is_truncated(vlm_info),
                            "vlm": vlm_info})
            continue

        try:
            yaw_deg = float(action["yaw_deg"])
            pitch_deg = float(action["pitch_deg"])
            v0 = float(action["v0_cm_s"])
        except (KeyError, ValueError, TypeError) as e:
            log.warning("round %d: missing/invalid launch params: %s",
                        round_idx, e)
            history.append({"round": round_idx, "thought": thought,
                            "action": action, "parse_error": True})
            continue

        # Clamp v0 to the configured envelope.
        v0 = max(v0_lo, min(v0_hi, v0))
        # Pitch must be > 0 for any arc to leave the ground.
        pitch_deg = max(0.5, min(89.0, pitch_deg))

        # Phase A — fire the parabola silently (no debug visualization),
        # walk it step-by-step with one ground raycast per step until
        # we find the first ground intersection. That fixes ``t_land``.
        result = await find_landing(
            engine, (launch["X"], launch["Y"], launch["Z"]),
            yaw_deg=yaw_deg, pitch_deg=pitch_deg, v0_cm_s=v0,
            ground_raycast_code=ground_z_at_code,
            parse_ground_z=parse_ground_z,
            unwrap=unwrap,
            gravity_cm_s2=consts["gravity_cm_s2"],
            dt=consts["trajectory_dt_s"] * 2,         # 0.10 s steps
            max_t=consts["trajectory_max_t_s"],
        )
        landing_d = result["landing"]
        off_map = bool(result["off_map"])
        if landing_d is None:
            # Trajectory never intersected the landscape (fell off the
            # world or stayed airborne past max_t). Pretend it landed
            # at the last sampled (X,Y) below the launch height so the
            # stack still has frames to show.
            samples = result["samples"]
            last_below = next((s for s in reversed(samples)
                               if s["z"] <= launch["Z"] - 5000.0), samples[-1])
            landing_d = {"X": last_below["x"], "Y": last_below["y"],
                          "Z": last_below["z"], "t": last_below["t"]}
        landing = (landing_d["X"], landing_d["Y"], landing_d["Z"], landing_d["t"])
        last_landing = landing

        dx = landing[0] - target_apex[0]
        dy = landing[1] - target_apex[1]
        dz = landing[2] - target_apex[2]
        last_distance = math.sqrt(dx*dx + dy*dy + dz*dz)
        log.info("round %d: launch yaw=%.2f pitch=%.2f v0=%.0f -> "
                 "land=(%.0f,%.0f,%.0f) at t=%.2f, dist=%.0f cm%s "
                 "(walked %d steps)",
                 round_idx, yaw_deg, pitch_deg, v0,
                 landing[0], landing[1], landing[2], landing[3],
                 last_distance, " (OFF-MAP)" if off_map else "",
                 len(result["samples"]))

        # Phase B — pick 6 evenly-spaced waypoints in (0, t_land). This
        # is closed-form analytical (no raycasts), so the camera moves
        # smoothly through the arc without weird quantisation.
        waypoints = evenly_spaced_waypoints(
            (launch["X"], launch["Y"], launch["Z"]),
            yaw_deg=yaw_deg, pitch_deg=pitch_deg, v0_cm_s=v0,
            t_land=landing[3], n_frames=n_frames,
            gravity_cm_s2=consts["gravity_cm_s2"],
        )

        # Capture the 6 FPV frames + stack them (image arms only — the
        # symbolic arm's feedback is the telemetry block below, and
        # skipping capture keeps its episodes image-free and ~40 s/round
        # faster).
        if give_images:
            frames = await _capture_arc_frames(
                engine, workspace, round_idx, waypoints,
                width=width, height=height,
                back_offset_cm=consts["fpv_back_offset_cm"],
                lookahead_cm=consts["fpv_lookahead_cm"])
            stack_path = workspace / f"round_{round_idx:03d}_stack.png"
            labels = [lbl for (_p, lbl) in frames]
            stack_frames([p for (p, _l) in frames], stack_path,
                         cols=cols, rows=rows, labels=labels)
            prev_stack_path = stack_path

        # Symbolic telemetry for the NEXT round (S and C arms). Sampled
        # from what this round already computed — zero extra UE calls.
        # The arc samples carry the raycast ground height under each
        # sampled point, which is the honest symbolic analogue of
        # "seeing" terrain along the flown arc (never a global map).
        if give_state:
            samples = result["samples"]
            step = max(1, len(samples) // 8)
            picked = samples[::step][:8]
            lines = [
                "**Previous shot telemetry:**",
                f"  launch params: yaw={yaw_deg:.2f}°, pitch={pitch_deg:.2f}°, "
                f"v0={v0:.0f} cm/s",
                f"  landing: X={landing[0]:.0f}, Y={landing[1]:.0f}, "
                f"Z={landing[2]:.0f} at t={landing[3]:.2f}s"
                + (" (OFF-MAP)" if off_map else ""),
                f"  miss vector to target: dx={dx:.0f}, dy={dy:.0f}, "
                f"dz={dz:.0f}  (|miss|={last_distance:.0f} cm)",
                "  arc samples (t, x, y, z, ground_z under point):",
            ]
            for s in picked:
                gz = s.get("ground_z")
                lines.append(
                    f"    t={s['t']:.2f}  ({s['x']:.0f}, {s['y']:.0f}, "
                    f"{s['z']:.0f})  ground_z="
                    + (f"{gz:.0f}" if gz is not None else "n/a"))
            prev_symbolic_feedback = "\n".join(lines)

        history.append({
            "round": round_idx,
            "thought": thought,
            "action": {"yaw_deg": yaw_deg, "pitch_deg": pitch_deg,
                        "v0_cm_s": v0,
                        "stop": bool(action.get("stop", False))},
            "landing": {"X": landing[0], "Y": landing[1],
                         "Z": landing[2], "t": landing[3]},
            "distance_cm": round(last_distance, 2),
            "stack": (stack_path.name if give_images else None),
            "truncated": is_truncated(vlm_info),
            "vlm": vlm_info,
        })

        # (B) Append a knowledge entry the NEXT round's user message will
        # render. We summarise the result as a SCALAR miss distance (no
        # landing coords) so the model sees its own action history
        # without us leaking the beacon's position. In the visual-only
        # ablation we DROP the miss distance entirely — only thought +
        # action are kept, so the model has to read the FPV stack to
        # judge how prior shots fared.
        knowledge_entry = {
            "round": round_idx,
            "thought": thought,
            "action": {"yaw_deg": yaw_deg, "pitch_deg": pitch_deg,
                        "v0_cm_s": v0},
        }
        if give_state:
            # S/C: landing coords are not a leak — the target's exact
            # position is already in the prompt. Full symbolic history.
            knowledge_entry["result"] = (
                f"landed at ({landing[0]:.0f}, {landing[1]:.0f}, "
                f"{landing[2]:.0f}), missed by {last_distance/100.0:.1f} m "
                f"(dx={dx:.0f}, dy={dy:.0f}, dz={dz:.0f}; "
                f"{'OFF-MAP' if off_map else 'on-map'})")
        elif give_miss_distance:
            knowledge_entry["result"] = (
                f"missed by {last_distance/100.0:.1f} m "
                f"(tolerance {tol/100.0:.0f} m, "
                f"{'OFF-MAP' if off_map else 'on-map'})")
        knowledge_entries.append(knowledge_entry)
        if action.get("stop") is True:
            log.info("round %d: agent stopped (claim convergence dist=%.0f)",
                     round_idx, last_distance)
            early_stopped = True
            early_stop_reason = "agent_stop"
            break
        if last_distance <= tol:
            log.info("round %d: landing within tolerance (%.0f <= %.0f) — early stop",
                     round_idx, last_distance, tol)
            early_stopped = True
            early_stop_reason = "harness_tolerance"
            break

    # ---- 5. Final verifier ----
    if last_landing is None:
        result = "FAIL"
    else:
        result = "PASS" if last_distance <= tol else "FAIL"

    summary = {
        "run_id": run_id, "model": vlm.model, "task": task_id, "seed": seed,
        "ablation": arm,
        "tolerance_cm": tol, "max_rounds": max_rounds,
        "launch_pose": launch,
        "beacon": beacon,
        "target_apex": {"X": target_apex[0], "Y": target_apex[1], "Z": target_apex[2]},
        "result": result,
        # Both conventions: lightwar's own aggregation reads "result"
        # (PASS/FAIL); the rebuttal tallies read final_status/crashed.
        "final_status": result,
        "crashed": False,
        "rounds": len(history),
        "early_stopped": early_stopped,
        "early_stop_reason": early_stop_reason,
        "final_landing": (None if last_landing is None else
                          {"X": last_landing[0], "Y": last_landing[1],
                           "Z": last_landing[2], "t": last_landing[3]}),
        "final_distance_cm": (None if last_distance is None
                              else round(last_distance, 2)),
        "history": history,
    }
    (workspace / "episode_summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8")

    # Standalone knowledge.md so reviewers can read the agent's accumulated
    # reasoning over the episode at a glance (matches the VeriWorld
    # harness_win convention).
    md_lines = [f"# Lightwar episode — {run_id}", ""]
    for e in knowledge_entries:
        md_lines.append(f"## Round {e['round']}")
        if e.get('thought'):
            md_lines += ["", "**Thought**: " + str(e['thought']).strip(), ""]
        a = e['action']
        if a:
            md_lines.append(
                f"**Action**: yaw={a['yaw_deg']:.2f}°, "
                f"pitch={a['pitch_deg']:.2f}°, v0={a['v0_cm_s']:.0f} cm/s")
        if e.get('result'):
            md_lines.append(f"**Result**: {e['result']}")
        md_lines.append("")
    (workspace / "knowledge.md").write_text(
        "\n".join(md_lines), encoding="utf-8")
    log.info("%s — final_dist=%s tolerance=%.0f rounds=%d",
             result,
             ("none" if last_distance is None else f"{last_distance:.0f}"),
             tol, len(history))

    return EpisodeResult(
        run_id=run_id, model=vlm.model, task=task_id,
        result=result, rounds=len(history),
        target_xyz=target_apex,
        final_landing=last_landing,
        closest_distance_cm=last_distance, tolerance_cm=tol,
        artifacts={"workspace": str(workspace)})
