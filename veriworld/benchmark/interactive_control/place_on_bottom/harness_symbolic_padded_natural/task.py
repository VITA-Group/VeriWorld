"""place_on_platform — SYMBOLIC harness with NATURAL-PROSE length padding (v2).

Sibling of harness_symbolic_padded along the filler-style axis. The v1
filler (seeded word-salad) turned out to trigger empty completions for
one model (claude-sonnet-4-6 via its API proxy) at any length, while
leaving nine other models exactly unchanged — an interesting proxy/model
quirk, but it prevents a uniform 10-model panel. This variant pads with
deterministic, coherent, task-irrelevant prose instead, which (a) all
models tolerate, and (b) is the better analogue of the combined
condition's extra channel: C adds coherent content (an image), not noise.

Same guarantees as v1: seeded determinism, no digits, no task-collideable
vocabulary (no colors, shapes, or spatial object words), identical
system prompt (imported from harness_symbolic), identical episode loop.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Optional

from .._episode import EpisodeResult, run_episode
from .._shared import QUERY_STATE_CODE, annotate_with_colors, parse_state, unwrap
from ..harness_symbolic.task import SYSTEM_PROMPT  # unchanged, by design

# Sentence templates about mundane administrative / pastoral matters.
# Slots are filled from neutral word banks; no digits, no task vocabulary.
_SUBJECTS = ["The committee", "The archivist", "The delegation", "The librarian",
             "The council", "The registrar", "The caretaker", "The chorus",
             "The gardener", "The steward"]
_VERBS = ["reviewed", "catalogued", "postponed", "discussed", "approved",
          "drafted", "rehearsed", "archived", "scheduled", "amended"]
_OBJECTS = ["the annual agenda", "the meeting minutes", "the seasonal program",
            "the visiting itinerary", "the membership roster", "the garden plan",
            "the lecture series", "the archive inventory", "the festival notes",
            "the travel arrangements"]
_TAILS = ["before the recess concluded", "while the hall remained quiet",
          "after a lengthy deliberation", "as the afternoon session ended",
          "and adjourned until the following week", "despite the crowded calendar",
          "once the correspondence was filed", "with little further debate",
          "and noted the matter for later review", "as was customary each term"]


def make_filler_natural(seed: int, n_words: int) -> str:
    """Deterministic coherent prose of ~n_words words."""
    rng = random.Random(0xFACE ^ seed)
    words: list[str] = []
    while len(words) < n_words:
        s = (f"{rng.choice(_SUBJECTS)} {rng.choice(_VERBS)} "
             f"{rng.choice(_OBJECTS)} {rng.choice(_TAILS)}.")
        words.extend(s.split())
    return " ".join(words[:n_words]).rstrip(".,") + "."


def _padded_observation_factory(seed: int, pad_words: int):
    filler = make_filler_natural(seed, pad_words)

    async def _obs(engine, params, prev_shot: Optional[Path]):
        out = unwrap(await engine.python_exec(QUERY_STATE_CODE))
        state = parse_state(out)
        state = annotate_with_colors(state, params)
        parts = [
            {"type": "text", "text": "Auxiliary context:\n" + filler},
            {"type": "text",
             "text": "Current symbolic state:\n```json\n"
                     + json.dumps(state, indent=2) + "\n```"},
        ]
        return parts, [], {"channel": "symbolic_padded_natural",
                           "pad_words": pad_words, "state": state}

    return _obs


async def run(engine, seed, vlm, logger, *, max_rounds: int = 8,
              pad_words: int = 750) -> EpisodeResult:
    return await run_episode(
        engine, seed, vlm, logger,
        harness_id=f"sympadnat{pad_words}",
        system_prompt=SYSTEM_PROMPT,
        observation_fn=_padded_observation_factory(seed, pad_words),
        package=__package__,
        max_rounds=max_rounds,
    )


__all__ = ["run", "EpisodeResult", "make_filler_natural"]
