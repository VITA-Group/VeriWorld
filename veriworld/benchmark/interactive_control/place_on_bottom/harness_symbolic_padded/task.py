"""place_on_platform — SYMBOLIC harness with LENGTH-MATCHED PADDING.

Length-control arm for the C < S diagnosis (rebuttal experiment for
GHV8 W4/Q3 and aqX2 Q2). Identical to harness_symbolic in every way
— same system prompt, same state serialization, same single-span
action loop — except that each round's user message is prefixed with
a deterministic, task-irrelevant prose block sized to approximate the
token cost of the screenshot that the COMBINED condition adds on top
of S.

Interpretation:
  * padded-S ≈ S      → context length / irrelevant extra content do
                        NOT explain C < S; the image channel itself
                        (integration) is implicated.
  * padded-S << S     → length/irrelevance alone degrades the model;
                        C < S cannot be read as an integration
                        failure without this correction.

The filler is generated from the episode seed (deterministic across
reruns), contains NO digits and NO task-collideable vocabulary
(no color words, no geometry/object words), and is prefixed
"Auxiliary context:" — mirroring how C's screenshot arrives as an
additional unexplained channel rather than one labeled irrelevant.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Optional

from .._episode import EpisodeResult, run_episode
from .._shared import QUERY_STATE_CODE, annotate_with_colors, parse_state, unwrap
from ..harness_symbolic.task import SYSTEM_PROMPT  # unchanged, by design

# Word bank: abstract / administrative / botanical vocabulary chosen to
# avoid collisions with task-relevant tokens (no colors, no shapes, no
# spatial prepositions beyond unavoidable function words, no numerals).
_BANK = (
    "archive ledger meadow harvest treaty chorus lantern orchard whisper "
    "granite tide festival parchment ballad meridian ember thicket sonata "
    "quorum herald lattice tapestry monsoon garland anthem foothill saga "
    "harbor almanac prairie vestibule chronicle furlough tundra madrigal "
    "eaves census pilgrim foliage dynasty tributary hearth mosaic gale "
    "prologue creek atlas hymn ridge fable garrison lily grove chapel "
    "envoy tavern scroll dune elm summit rill glen reef fjord moor heath"
).split()

_CONNECTORS = ["while", "although", "because", "and", "yet", "so", "as"]


def make_filler(seed: int, n_words: int) -> str:
    """Deterministic pseudo-prose of ~n_words words from the bank."""
    rng = random.Random(0xF1D0 ^ seed)
    words: list[str] = []
    while len(words) < n_words:
        sent_len = rng.randint(8, 14)
        sent = [rng.choice(_BANK) for _ in range(sent_len)]
        if sent_len > 9:
            sent.insert(rng.randint(2, 6), rng.choice(_CONNECTORS))
        sent[0] = sent[0].capitalize()
        words.extend(sent)
        words[-1] = words[-1] + "."
    return " ".join(words[:n_words]).rstrip(",") + "."


def _padded_observation_factory(seed: int, pad_words: int):
    filler = make_filler(seed, pad_words)

    async def _padded_symbolic_observation(engine, params, prev_shot: Optional[Path]):
        out = unwrap(await engine.python_exec(QUERY_STATE_CODE))
        state = parse_state(out)
        state = annotate_with_colors(state, params)
        parts = [
            {"type": "text", "text": "Auxiliary context:\n" + filler},
            {"type": "text",
             "text": "Current symbolic state:\n```json\n"
                     + json.dumps(state, indent=2) + "\n```"},
        ]
        return parts, [], {"channel": "symbolic_padded",
                           "pad_words": pad_words, "state": state}

    return _padded_symbolic_observation


async def run(engine, seed, vlm, logger, *, max_rounds: int = 8,
              pad_words: int = 750) -> EpisodeResult:
    return await run_episode(
        engine, seed, vlm, logger,
        harness_id=f"sympad{pad_words}",
        system_prompt=SYSTEM_PROMPT,
        observation_fn=_padded_observation_factory(seed, pad_words),
        package=__package__,
        max_rounds=max_rounds,
    )


__all__ = ["run", "EpisodeResult", "make_filler"]
