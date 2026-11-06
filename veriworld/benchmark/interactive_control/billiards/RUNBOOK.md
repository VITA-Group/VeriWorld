# billiards — RUNBOOK (running the active 4-harness ablation)

How to launch the four active billiards harnesses on a fresh machine,
varying seeds, difficulty, and models.

---

## What you'll run

**4 active harnesses** (the 2×2 ablation):

| # | Harness module path |
|---|---|
| 1 | `veriworld.benchmark.interactive_control.billiards.harness_image.shot_code` |
| 2 | `veriworld.benchmark.interactive_control.billiards.harness_visionposition.shot_code` |
| 3 | `veriworld.benchmark.interactive_control.billiards.harness_groundtruth.shot_code` |
| 4 | `veriworld.benchmark.interactive_control.billiards.harness_visiongroundtruth.shot_code` |

**Models** — anything with `"parallel": true` in `model_configs.json`.
Currently 11 such models:

```
qwen-vl-max, grok-4-1-fast-non-reasoning, moonshot-v1-32k-vision,
gemini-3-flash, doubao-seed-1-6-flash, gpt-4o, qwen3-vl-235b,
gpt-4.1, gpt-5.2, gemini-3-pro-thinking, claude-sonnet-4-6
```

To run "all 12 models", add one more parallel-enabled entry to
`model_configs.json` then either:
- omit `--models` entirely → orchestrator picks every parallel-enabled model
- explicitly list them with `--models "..."` (comma-separated)

**Difficulty** — single CLI flag `--difficulty {standard|hard}`:

| Preset | Table | Balls | Rack | Recommended `--max-rounds` |
|---|---|---|---|---|
| `standard` | 800×800 square | 5 | 1+2+2 triangle | **5** |
| `hard` | 1150×800 rectangle | 10 | 1+2+3+4 triangle | **10** |

`standard` reproduces the original task byte-for-byte (seed-0 SHA
verified); `hard` adds 2× more balls + a non-square table, with
camera height + settle thresholds auto-bumped to match.

---

## One-time machine setup

1. **UE build at a known path**

   The harnesses launch the dev build (`PackagedOutput_dev`,
   has Chaos physics enabled). Place it at any path; you'll pass
   `--build` to the orchestrator OR set it in `run_defaults.json`.

   Default path used in this repo's `run_defaults.json`:
   ```
   ${UE_BUILD_ROOT}\PackagedOutput_dev\Windows\demo1.exe
   ```
   Edit that key (`builds.interactive`) on the new machine.

2. **Python env**

   ```bash
   pip install -r requirements.txt   # (or whatever your usual env install is)
   ```

3. **`model_configs.json`** with API keys for every model you want to test.
   Skip with `--models <subset>` to use only a subset.

4. **Optional — `run_defaults.json`** holds default kwargs per task path.
   The 4 active harnesses already have entries (`max_rounds: 5`,
   `settle_timeout: 35`); CLI flags override these per-run.

---

## Launch commands

All commands assume you're in the repo root. Replace `<EXE>` with
your actual `demo1.exe` path (or omit `--build` if `run_defaults.json`
has the right path).

### Standard preset (5 balls, 5 rounds)

```bash
EXE="C:/path/to/PackagedOutput_dev/Windows/demo1.exe"

for HARNESS in harness_image harness_visionposition harness_groundtruth harness_visiongroundtruth; do
  python -m veriworld.scripts.run_parallel \
    --task veriworld.benchmark.interactive_control.billiards.${HARNESS}.shot_code \
    --seeds 0-2 \
    --max-rounds 5 \
    --base-port 9003 \
    --max-instances 6 \
    --build "$EXE"
done
```

`--seeds 0-2` runs seeds 0, 1, 2. Use `0-9` for 10 seeds, or
`0,3,7` for an explicit list.

`--max-instances 6` means at most 6 UE instances in parallel.
With 11 models × N seeds, batches of 6 are run sequentially —
each batch finishes its UE teardown before the next starts. Bump
to 12 if you have GPU+RAM headroom for it (each UE shipping
process takes ~3GB resident).

### Hard preset (10 balls, 10 rounds)

```bash
EXE="C:/path/to/PackagedOutput_dev/Windows/demo1.exe"

for HARNESS in harness_image harness_visionposition harness_groundtruth harness_visiongroundtruth; do
  python -m veriworld.scripts.run_parallel \
    --task veriworld.benchmark.interactive_control.billiards.${HARNESS}.shot_code \
    --seeds 0-2 \
    --difficulty hard \
    --max-rounds 10 \
    --base-port 9003 \
    --max-instances 6 \
    --build "$EXE"
done
```

The `--difficulty hard` flag flows through the orchestrator into
each task's `run(...)` kwargs. Settle thresholds for hard are baked
into `params.json` (so the UE side reads them); `settle_timeout`
auto-bumps to 50s inside the harness when difficulty=hard.

### Specific model subset

```bash
--models "gpt-4o,gemini-3-flash,claude-sonnet-4-6"
```

### All parallel-enabled models (omit --models)

```bash
# Orchestrator picks every model with "parallel": true in model_configs.json.
python -m veriworld.scripts.run_parallel \
  --task veriworld.benchmark.interactive_control.billiards.harness_visiongroundtruth.shot_code \
  --seeds 0-4 --difficulty hard --max-rounds 10 --max-instances 6 --build "$EXE"
```

---

## Where results land

```
veriworld/results/interactive_control/billiards/<HARNESS>/shot_code/
  seed_<NNNN>_<TIMESTAMP>/
    orchestrator.json                         # batch metadata
    <model_name>/
      params.json                             # the seed's scene config
      state.json                              # latest dynamic state
      round_NN_prompt.txt
      round_NN_response.txt
      round_NN_thought.txt
      round_NN_shot.json
      round_NN_verify.txt
      round_NN.png                            # post-shot screenshot
      episode_summary.json                    # final score, trajectory
      lean_verify/log_for_verify.txt          # raw UE harvest log
```

`episode_summary.json` is the canonical per-(seed, model, harness)
result. Aggregate across these for plots / tables.

`harness_groundtruth` still writes `round_NN.png` for human inspection
even though the model never sees it (text-only prompt).

---

## Total estimated wall-clock

Single seed, all 4 harnesses, 11 models, hard mode:
- ~60 s per round × 10 rounds × 11 models / 6 instances ≈ **20 min/harness**
- × 4 harnesses ≈ **80 min total** for one seed

Standard mode is roughly half (5 rounds vs 10).

For a paper-grade run (5 seeds × 4 harnesses × 11 models, hard mode):
budget ~6-7 hours, mostly serial across batches.

---

## Troubleshooting

**Stale UE process holding the port**
The orchestrator runs `taskkill /F /IM demo1.exe` before each batch.
On Linux/macOS substitute the equivalent (the orchestrator is
Windows-aware; verify before the first batch on a new platform).

**`FileNotFoundError: ...PackagedOutput\\Windows\\demo1.exe`**
The `run_defaults.json` `builds.interactive` path is wrong for this
machine. Either fix that file or pass `--build "<actual path>"`.

**Hard mode + `--max-rounds 5`**
You'll see most models stuck at 1-3/10. Hard mode is tuned for 10
rounds; below that, scoring resolution is too coarse to compare
models. Always pass `--max-rounds 10` with `--difficulty hard`.

**`--difficulty hard` ignored**
If you see `params.json` with `table_size_x: 800` despite passing
`--difficulty hard`, the harness module doesn't accept the kwarg.
The 4 active harnesses do (verified via `inspect.signature`); other
ablation harnesses in the tree may not.

**WebSocket port collisions**
`--base-port 9003` allocates ports `9003, 9004, 9005, ...` per
parallel UE. If something else is using these, pass a different
`--base-port` or kill the offending process.

**Empty VLM responses (rare)**
The harnesses already have a 3-retry exponential-backoff wrapper for
VLM calls. If a model is chronically broken (3 consecutive timeouts
or 400s), that round is skipped and the agent gets the next round
fresh — it doesn't loop forever and doesn't end the episode.

---

## Reproducing exact results

Each `seed_NNNN_TIMESTAMP/<model>/` directory contains a
`reproduce.bat` and `reproduce.sh` written by `RunLogger.snapshot_model`.
These re-run the exact (seed, model, kwargs, build path) combo against
the same task module — useful when you want to re-grade one cell of
the table on a different machine.
