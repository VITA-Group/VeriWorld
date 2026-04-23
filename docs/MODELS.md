# Model Selection Notes

Experience from smoke-testing VLMs for VeriWorld's two task categories. These numbers are for a specific OpenAI-compatible gateway; your endpoint may differ, but the **methodology** and the **relative ranking** within model families are usually transferable.

## TL;DR

- **Interactive tasks** (per-tick loop, 30+ steps): pick models with **consistent 20–30 s/step** latency under concurrent load. One slow model in a 6-way parallel batch stalls the whole batch — its slowest step sets the wall-clock.
- **Computational tasks** (per-round submission, ~5 rounds): slower/reasoning-heavy models are fine here — per-episode latency is bounded by a small round count, so 60–220 s/step is tolerable.
- **Never probe with a single sample.** Single-sample probes can be 3–5× faster than the latency you'll actually see under batch load, because gateways queue concurrent calls per backend "distributor".

## How we tested

- Fire **5 replicas per model concurrently** via `ThreadPoolExecutor`.
- Use a real 640 × 480 screenshot (≈ 1 MB PNG base64) as the image payload — tiny placeholder PNGs trigger "image too small" errors on many VLMs and give misleading results.
- Use a maze-style prompt (~400-token system + ~300-token user) that approximates what VeriWorld actually sends in-task.
- `max_tokens = 16384` to avoid reasoning-token starvation (see "Gotchas" below).

## Interactive-cluster recommendations

Confirmed on the sponsored gateway, 2026-04-19. Median latency from 5-replica concurrent probes with realistic prompts. Tier-A = consistent ≤30 s; Tier-B = borderline with variance; Tier-C = too slow / too unstable for interactive batches.

| Model                         | Vendor     | Median  | Range    | Notes |
|-------------------------------|------------|---------|----------|-------|
| `qwen-vl-max`                 | Alibaba    | 12 s    | 10–25 s  | Fastest consistently |
| `gemini-3-flash`              | Google     | 16 s    | 15–25 s  | Reasoning-hidden but fast |
| `moonshot-v1-32k-vision-preview` | Moonshot | 15 s    | 15–30 s  | |
| `gpt-4o`                      | OpenAI     | 17 s    | 15–25 s  | |
| `grok-4-1-fast-non-reasoning` | xAI        | 19 s    | 17–26 s  | Stable under concurrency |
| `qwen3-vl-235b-a22b-instruct` | Alibaba    | 18 s    | 17–28 s  | |
| `gpt-4.1`                     | OpenAI     | 21 s    | 20–30 s  | |
| `gpt-5.2` **+ `reasoning_effort=minimal`** | OpenAI | 22 s | 18–24 s | Fast without opt-out ~75 s+ |
| `gemini-3-pro-preview-thinking` | Google   | 28 s    | 25–35 s  | Reasoning on but fast |
| `claude-sonnet-4-6`           | Anthropic  | 24 s    | 20–30 s  | |
| `doubao-seed-1-6-flash-250615` | ByteDance | 26 s    | 19–42 s  | Variance high, but this is the most stable Doubao variant we found |

## Computational-cluster recommendations

Latency per step is too high for a 30-step interactive episode, but fine for a 5-round computational task (e.g. SurfaceBilliards). Flag `parallel: false` in `model_configs.json` or run these with an explicit `--models` filter in a dedicated batch.

| Model                              | Vendor    | Latency        | Notes |
|------------------------------------|-----------|----------------|-------|
| `grok-4-1-fast-reasoning`          | xAI       | ~106 s/step    | Reasoning on; decent results |
| `Doubao-Seed-1.6`                  | ByteDance | ~114 s/step    | Heavy reasoning |
| `gpt-5`                            | OpenAI    | ~74 s/step avg | Older GPT-5 snapshot; superseded by `gpt-5.2 + re=minimal` for interactive |
| `claude-opus-4-5-20251101`         | Anthropic | ~220 s/step    | Slowest tested; best reserved for computational / single-shot |
| `claude-opus-4-5-20251101-thinking`| Anthropic | ~117 s probe   | Thinking variant; run standalone |

## Not usable via this gateway (as of 2026-04-19)

| Model                              | Why |
|------------------------------------|-----|
| `gpt-5.2-pro`                      | `400` — "does not support endpoint: chat/completions". Pro-tier models route via OpenAI's `/v1/responses` API; this gateway only exposes `/chat/completions`. |
| `gpt-5-thinking`                   | `503` — "training in progress" |
| `claude-opus-4-7`                  | `400` — internal server error on every retry |
| `gemini-3-pro-preview` (non-thinking) | `503` — "no A-type distributor available". The `-thinking` variant *does* work. |
| `doubao-seed-2-0-pro` / `-lite`    | `404` — not accessible to this sponsored key |
| `doubao-seed-1-6-flash-250715`     | `503` — distributor unavailable (newer `-250828` and older `-250615` work) |
| `doubao-seed-1-6-thinking-250715`  | `404` — not accessible |
| `doubao-1-5-pro-32k`               | `503` — "training in progress" |
| `grok-4.20-multi-agent-beta-0309`  | `400` — "Multi Agent requests are not allowed on chat completions" |

Your mileage may vary: gateway distributor config changes over time. The list above is a snapshot; always probe before committing to a model for a new experiment.

## Gotchas we hit (and their fixes)

### 1. `reasoning_effort` is mandatory for fast GPT-5

`gpt-5` / `gpt-5.2` / `gpt-5-thinking` default to full reasoning, which eats 200+ tokens and 60–100 s before producing any visible output. In VeriWorld we don't need the full reasoning budget on every tick:

```json
{
  "name": "gpt-5.2",
  "model": "gpt-5.2",
  "base_url": "http://…/v1",
  "api_key": "…",
  "extra_params": {"reasoning_effort": "minimal"}
}
```

Supported in VeriWorld via `ModelConfig.extra_params` (see `veriworld/common/vlm.py`). Dropping `reasoning_effort=minimal` returns `gpt-5.2` to ~100 s/step — three-fold slowdown.

`reasoning_effort=low` is a middle ground (~28 s median) when you want some reasoning but not full.

### 2. Reasoning models need `max_tokens ≥ 2000`

If you pass `max_tokens=50` or `max_tokens=200` to a thinking model, all the budget goes to *invisible* reasoning tokens and the response `content` field is an empty string — no error, just empty. Gateway-reported `completion_tokens` equals `max_tokens`, `reasoning_tokens` equals `completion_tokens`, and `content` is `""`. Always pass ≥ 2000 (VeriWorld uses 16384) when the model might do reasoning.

### 3. Single-sample probes lie

A model that runs 15 s on one serial probe can run 60–100 s under 6-way concurrent load because of gateway-side queueing per distributor. Always probe N ≥ 5 concurrent replicas against the gateway before declaring a model "fast". See `doubao-seed-1-6-flash-250828` in our notes: 15.7 s serial probe, 60 s/step in the actual maze batch.

### 4. Concurrency ceiling can't be negotiated

The Apifox documentation for this gateway exposes no parameter, header, or model-name convention to request higher concurrency, priority, or a specific distributor. Observed error messages mention "A-type distributor" and `pa/` model-name prefixes, but these are internal routing hints not user-tunable. **If a model is slow under concurrency, you cannot speed it up — only swap.**

### 5. Slow Opus is *very* slow

`claude-opus-4-5-20251101` took 220 s per step in the maze smoke (mean of 3 steps: 229, 84, 222). On a 30-step full run that's ~110 minutes for a single agent. Unless you're deliberately studying Opus, relegate it to computational tasks (`surface_billiards` runs 5 rounds, so ~20 min total — tolerable).

## Recommended starting-point config

See the committed `model_configs.example.json` for a template. For reproducing VeriWorld benchmark numbers with 11 vendor-diverse models in the interactive cluster:

- `qwen-vl-max` (Alibaba)
- `qwen3-vl-235b-a22b-instruct` (Alibaba)
- `gemini-3-flash` (Google)
- `gemini-3-pro-preview-thinking` (Google)
- `gpt-4o` (OpenAI)
- `gpt-4.1` (OpenAI)
- `gpt-5.2` with `reasoning_effort=minimal` (OpenAI)
- `claude-sonnet-4-6` (Anthropic)
- `grok-4-1-fast-non-reasoning` (xAI)
- `moonshot-v1-32k-vision-preview` (Moonshot)
- `doubao-seed-1-6-flash-250615` (ByteDance)

Covers **7 major vendors** with consistent 15–30 s/step latency — fair-comparison parallel runs at `max_instances=6` complete in ~10–15 min per seed for a 30-step maze.

For the computational cluster, add `claude-opus-4-5-20251101`, `gpt-5` (the old non-`.2` snapshot), and `Doubao-Seed-1.6` as separate entries with `parallel: false`, invoked via `--models <name>` when you want to include them in a dedicated slow batch.

## Related reading

- `veriworld/common/vlm.py` — `ModelConfig.extra_params` threading, key rotation, Anthropic native protocol fallback.
- `veriworld/scripts/run_parallel.py` — fair-comparison orchestrator, batch-sizing math (`batch = ⌊max_instances / N_models⌋ × N_models`).
- `example_starters/README.md` — how to invoke batches with selected models.
