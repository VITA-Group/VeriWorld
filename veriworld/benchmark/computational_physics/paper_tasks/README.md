# Paper computational-physics tasks

This entry point runs the paper's 80-by-80 curved-surface ball task and the
surface-design task. The older `curvy_billiards` bounce-shot variant remains
available separately. This directory contains the original physics kernels,
scene setup, instance parameters, and executable verification logic.

Requirements: the packaged VeriWorld runtime, the repository's Python
dependencies, and `ffmpeg` / `ffprobe` on `PATH`. Linux requires an NVIDIA GPU
with Vulkan graphics support and the runtime's video encoder dependencies.

Set an API credential in your environment and reference it from a model config:

```json
[
  {
    "name": "my-model",
    "model": "MODEL_ID",
    "base_url": "https://YOUR_PROVIDER/v1",
    "api_key": "env:VERIWORLD_API_KEY"
  }
]
```

From the repository root:

```bash
python -m veriworld.benchmark.computational_physics.paper_tasks \
  --build /path/to/demo1-Linux-Shipping \
  --config model_configs.local.json --model my-model \
  --task drop --seed 0 --condition C --max-rounds 5 \
  --port 19600 --output /path/to/new/episode
```

On Windows, supply the packaged `demo1-Win64-Shipping.exe`. The output directory
must be new. Concurrent instances need distinct ports and output directories.

Supported reference-validated instances:

| Task | `--task` | Seeds | Model action |
| --- | --- | --- | --- |
| CurvyBilliards | `curvy` | 0, 2, 9 | Launch angle in radians and speed in cm/s |
| DropToTarget | `drop` | 0, 1, 2 | Python `surface_height(x, y)` function |

V receives rendered observations. S receives structured scene parameters and
trajectory feedback. C receives both. Actions, instances, simulation budgets,
and verification rules are shared across the three conditions. Reference
actions are used only in the integration checks and are not sent to the model.
All three conditions include the shared setup constants needed to specify an
action. V does not receive the target coordinates or the full surface parameters.
Each episode has at most five action attempts by default. A simulation has a
60-second budget for DropToTarget and a 90-second budget for CurvyBilliards.

## DropToTarget function interface

The action is one `surface_height(x, y)` function in a restricted numeric
Python subset. `math` and `SURFACE_Z` are already provided by the runtime.
An explicit `import math` is also accepted and normalized to this same
provided module. For example:

```python
def surface_height(x, y):
    along = 0.8 * x + 0.6 * y
    return SURFACE_Z - 0.5 * along
```

This example illustrates the interface, not a solution to every instance.
The parser accepts scalar assignments, numeric tuple/list unpacking,
scalar augmented assignments such as `+=`, arithmetic, comparisons, Boolean
expressions, `if` statements, conditional expressions, and `return`.
Available calls are `abs`, `min`, `max`, and the following `math` functions:
`sin`, `cos`, `tan`, `asin`, `acos`, `atan`, `atan2`, `sinh`, `cosh`, `tanh`,
`sqrt`, `exp`, `expm1`, `log`, `log10`, `log2`, `log1p`, `fabs`, `floor`,
`ceil`, `hypot`, `copysign`, `radians`, and `degrees`. The constants `math.pi`,
`math.e`, and `math.tau` are available. Explicit imports of these math names
and aliases are normalized without executing arbitrary import statements.
Powers require a constant exponent with absolute value at most eight.

Other module imports, loops, comprehensions, decorators, recursive calls,
attribute writes, and other function definitions are not part of this interface.
Numeric constants accompanying the function become local bindings and cannot
overwrite the simulation's physics globals. The parser also limits the
function to 500 syntax-tree nodes. Before simulation, the function is
evaluated at every grid point; heights must be finite numbers with absolute
value at most 100,000 cm.

An unsupported submission consumes one action attempt and records
`invalid_action.json`; the next prompt supplies that diagnostic. These
format and language-subset errors are retained separately from executed
actions that miss the target. API or runtime outages are infrastructure
errors and are not scored as completed episodes.

Python Markdown fences are accepted. When a response includes revised code
blocks, the final surface function block is used, preserving the original
harness convention. CurvyBilliards accepts a bare or fenced JSON action, or
one unambiguous complete JSON action accompanying explanatory text. It does
not repair truncated JSON or choose between conflicting actions. Numeric
values and the episode's attempt budget are unchanged by parsing.

Episode summaries record `action_parser_version`. The numeric parser repair
was checked against all 795 retained action replies: all 614 previously
accepted actions preserve their full-grid heights or numeric shot parameters.
Offline replay does not create new scored outcomes or replace historical runs.

## Logs and runtime feedback

The output includes each prompt, complete model response, parsed action,
simulation code, trajectory log, rendered frames, video, and final verifier
result. A failed action receives `FAIL`; missing runtime results and transport
errors raise an exception instead of silently becoming model failures.

Recording starts after the scene and camera are ready and before simulation.
Visual feedback consists of six frames sampled at fixed times after discarding
two queued frames. Sampling does not depend on whether a target remains visible.
If the engine exits or its WebSocket connection closes, the runner replays the
same committed action, with at most three simulation attempts and no new model
call. The failed runtime attempt is retained for diagnosis.

The API response timeout defaults to 600 seconds and can be set with
`--api-timeout`. This timeout does not change the model's action-attempt budget
or the simulation duration.

`PROVENANCE.json` identifies the reference-validated fixtures. The generator
sources are included for extension; validate newly generated instances before
adding them to a scored evaluation.

## Separate parameter variants

`--variant standard` preserves the released instance exactly (the default).
Three explicit alternatives are available: `rotate90` rotates target/start
coordinates and terrain centers 90 degrees about the vertical axis;
`mirror-x` reflects those X coordinates; `wide-target` increases only the
target radius by 25%. These do not change the physics kernel, grid spacing,
model action space or attempt budget. For surface design, the model must
produce an appropriate surface for the changed target.

Use `--variant rotate90`, `--variant mirror-x`, or `--variant wide-target`
with the same CLI above. Each output records its base seed and variant, and
stores the effective parameters in the observation and every action folder.
Base instance files are never edited. Old witnesses are removed from variant
parameters; a transformed reference must pass the native runtime before a
variant is used in a scored study. Variant results and independent repeats
belong to a separate parameter study, not the original paper seed panel.
