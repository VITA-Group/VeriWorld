# SurfaceBilliards

Bounce a ball launched from fixed point A over a seeded gaussian
terrain into the red target crater at B. The agent picks only two
scalars per round — **`v_angle`** (horizontal aim, radians) and
**`v_speed`** (cm/s) — not full code; the harness substitutes those
into a fixed shot template and runs the simulation in UE. A Slang
compute shader (`bouncy_ball.slang`) provides deterministic physics;
a log file flags `PASS` / `FAIL`.

**Super-category**: computational / feedback (parameter-only action space)
**Build required**: `PackagedOutput_dev` (see [docs/ACCESS.md](../../../../../docs/ACCESS.md))

## Run

The harness launches and relaunches UE itself (per round, for clean
CUDA/shader state). Pass the executable path instead of pre-launching.

```
python -m veriworld.benchmark.computational.feedback.surface_billiards \
  --seed 0 \
  --exe "C:/.../PackagedOutput_dev/Windows/demo1.exe" \
  --port 9003 \
  --max-rounds 5
```

## Conditions

| Key              | Default | Effect |
|------------------|---------|--------|
| `max_rounds`     | `5`     | Submission rounds after the observation round |
| `n_frames`       | `6`     | Frames extracted from each video for the grid |
| `settle_timeout` | `60`    | Seconds to wait for `PASS`/`FAIL` log before timing out |

## Agent response format

The task prompts for four blocks. Only the last two are parsed; the
first two are preserved verbatim for post-hoc audit.

```
observation: <what you saw in the frames>
knowledge:   <accumulated understanding of A/B, ranges, best params>
v_angle:     <number, radians>
v_speed:     <number, cm/s>
```

Line regex is tolerant to whitespace and sign/decimal forms. Everything
else in the response (the model's free-form reasoning) is ignored.

## Files

| File | Purpose |
|------|---------|
| `task.py`              | Per-round loop, UE restart, frame extraction, log polling, response parsing. |
| `generate_params.py`   | Seeded terrain + A/B position generator. |
| `setup_observe.py`     | UE-side: scene build for Round 0 observation (no ball motion). |
| `setup_shot.py`        | Harness-internal shot template; substitute `{{V_ANGLE}}` / `{{V_SPEED}}` and exec. Includes A marker + A/B text labels. |
| `task.md`              | Agent-facing task description + response format. |
| `lean_verify/bouncy_ball.slang` | CUDA shader that simulates the ball — the ground-truth physics. |
| `lean_verify/solvability_check.py` | Simulation-based sweep that certifies each seed has a reachable shot. |

## Why parameter-only (not full-code)

Per the family distinction in the top-level README: `computational/feedback/`
= scalar parameter space, `computational/coding/` = full Python. Here
the only useful degrees of freedom are `(v_angle, v_speed)`; scene
setup is harness-controlled boilerplate. Asking the agent to re-emit
the setup code every round wastes tokens and risks losing visual
elements (markers, labels) between rounds.

## External dependencies

- `ffmpeg` and `ffprobe` on `$PATH`.
- Pillow (installed by `pip install -e .`).
