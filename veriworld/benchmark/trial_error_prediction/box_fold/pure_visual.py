"""BoxFold pure-visual ("pure V") condition (release).

Runs the unchanged VISION harness (``harness_vision``, the paper V arm) but
rewrites each outbound round so the model receives only the screenshot and the
allowed hinge-ID vocabulary. Relative to the paper V arm this removes, from the
system prompt: the unfolding-shape name, the flat-layout table, the hinge table
(parent/child/edge), the edge-to-axis rules, the cube side S, the target centre
and the numeric tolerance; and, from the per-round user text, the native engine
echo " — FOLDED hinge_id=... angle=... subtree=[...]" (the subtree face indices
are native topology). Kept verbatim: the screenshot, the task and fold semantics,
the action format and degree unit, the +90/-90 ambiguity sentence, the round
counter, the model's own last-command echo, command-validation errors, and the
15-round budget. A line "Allowed hinge IDs: 0, 1, 2, 3, 4" is appended as action
vocabulary only (the harness itself validates hinge_id in [0, 4]).

CONFOUND (must accompany every result): hinge IDs are not identifiable from the
render — the scene shows six unlabelled coloured slabs, the prompt maps no colour
to a face or hinge, and on some seeds two faces are near-identical in colour. A
model can only learn what a hinge does by folding and watching, so this control
mixes hinge-ID grounding by trial with reading task information from pixels.

The scene, physics, rendering, action parser and verifier are the unchanged
scientific source. Derived byte-for-byte from the 2026-11-06 experiment transform
(boxfold_runtime/pure_boxfold_input.py); the only change is a generalised round
regex so the control also runs under --smoke (one round). The transform is a pure
function of the outbound messages and never forwards native geometry.
"""
import copy, hashlib, json, re
from pathlib import Path

CONDITION = 'pure_visual'
TASK = Path(__file__).resolve().parent / 'harness_vision' / 'task.py'
# A cube net is 6 faces / 5 spanning-tree hinges; the harness accepts hinge_id in [0, 4].
DEFAULT_N_HINGES = 5

SYSTEM_PROMPT = """\
You are an agent folding a flat cube net back into a closed cube.
The scene shows 6 thin slab faces lying flat on the ground.

Each round you receive a SCREENSHOT and the list of allowed hinge IDs.
The hinge IDs are action vocabulary only: no ID is identified with a
face, an edge, a parent/child relation or a position in the image.
No net layout, hinge table, face size, target position or numeric
tolerance is supplied. Use the images and the effects of your folds.

A fold rotates the CHILD face AND every face attached to its
subtree as a rigid unit (so if face A is hinged to B, and B is
hinged to C, folding A->B also moves C with B).

ACTION FORMAT — emit ONE JSON code fence per round:

```json
{"cmd": "fold", "hinge_id": 2, "angle": 90}
{"cmd": "stop"}
```

The example values are syntax only. `angle` is in degrees. Folds are
around the parent face's CURRENT local frame; +90 / -90 either lifts
the child up or pushes it down depending on which side of the parent
the child sits — that's the puzzle.

GOAL: after your folds, all 6 faces should sit at the 6 face-
centers of a closed cube.

PASS condition: every face center within the task tolerance of its
canonical closed-cube position.

You have 15 rounds total. Emit `stop` once the cube looks
closed.
"""

ECHO = re.compile(r' — FOLDED hinge_id=-?\d+ angle=-?[0-9.eE+-]+ subtree=\[[0-9, ]*\]')
# Generalised from the experiment's hard-coded "/15" so the control also runs
# under --smoke; the model-visible bytes are unchanged (this only matches/validates).
HEAD = re.compile(r'\*\*Round (\d+)/(\d+)\*\*\n\nImage: current state of the cube net\. Emit ONE fold \(or stop\)\.\n')


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def original_parser(text):
    """Identical action extraction imported from the unchanged scientific source."""
    from veriworld.benchmark.trial_error_prediction.box_fold._shared import extract_action
    return extract_action(text)


def transform(messages, n_hinges, call_index):
    assert isinstance(messages, list) and len(messages) == 2
    system, user = messages
    assert system['role'] == 'system' and system['content'].startswith('You are an agent folding a flat cube net')
    assert 'Hinges in this seed' in system['content'] and 'Flat layout' in system['content']
    assert user['role'] == 'user' and isinstance(user['content'], list) and len(user['content']) == 2
    image, text = user['content']
    assert image['type'] == 'image_url' and image['image_url']['url'].startswith('data:image/png;base64,')
    assert text['type'] == 'text'
    m = HEAD.match(text['text']); assert m and int(m.group(1)) == call_index, 'unexpected round text'
    new_text, n_echo = ECHO.subn('', text['text'])
    assert n_echo <= 1 and 'subtree' not in new_text and 'FOLDED' not in new_text
    new_text += '\nAllowed hinge IDs: ' + ', '.join(str(i) for i in range(n_hinges))
    out = [{'role': 'system', 'content': SYSTEM_PROMPT},
           {'role': 'user', 'content': [copy.deepcopy(image), {'type': 'text', 'text': new_text}]}]
    proof = dict(condition=CONDITION, source_arm='harness_vision', call_index=call_index, native_echo_removed=n_echo,
                 source_messages_sha256=hashlib.sha256(encode(messages)).hexdigest(),
                 sent_messages_sha256=hashlib.sha256(encode(out)).hexdigest(),
                 image_unchanged=out[1]['content'][0] == image, allowed_hinge_ids=list(range(n_hinges)),
                 confound='hinge IDs not identifiable from the render; near-identical face colours on some seeds')
    return out, proof


class PureVisualOutbound:
    """Stateful per-episode wrapper matching the worker's apply/observe_reply
    interface: strip the native geometry/echo from each round's V messages and
    track the round index (the model's own last-command echo is already carried
    in the source user text, so no reply state is needed)."""

    def __init__(self, n_hinges=DEFAULT_N_HINGES):
        self.n_hinges = n_hinges
        self.call_index = 0

    def apply(self, messages):
        self.call_index += 1
        return transform(messages, self.n_hinges, self.call_index)

    def observe_reply(self, response_text):
        return None


__all__ = ["CONDITION", "SYSTEM_PROMPT", "transform", "original_parser", "PureVisualOutbound", "DEFAULT_N_HINGES"]
