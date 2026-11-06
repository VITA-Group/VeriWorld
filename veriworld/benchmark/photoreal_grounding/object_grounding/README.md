# ObjectGrounding: iterative 3D localization

The model locates a named target in a photoreal scene by placing and refining
3D markers. Each round returns the rendered marker positions. A deterministic
verifier measures the selected marker's distance from the target. The task
configuration and final summaries record the target definition and tolerance.

## Paper evaluation modules

| Marker family | V arm | C arm |
|---|---|---|
| Multiple markers | `harness_grid_iterate_offcenter` | `harness_grid_iterate_offcenter_bboxv2` |
| Single marker | `harness_single_iterate_offcenter` | `harness_single_iterate_offcenter_bboxv2` |

These are iterative tasks, not the separate one-call colour-matching probe.
The paper completion panel uses ten target tasks, three seeds, both marker
families and both arms, with ten rounds per episode. Task IDs and scene
parameters are stored in `groundtruth.json`.

The off-centre variants generate a seeded view aimed near the target. Both
arms provide the camera and marker context specified in their prompts. The
`bboxv2` modules change the rendered coordinate-label presentation. They do
not expose the target's ground-truth coordinates. Keep the module name and
prompt snapshot with every result rather than treating the arm name alone
as a complete description of its inputs.

## Actions and marker IDs

At round one the model chooses a grid `anchor_idx`, then places markers with
`dx`, `dy` and `dz` offsets in Unreal centimetres. Later actions refine
markers using their persistent IDs. `closest_idx` selects the marker used
by the final verifier.

In multiple-marker mode, omitted IDs default to list positions starting at
zero. In single-marker mode, the marker inherits the chosen grid anchor's
ID. Use the ID actually shown in feedback in later rounds.

The grid action parser accepts standard JSON and the `//` line comments
shown in its prompt examples. It removes comments only outside quoted
strings. Other malformed JSON is rejected and logged as an invalid action.

## Run

Supply a model configuration and the packaged executable. The following
uses the multiple-marker V arm:

```bash
python -m veriworld.benchmark.photoreal_grounding.object_grounding.harness_grid_iterate_offcenter --build /path/to/demo1 --configs /path/to/model_configs.json --model YOUR_MODEL --task find_wooden_spoon_pot_view --seed 0 --max-rounds 10 --random-view --port 19700 --output-root /path/to/results/object --no-cleanup
```

Choose another module from the table for the other arms. Use distinct ports
and output directories for concurrent instances. `--no-cleanup` prevents
the CLI from terminating other running Windows instances. Check each
module's `--help` for its options.

## Retained results

Each episode stores its configuration, source snapshots, model responses,
parsed actions, rendered observations and `episode_summary.json`. The
summary includes the selected marker, target distance and PASS/FAIL verdict.
Compare models using the same task, seed, module, input settings and budget.
