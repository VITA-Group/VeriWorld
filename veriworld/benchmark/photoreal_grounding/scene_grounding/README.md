# SceneGrounding: recover a target camera view

The model receives a target screenshot and navigates a photoreal scene to
recover the target camera pose. Each round provides the current screenshot,
current camera pose and the previous action's measured movement. The target
pose is retained for verification and is not disclosed to the model.

## Scenes and instance generation

The released `groundtruth.json` contains two scenes: `living_room` and `aiue`.
It specifies their Unreal level paths, target anchors and tolerances.
`view_seed_for(level, seed)` computes SHA-256 of the UTF-8 string
`level|seed`, takes its first four bytes as a big-endian integer, and masks
the result with `0x7FFFFFFF`. This is stable across Python processes.

Both harnesses use `sample_walkable_target_and_spawn` to generate the target
and starting poses, including native movement checks. Record the runtime
build, scene, seed and sampled poses when comparing runs.

## Action interfaces

`harness_walk` accepts one JSON object per round:

```json
{"cmd": "walk", "distance": 200}
```

Other commands are `{"cmd":"set_view","yaw":90,"pitch":-5}` and
`{"cmd":"stop"}`. Distances are in Unreal centimetres and angles in degrees.
Movement is collision-aware; `set_view` changes absolute orientation.

The legacy module name `harness_teleport` is retained for compatibility.
Its current interface uses collision-aware `forward` and `backward` moves,
relative `turn` commands, absolute `look` pitch, and `stop`:

```json
{"cmd": "forward", "distance": 200}
```

Other examples are `{"cmd":"backward","distance":100}`,
`{"cmd":"turn","degrees":45}`, `{"cmd":"look","pitch":-10}` and
`{"cmd":"stop"}`. This interface does not accept a `set_pose` action.

## Verification

The supplied configuration sets 15 rounds, a 100 cm XY tolerance, a
10 degree yaw tolerance and a 10 degree pitch tolerance. `harness_walk`
checks XY distance and yaw. `harness_teleport` checks XY distance, yaw and
pitch. Report these harnesses separately with their actual verifier settings;
their comparison does not isolate the action vocabulary alone.

The harness checks tolerance after movement and can end an episode early.
A model's `stop` command ends interaction, but success still comes from the
pose verifier. Logs retain target and final poses, continuous pose errors,
responses, actions, screenshots and the final verdict.

## Run

Supply a model configuration and the path to the packaged executable.
`--no-cleanup` preserves other running instances; use a distinct port and
output directory for every concurrent run.

```bash
python -m veriworld.benchmark.photoreal_grounding.scene_grounding.harness_walk --build /path/to/demo1 --configs /path/to/model_configs.json --model YOUR_MODEL --level living_room --seed 0 --max-rounds 15 --port 19700 --output-root /path/to/results/walk --no-cleanup
```

For the relative-movement interface, replace `harness_walk` with
`harness_teleport`, and choose another port and output directory.
Run the module with `--help` to inspect available options.
