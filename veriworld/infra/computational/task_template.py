"""Template for a computational VeriWorld task.

Copy this file into ``veriworld/benchmark/computational/<category>/<your_task>/task.py``.

Computational tasks differ from interactive tasks in three ways:

1. The UE instance is **restarted per round** — state does not leak
   between rounds. This is handled by :class:`ComputationalEngine`.
2. The agent produces a **complete artefact per round** (a parameter
   vector, a Python snippet, or a Slang shader) — not tick-by-tick
   commands.
3. Feedback is a **video + log** summary of the round, not an immediate
   response to each action.

Fill in the three required pieces (same slots as the interactive
template — it is deliberately analogous):

1. :func:`generate_params` — deterministic seed → task state.
2. :func:`build_prompt` — construct the VLM messages, typically
   including a frame-grid PNG extracted from the previous round's
   video plus any textual feedback.
3. :func:`run` — the round loop: restart engine → setup scene → submit
   artefact → record video → extract frames → score → repeat.

-------------------------------------------------------------------------------
Worked example (SurfaceBilliards) — kept in comments for reference
-------------------------------------------------------------------------------

SurfaceBilliards is shipped under
``veriworld/benchmark/computational/feedback/surface_billiards/`` and
demonstrates:

* Level = ``"Untitled"``; terrain + target crater are parameters of the
  seed (``generate_params``).
* Round 0 is an *observation round*: the engine flies the camera over
  the terrain, a video is recorded, ffmpeg extracts N frames, and
  :func:`veriworld.common.screenshot.make_grid` stitches them into a
  single PNG that the VLM sees.
* Rounds 1..N: agent outputs Python code that sets ball initial state
  (``velocity``, ``angle``, ``timing``); engine runs it, records a new
  video, extracts a new frame grid. A small Slang shader
  (``lean_verify/billiard_ball.slang``) does deterministic physics so
  the final ``PASS`` / ``FAIL`` can be read from the log.
* Between rounds :meth:`ComputationalEngine.next_round` kills the UE
  process and starts a fresh one — the shader / CUDA state resets, so
  round N is statistically independent of round N-1.

See the task's own ``task.md``, ``api.md``, and its shader under
``lean_verify/`` for the full worked contract.
-------------------------------------------------------------------------------
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List

from veriworld.common import RunLogger, VLMClient, make_grid, png_to_base64_url
from veriworld.infra.computational.engine import ComputationalEngine


# --- 1. Parameter generation -------------------------------------------------
def generate_params(seed: int, **task_kwargs: Any) -> dict:
    """Deterministic seed → per-round task state.

    Example (SurfaceBilliards)::

        {
            "level": "Untitled",
            "terrain": {"n": 80, "spacing": 20.0, "noise_seed": seed},
            "ball_start": [0, 0, 10],
            "target":     [45, 30, 0],
            "target_radius": 2.5,
        }
    """
    raise NotImplementedError("task author: implement generate_params")


# --- 2. Prompt construction --------------------------------------------------
def build_prompt(
    state: dict,
    observation_grid: Path,
    history: List[dict],
    conditions: dict,
) -> list:
    """Return OpenAI-style messages for one round.

    ``observation_grid`` is typically a PNG produced by
    :func:`veriworld.common.screenshot.make_grid` from the previous
    round's video frames. ``history`` is a task-defined record of past
    submissions + outcomes so the model can iterate.

    Example::

        return [{
            "role": "user",
            "content": [
                {"type": "text", "text": task_prompt + format_history(history)},
                {"type": "image_url", "image_url": {"url": png_to_base64_url(observation_grid)}},
            ],
        }]
    """
    raise NotImplementedError("task author: implement build_prompt")


# --- 3. Round loop -----------------------------------------------------------
@dataclass
class RoundResult:
    round_index: int
    submission: str
    passed: bool
    score: float
    video: Path | None = None
    frame_grid: Path | None = None


@dataclass
class EpisodeResult:
    success: bool
    rounds: List[RoundResult] = field(default_factory=list)


async def run(
    engine: ComputationalEngine,
    seed: int,
    vlm: VLMClient,
    logger: RunLogger,
    *,
    max_rounds: int = 5,
    **conditions: Any,
) -> EpisodeResult:
    """Run one agent/model against one seed of this task.

    Skeleton (fill in ``record_video``, ``extract_frames``,
    ``parse_submission``, ``verify``)::

        params = generate_params(seed, **conditions)
        rounds: list[RoundResult] = []

        # ── Per-model reproducibility snapshot — REQUIRED ────────────
        # Writes ``config.json`` + ``reproduce.bat`` + ``reproduce.sh``
        # into the per-model run dir so any model subfolder can be
        # rerun in isolation later. Forgetting this call is the single
        # most common reason a result dir turns out non-reproducible
        # — the seed-level ``snapshot_task`` (called by the
        # orchestrator) covers shared sources, but this is what
        # documents which model + which conditions produced THIS
        # subfolder. ``extra_config`` merges into the standard
        # ``config.json`` schema, so put your task's bespoke fields
        # (run_id, ablation tag, condition flags) there.
        model_name = vlm.name if hasattr(vlm, "name") else vlm.model
        workspace = logger.model_dir(model_name)
        logger.snapshot_model(
            task_module=__package__,
            model_name=model_name,
            seed=seed,
            resolved_args={"seed": seed, "model": model_name,
                           "exe": str(engine.exe),
                           "max_rounds": max_rounds, **conditions},
            # extra_config={"run_id": ..., "ablation": ...},   # if needed
        )

        # Round 0: observation flyover
        await engine.next_round(level=params["level"])
        await engine.python_exec(build_setup_code(params, ball=False))
        video = await record_video(engine, frames=120)
        grid = make_grid(extract_frames(video, n=6), cols=3)
        grid.save(workspace / "round_00_frames.png")

        history: list[dict] = []
        for r in range(1, max_rounds + 1):
            messages = build_prompt(params, grid, history, conditions)
            reply = vlm.chat(messages)
            logger.write_text(f"{vlm.model}/round_{r:02d}_response.txt", reply)

            submission = parse_submission(reply)          # e.g. python snippet
            await engine.next_round(level=params["level"])
            await engine.python_exec(build_setup_code(params, ball=True))
            await engine.python_exec(submission)          # task-specific
            video = await record_video(engine, frames=300)
            grid = make_grid(extract_frames(video, n=6), cols=3)

            passed, score = verify(engine, params)
            rounds.append(RoundResult(r, submission, passed, score, video, grid))
            history.append({"round": r, "passed": passed, "score": score})
            if passed:
                return EpisodeResult(success=True, rounds=rounds)

        return EpisodeResult(success=False, rounds=rounds)
    """
    raise NotImplementedError("task author: implement run")


__all__ = ["RoundResult", "EpisodeResult", "generate_params", "build_prompt", "run"]
