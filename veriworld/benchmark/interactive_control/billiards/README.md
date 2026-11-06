# billiards — task

Multi-round Chaos-physics pool game. Each round the agent picks an
(angle, speed) cue shot; the engine runs it, observes the result,
and feeds it into the next round's prompt. Score = number of
coloured balls pocketed within `max_rounds`. Cue ball into a
pocket → respawn at table centre, episode continues.

Uses **`InteractiveEngine`** (one persistent UE per seed). The
scene is built once at Round 0 and balls remain in their settled
positions across all rounds — what the agent sees as the end of
round N IS the start of round N+1.

Sibling harnesses share scene + action; they differ in
**how much info — visual and structured — the agent receives
each round**.

## Active harnesses

| Harness | Visual feedback | Structured ground truth | VLM calls/round |
|---|---|---|---|
| [`harness_image/`](harness_image/README.md) | settled PNG | (none) | 1 |
| [`harness_image_aimline_confirm/`](harness_image_aimline_confirm/README.md) | settled PNG **+ aim-line previews** | (none) | 1 + up to N |
| [`harness_visionposition/`](harness_visionposition/README.md) | settled PNG | **agent-queried** ball positions (request specific ball IDs; result fed back **next** round) | 1 |
| [`harness_groundtruth/`](harness_groundtruth/README.md) | **(no image)** | positions + every static physics param + pocket centres | 1 |
| [`harness_visiongroundtruth/`](harness_visiongroundtruth/README.md) | settled PNG | positions + every static physics param + pocket centres | 1 |
| [`harness_aimline_confirm/`](harness_aimline_confirm/README.md) | settled PNG **+ aim-line previews** (red beam from cue at proposed angle) | positions + every static physics param + pocket centres | 1 + up to N |

### Aim-line preview ablation (2×2)

The two `_aimline_confirm` harnesses cross with their non-aim-line
counterparts to isolate the contribution of the visual-confirmation
REPL under each information regime:

|                       | **No aim-line REPL**    | **With aim-line REPL**          |
|-----------------------|--------------------------|----------------------------------|
| **Vision only**       | `harness_image`         | `harness_image_aimline_confirm`  |
| **Vision + full GT**  | `harness_visiongroundtruth` | `harness_aimline_confirm`     |

- **Row diff** (`*_aimline_confirm` − base) = effect of the visual
  feedback loop under each base modality.
- **Column diff** (`harness_visiongroundtruth*` − `harness_image*`)
  = effect of structured GT push under each REPL setting.

The four active harnesses form a clean 2×2 ablation:

|                       | **No image**         | **Image**                  |
|-----------------------|----------------------|----------------------------|
| **No structured GT**  | (trivial baseline)   | `harness_image`            |
| **Full structured GT**| `harness_groundtruth`| `harness_visiongroundtruth`|

- `groundtruth − image` = structured-info contribution
- `visiongroundtruth − groundtruth` = vision contribution **on top of** complete numbers
- `harness_visionposition` adds the active-query axis on top of `image`

## Archived harnesses

Code retained in the tree (under each subfolder) but removed from
active rotation in `run_defaults.json` and from large-scale batches:

| Harness | Why archived |
|---|---|
| [`harness_video/`](harness_video/README.md) | mp4 vs PNG is a separate visual-modality axis; not load-bearing for the structured-info story currently being measured. |
| [`harness_visionposition_doublecall/`](harness_visionposition_doublecall/README.md) | 2× VLM cost; intent-string handoff between Call 1 (vision) and Call 2 (numbers) loses cross-modal verification; empirically lost to single-call siblings. |
| [`harness_querytool/`](harness_querytool/README.md) | `simulate_shot` REPL is interesting in principle but expensive (1+N calls/round) and unstable (context bloat → empty VLM responses). Useful as a design exploration; not ready for batch comparisons. |

Same model + same seed across the four active harnesses isolates
the contribution of each information channel.

## Difficulty presets

A single `--difficulty` flag (passed to `run_parallel.py`) switches
between two scene presets:

- `standard` — 800×800 square table, **5 balls** in a 1+2+2
  triangle. Original tuning; suits 5-round episodes.
- `hard` — 1150×800 rectangular table, **10 balls** in a complete
  1+2+3+4 triangle. Settle thresholds are bumped automatically
  (longer collision chains); needs `--max-rounds 10` to be useful.

Standard preset is byte-identical to the pre-difficulty version
(verified seed-0 SHA), so existing standard-mode runs reproduce.

## Running on a fresh machine

See [`RUNBOOK.md`](RUNBOOK.md) in this directory for a copy-paste
launch sequence covering all 4 active harnesses × 12 models × any
seed range, on either difficulty.

Scene code adapted from
`VeriWorld-benchmark/.../navigation/debug/chaos/billiards/01_billiards_basic.py`.
