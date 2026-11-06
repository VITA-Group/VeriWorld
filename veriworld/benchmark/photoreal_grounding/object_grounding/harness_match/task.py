"""object_ground — harness_match.

Pure-V single-round visual-grounding probe. The harness:

  1. Opens the apartment level + sets the camera to the task's
     `camera_start_pose` (or the global scene_start_pose).
  2. Reads each task-listed ball's `alias`, looks up the actual
     placed-actor coord via the shared `aliases.json` /
     `query_object_poses_code` pipe (so the spawn position matches
     the LIVE pose of that actor in the scene, not a hard-coded
     groundtruth coord — guards against a spawn point drifting if
     the level changes).
  3. Spawns one tinted sphere per ball at that actor's current world
     coord using `spawn_query_markers_code`.
  4. Takes ONE screenshot.
  5. Sends image + candidate alias list (with each alias's listed
     coord) to the VLM. The agent does NOT see which color sits on
     which alias — that is the question.
  6. Parses the agent's `{color: alias}` JSON and scores: number of
     matches that equal the ground-truth `{color: alias}` mapping.

This isolates the visual-grounding capability — can the model bind
the colored ball it sees in the image to the alias name we list in
text? — from the placement-and-rotation reasoning in
`object_grounding/harness_predict*`. Useful as a sanity check before
running the multi-round placement harnesses on a model.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from veriworld.common import RunLogger, VLMClient, format_prompt_txt, png_to_base64_url
from veriworld.infra.interactive.engine import InteractiveEngine

# Reuse object_grounding's UE-side helpers — same scene, same primitives.
from .._common import (
    OPEN_APARTMENT_CODE,
    QUERY_MARKER_COLORS,
    clear_query_markers_code,
    extract_thought,
    load_aliases,
    parse_object_poses,
    query_object_poses_code,
    reset_pawn_code,
    resolve_camera_view,
    spawn_query_markers_code,
    take_screenshot,
    unwrap,
)

log = logging.getLogger(__name__)

HERE = Path(__file__).parent
GROUNDTRUTH = HERE.parent / "groundtruth.json"


@dataclass
class EpisodeResult:
    run_id: str
    model: str
    task: str
    result: str
    n_correct: int
    n_total: int
    score_pct: float
    predicted: dict
    ground_truth: dict
    artifacts: dict = field(default_factory=dict)


def load_groundtruth() -> dict:
    return json.loads(GROUNDTRUTH.read_text(encoding="utf-8"))


_JSON_FENCE = re.compile(r"```json\s*(.+?)\s*```", re.DOTALL)


def _extract_mapping(text: str) -> Optional[dict]:
    """Pull a `{color: alias, color: alias, ...}` JSON object."""
    candidates: list[str] = []
    for m in _JSON_FENCE.finditer(text):
        candidates.append(m.group(1))
    # Fallback — any { ... } that has at least one quoted color name.
    color_re = "|".join(QUERY_MARKER_COLORS.keys())
    for m in re.finditer(r"\{[^{}]*\"(?:" + color_re + r")\"[^{}]*\}", text):
        candidates.append(m.group(0))
    for c in candidates:
        try:
            d = json.loads(c)
        except Exception:
            continue
        if isinstance(d, dict) and all(isinstance(v, str) for v in d.values()):
            # Filter: keys should be color names.
            keys = {str(k).lower() for k in d.keys()}
            if keys & set(QUERY_MARKER_COLORS.keys()):
                return {str(k).lower(): str(v) for k, v in d.items()}
    return None


def _system_prompt(task_id: str, spec: dict, candidate_aliases: list[str],
                   candidate_poses: dict, ball_colors: list[str]) -> str:
    desc = spec.get("description", "")
    cand_lines = []
    for alias in candidate_aliases:
        leaf_lower = None
        # Look up the alias's real mesh leaf via aliases.json.
        # candidate_poses is keyed by lowercased mesh leaf.
        for leaf, pose in candidate_poses.items():
            if leaf in alias.lower() or alias.lower().endswith(leaf.split("_")[-1]):
                leaf_lower = leaf
                break
        # Fallback — find by alias short name.
        if leaf_lower is None and alias.lower() in candidate_poses:
            leaf_lower = alias.lower()
        # If still not found, just list a placeholder.
        if leaf_lower and leaf_lower in candidate_poses:
            p = candidate_poses[leaf_lower]
            cand_lines.append(
                f"  {alias:<28s} listed_world_pose=(X={p['X']:.1f}, "
                f"Y={p['Y']:.1f}, Z={p['Z']:.1f})"
            )
        else:
            cand_lines.append(f"  {alias:<28s} (pose unavailable)")
    cand_block = "\n".join(cand_lines)

    colors_str = ", ".join(f'"{c}"' for c in ball_colors)
    n = len(ball_colors)

    return (
        "You are a visual-grounding probe. The screenshot below was taken\n"
        "in a photoreal UE5 kitchen scene. The harness has spawned a small\n"
        f"colored sphere ({n} of them) sitting ON or RIGHT NEXT TO various\n"
        "named candidate objects in the scene. Your job is to bind each\n"
        "colored ball to the name of the candidate it sits on.\n\n"
        f"TASK: {task_id}\n"
        f"description: {desc}\n\n"
        "CANDIDATE OBJECTS (alias + listed world coord):\n"
        f"{cand_block}\n\n"
        f"BALL COLORS PRESENT IN THE SCREENSHOT: {colors_str}\n"
        "You are NOT told which color sits on which candidate. Look at the\n"
        "image, identify each colored sphere, and decide which named\n"
        "candidate it is sitting on (by visual identification — physical\n"
        "shape, material, position relative to other objects).\n\n"
        "RESPONSE FORMAT:\n"
        "  Brief THOUGHT (plain text, optional), then a single JSON code\n"
        "  fence mapping each color to a candidate alias. Use the EXACT\n"
        "  alias string from the candidate list (case-sensitive).\n"
        "  Example for a 4-ball case:\n"
        "  ```json\n"
        '  {"red": "SM_wooden_spatula", "blue": "SM_wooden_spoon",\n'
        '   "green": "SM_black_spoon",  "yellow": "SM_black_spatula"}\n'
        "  ```\n"
        "Score = number of (color, alias) pairs you get right. PASS = all\n"
        "correct.\n"
    )


async def run(
    engine: InteractiveEngine,
    seed: int,
    vlm: VLMClient,
    logger: RunLogger,
    *,
    task_id: str = "match_4_utensils_pot_view",
) -> EpisodeResult:
    gt = load_groundtruth()
    if task_id not in gt["tasks"]:
        raise ValueError(f"unknown task: {task_id!r}; choices: {list(gt['tasks'])}")
    spec = gt["tasks"][task_id]
    aliases = load_aliases()
    start_pose = resolve_camera_view(spec, "default", gt["scene_start_pose"])
    balls = spec.get("balls") or []
    candidate_aliases = spec["candidate_aliases"]

    run_id = f"{vlm.model}_{task_id}_match"
    workspace = logger.model_dir(run_id)
    cfg = {
        "run_id": run_id, "model": vlm.model, "seed": seed, "task": task_id,
        "ablation": "harness_match", "info": "V", "action": "MapColorToAlias",
        "balls": balls, "candidates": candidate_aliases,
    }
    logger.snapshot_model(
        task_module=__package__, model_name=run_id, seed=seed,
        resolved_args={"seed": seed, "model": vlm.model, "task": task_id},
        extra_config=cfg,
    )

    out = unwrap(await engine.python_exec(OPEN_APARTMENT_CODE))
    log.info("open level: %s", out.strip())
    if "LEVEL_LOADING" in out:
        await asyncio.sleep(12.0)
    else:
        await asyncio.sleep(1.0)

    out = unwrap(await engine.python_exec(reset_pawn_code(start_pose)))
    log.info("reset pawn: %s", out.strip())
    await asyncio.sleep(0.7)

    # Cleanup any markers from a previous run on this UE.
    unwrap(await engine.python_exec(clear_query_markers_code()))

    # Look up the live pose of every alias in the candidate list — gives
    # us the listed_world_pose to show the agent.
    candidate_leaves = []
    for a in candidate_aliases:
        info = aliases["aliases"].get(a)
        if info is None:
            log.warning("alias %r not in aliases.json — skipping", a)
            continue
        candidate_leaves.append(info["real"].lower())
    candidate_poses = parse_object_poses(unwrap(
        await engine.python_exec(query_object_poses_code(candidate_leaves))))
    log.info("candidate poses: %s", candidate_poses)

    # Spawn the balls (colors + coords come straight from the task spec —
    # no model in the loop yet).
    spawn_queries = [
        {"x": float(b["x"]), "y": float(b["y"]), "z": float(b["z"]),
         "color": str(b["color"]).lower()}
        for b in balls
    ]
    unwrap(await engine.python_exec(spawn_query_markers_code(spawn_queries)))
    log.info("spawned %d balls", len(spawn_queries))
    await asyncio.sleep(0.5)

    shot = await take_screenshot(engine, "probe", workspace)

    ball_colors = [str(b["color"]).lower() for b in balls]
    SYSTEM = _system_prompt(task_id, spec, candidate_aliases,
                            candidate_poses, ball_colors)

    parts: list = []
    if shot and shot.exists():
        parts.append({"type": "image_url",
                      "image_url": {"url": png_to_base64_url(shot)}})
    parts.append({"type": "text",
                  "text": "Identify which colored ball sits on which candidate. "
                          "Reply with the JSON code fence."})
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": parts},
    ]
    (workspace / "prompt.txt").write_text(
        format_prompt_txt(messages, [shot.name] if shot else []),
        encoding="utf-8")

    t0 = time.time()
    response = await asyncio.get_event_loop().run_in_executor(
        None, vlm.chat, messages)
    log.info("VLM %.1fs", time.time() - t0)
    (workspace / "response.txt").write_text(response, encoding="utf-8")
    thought = extract_thought(response)
    (workspace / "thought.txt").write_text(thought, encoding="utf-8")

    predicted = _extract_mapping(response) or {}
    ground_truth = {str(b["color"]).lower(): b["alias"] for b in balls}

    n_correct = sum(1 for c, a in ground_truth.items()
                    if predicted.get(c, "").strip() == a)
    n_total = len(ground_truth)
    score_pct = (n_correct / n_total) * 100.0 if n_total else 0.0
    result = "PASS" if n_correct == n_total else "FAIL"
    log.info("%s — %d/%d (%.1f%%)", result, n_correct, n_total, score_pct)

    summary = {
        "run_id": run_id, "model": vlm.model, "task": task_id,
        "result": result, "n_correct": n_correct, "n_total": n_total,
        "score_pct": round(score_pct, 1),
        "predicted": predicted, "ground_truth": ground_truth,
        "thought": thought,
    }
    (workspace / "episode_summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8")

    # Cleanup so the next attach run starts blank.
    try:
        unwrap(await engine.python_exec(clear_query_markers_code()))
    except Exception:
        pass

    return EpisodeResult(
        run_id=run_id, model=vlm.model, task=task_id, result=result,
        n_correct=n_correct, n_total=n_total, score_pct=round(score_pct, 1),
        predicted=predicted, ground_truth=ground_truth,
        artifacts={"workspace": str(workspace)},
    )


__all__ = ["run", "EpisodeResult", "load_groundtruth"]
