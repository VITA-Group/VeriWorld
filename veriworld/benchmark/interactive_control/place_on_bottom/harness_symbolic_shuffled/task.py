"""place_on_platform — SYMBOLIC + shuffled serialization (ablation arm).

Same SYSTEM_PROMPT, episode loop, verifier, and parser as
harness_symbolic — the ONLY difference is how the identical state is
*serialized*:

  * the object list is deterministically permuted per (seed, round);
  * each entry's keys are emitted in reversed order
    (mesh, rgb, scale_cm, position, label instead of label-first).

No information is added or removed. If the reviewers' "prompt design /
representation format" alternative explained symbolic-arm performance,
scrambling the representation would move the scores; the prediction from
the paper's account (models read the JSON semantically) is a null effect.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Optional

from .._episode import EpisodeResult, run_episode
from .._shared import QUERY_STATE_CODE, annotate_with_colors, parse_state, unwrap
from ..harness_symbolic.task import SYSTEM_PROMPT  # unchanged, by design


def _scramble(state: list[dict], seed: int, round_idx: int) -> list[dict]:
    """Deterministic permutation of list order + reversed key order.

    Seeded by (seed, round) so reruns are reproducible but consecutive
    rounds show different orders — a model relying on positional habits
    rather than reading labels would be maximally disrupted."""
    rng = random.Random(0xD1CE ^ (seed * 1000 + round_idx))
    order = list(range(len(state)))
    rng.shuffle(order)
    return [{k: state[i][k] for k in reversed(list(state[i]))} for i in order]


def _shuffled_observation_factory(seed: int):
    round_counter = {"n": 0}

    async def _obs(engine, params, prev_shot: Optional[Path]):
        round_counter["n"] += 1
        out = unwrap(await engine.python_exec(QUERY_STATE_CODE))
        state = parse_state(out)
        state = annotate_with_colors(state, params)
        shuffled = _scramble(state, seed, round_counter["n"])
        txt = ("Current symbolic state:\n```json\n"
               + json.dumps(shuffled, indent=2) + "\n```")
        parts = [{"type": "text", "text": txt}]
        # Keep the CANONICAL state in the record so downstream analysis
        # is order-independent; note the presented permutation size.
        return parts, [], {"channel": "symbolic_shuffled",
                           "round": round_counter["n"], "state": state}

    return _obs


async def run(engine, seed, vlm, logger, *, max_rounds: int = 8) -> EpisodeResult:
    return await run_episode(
        engine, seed, vlm, logger,
        harness_id="symshuf",
        system_prompt=SYSTEM_PROMPT,
        observation_fn=_shuffled_observation_factory(seed),
        package=__package__,
        max_rounds=max_rounds,
    )


__all__ = ["run", "EpisodeResult"]
