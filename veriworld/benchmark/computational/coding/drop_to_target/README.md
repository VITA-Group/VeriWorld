# DropToTarget

Deform a GPU cloth surface so a ball rolls off and lands inside a red target circle on the ground. The agent writes the **complete Python script** per round (surface shape + ball spawn + material + camera + tick sync). A Slang compute shader simulates ball dynamics deterministically; a log file flags `PASS`/`FAIL`.

**Super-category**: computational / coding
**Build required**: `PackagedOutput_dev` (see [docs/ACCESS.md](../../../../../docs/ACCESS.md))

## Ablations

| Folder | Condition | What the agent sees |
|--------|-----------|---------------------|
| [`visual/`](visual/) | Pure video feedback | Frame grid of the previous round. **No** numerical target coordinates — direction and distance must be estimated from rendered frames. |

Coming later: `text/` (target coordinates given in the prompt) for an ablation baseline.

## Shared assets

| File | Purpose |
|------|---------|
| `generate_params.py` | Seeded surface height + target position + radius. |
| `setup_observe.py` | Round 0 UE setup: flat surface, ball at centre, red disc on ground. |
| `task.md` / `api.md` | Agent-facing instructions (injected into each ablation's prompt). |
| `example.py` | Sine-wave template script shown to the agent — same API, wrong shape. |
| `lean_verify/slide_ball.slang` | Compute shader — 3D nearest-point surface constraint, gravity, friction. |
| `lean_verify/DropToTarget.lean` | Lean 4 proof of the pass/fail criterion. |
| `lean_verify/ground_truth.py` | Python mirror of the Lean oracle — useful for regression testing. |
| `lean_verify/run_verify.py` | Standalone verify runner. |

## External dependencies

- `ffmpeg` + `ffprobe` on `$PATH` (frame extraction).
- Pillow (installed by `pip install -e .`).
