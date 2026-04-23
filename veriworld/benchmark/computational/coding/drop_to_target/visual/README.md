# DropToTarget — `visual/` ablation

Pure video feedback. The agent only ever sees the frame grid of the previous round's recording. The prompt does not reveal the numerical target position — the agent must read direction and distance off the rendered scene.

## Run

```
python -m veriworld.benchmark.computational.coding.drop_to_target.visual \
  --seed 0 \
  --exe "C:/.../PackagedOutput_dev/Windows/demo1.exe" \
  --port 9003 \
  --max-rounds 5
```

UE is launched and relaunched by the harness itself (once per round) so that CUDA / shader state is clean.

## Conditions

| Key                | Default | Effect |
|--------------------|---------|--------|
| `max_rounds`       | `5`     | Submission rounds after the observation round. |
| `n_frames`         | `6`     | Frames extracted from each recording for the grid. |
| `settle_timeout`   | `45`    | Seconds to wait for a settled `# RESULT: X` line. First-touch `LANDED_X` is used as a fallback. |
| `observe_seconds`  | `5`     | Round 0 recording length. |

## Output

Per seed / model, the workspace contains:

- `round_00_observe.mp4` + `round_00_frames.png` — what the agent sees on turn 1.
- `round_NN_response.txt`, `round_NN_thought.txt`, `round_NN_code.py`, `round_NN_video.mp4`, `round_NN_frames.png`, `round_NN_log.txt` — one set per submission round.
- `summary.json` — seed, model, per-round `PASS`/`FAIL`, and the full `generate_params` record.

## Why this is its own folder

A text-target ablation (same physics, agent is *told* the coordinates) would change the **information channel** the agent has to reason over, not just a numeric knob — that crosses our per-folder boundary. It would live as a sibling `text/` when added.
