"""Template for an interactive VeriWorld task.

Copy this file into ``veriworld/benchmark/interactive/<category>/<your_task>/task.py``
and fill in the three required pieces:

1. :func:`generate_params` — deterministic seed → task state (level name,
   spawn positions, goal, any scene parameters).
2. :func:`build_prompt` — construct the VLM message(s) for each turn,
   including text + any screenshots or condition-dependent observations.
3. :func:`run` — the episode loop: observe → think → act → verify.

Nothing in this file is required by the engine — the engine only needs
your :func:`run` to accept an :class:`InteractiveEngine`. The functions
below are a recommended decomposition; reshape them freely.

-------------------------------------------------------------------------------
Worked example (MazeNavFPS) — kept in comments for reference
-------------------------------------------------------------------------------

MazeNavFPS is shipped under ``veriworld/benchmark/interactive/navigation/mazenavfps/``
and demonstrates a common pattern:

* ``generate_params(seed)`` builds a random grid maze (DFS) and picks start
  and goal cells.
* The engine loads level ``"Untitled"`` and the task injects the maze
  parameters via ``builtins._MAZE_PARAMS`` before running ``setup.py``
  on the UE side to spawn the walls.
* Each turn: ``screenshot()`` → VLM with a prompt that includes the
  image + the condition-dependent info (coords, raycast, history grid,
  compass) → parse the VLM's move command → ``python_exec`` to step
  the camera → verify whether the goal cell was reached.
* Conditions are task-private: a free-form ``conditions`` dict controls
  whether coords are shown, whether raycast feedback is included, whether
  to render a 6-screenshot history grid (the 'purevision' variant), etc.
  New axes can be added without touching the engine or skills.

See the task's own ``task.md`` for the agent-facing prompt and its
``conditions.py`` for the list of supported keys.
-------------------------------------------------------------------------------
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from veriworld.common import RunLogger, VLMClient, png_to_base64_url
from veriworld.infra.interactive.engine import InteractiveEngine


# --- 1. Parameter generation -------------------------------------------------
def generate_params(seed: int, **task_kwargs: Any) -> dict:
    """Return a deterministic task-state dict for a given seed.

    Must be pure and seed-deterministic. Any non-determinism (random wall
    textures, random noise) belongs in the engine-side setup script, not
    here, so that the same seed reproduces the same params.json across
    runs.

    Example (from MazeNavFPS)::

        {
            "level": "Untitled",
            "grid_n": 10,
            "walls": [[(x1,y1),(x2,y2)], ...],
            "start": [0, 0],
            "goal": [9, 9],
        }
    """
    raise NotImplementedError("task author: implement generate_params")


# --- 2. Prompt construction --------------------------------------------------
def build_prompt(state: dict, observation: Any, conditions: dict) -> list:
    """Return an OpenAI-style ``messages=[...]`` list for one VLM call.

    ``state`` is whatever the task chooses to track across turns (step
    index, history, last feedback). ``observation`` is typically a
    screenshot path or a composite image. ``conditions`` is the free-form
    dict passed at run start — keys are task-specific.

    Example pattern::

        content = [
            {"type": "text", "text": task_instructions + history_text},
            {"type": "image_url",
             "image_url": {"url": png_to_base64_url(observation)}},
        ]
        if conditions.get("show_coords"):
            content.append({"type": "text", "text": f"Position: {state['pos']}"})
        return [{"role": "user", "content": content}]
    """
    raise NotImplementedError("task author: implement build_prompt")


# --- 3. Episode loop ---------------------------------------------------------
@dataclass
class EpisodeResult:
    success: bool
    steps: int
    artifacts: dict = field(default_factory=dict)


async def run(
    engine: InteractiveEngine,
    seed: int,
    vlm: VLMClient,
    logger: RunLogger,
    *,
    max_steps: int = 30,
    **conditions: Any,
) -> EpisodeResult:
    """Run one agent/model against one seed of this task.

    ``conditions`` is free-form — tasks declare their own keys. The
    engine never validates this dict; it is task-private.

    Skeleton (fill in ``parse_action`` and ``apply_action``)::

        params = generate_params(seed, **conditions)
        await engine.switch_level(params["level"])
        await engine.python_exec(build_setup_code(params))

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
        run_id = f"{vlm.model}_{your_condition_tag}_seed{seed}"
        workspace = logger.model_dir(run_id)
        logger.snapshot_model(
            task_module=__package__,
            model_name=run_id,
            seed=seed,
            resolved_args={"seed": seed, "model": vlm.model,
                           "max_steps": max_steps, **conditions},
            extra_config={"run_id": run_id,
                          "ablation": "<your-layer3-name>",
                          # any other task-specific knobs you want pinned
                          },
        )

        state = init_state(params)

        for step in range(max_steps):
            obs = await engine.screenshot()
            obs_path = save_obs(obs, logger, step)
            messages = build_prompt(state, obs_path, conditions)
            response = vlm.chat(messages)
            logger.write_text(f"{vlm.model}/step_{step:03d}_response.txt", response)

            action = parse_action(response)
            feedback = await apply_action(engine, action, state)
            state = update_state(state, action, feedback)

            if is_goal_reached(state, params):
                return EpisodeResult(success=True, steps=step + 1)

        return EpisodeResult(success=False, steps=max_steps)
    """
    raise NotImplementedError("task author: implement run")


__all__ = ["EpisodeResult", "generate_params", "build_prompt", "run"]
