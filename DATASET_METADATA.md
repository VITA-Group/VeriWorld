# VeriWorld task dataset metadata

This dataset distributes executable **test-task definitions and initialization
fixtures**, not the authors' complete experiment results or raw model runs.
The versioned task source and Windows/Linux runtime are available from
[GitHub Releases](https://github.com/yanyanzheng96/VeriWorld-release/releases/tag/paper-artifacts-2026-11-03-rc1). Model API access is configured by the user.

## Records and units

| Record set | Rows | Meaning of one row |
| --- | ---: | --- |
| `task_catalog` | 11 | A task-family definition, objective, verifier/metric, and source path |
| `seed_fixtures` | 90 | A reference generator configuration and its initialization parameters |
| `runtime_recipes` | 29 | A representative public CLI command for installation checking |

These counts measure different things and must not be added to count scenes or
experiments. Fixtures cover eight task families; LightWar, SceneGrounding and
ObjectGrounding instead use live scene/runtime configuration in this snapshot.
The task catalogue and runtime recipes cover all eleven families. Fixtures are
reference examples, not an enumeration of every possible generated instance.

A task instance is identified by its task, generator/source version, seed,
generator options, scene and parameter variant as applicable. A model run adds
model identity, information condition, interface and budget. A repeated model
run on the same configuration does not create a new independent scene.
Legacy TunnelNav3D retains construction seed 99 for rendered tunnel waypoints:
changing the CLI seed alone does not produce independent tunnel geometries.

## Execution and verification

Read the source release README to download and verify the binary, install
dependencies and configure a model API. `run_task.py --check` checks setup;
`run_task.py --task TASK --seed SEED --model MODEL --smoke` runs an installation
check. The recipes preserve their task-specific interfaces and options.
Reading CSV metadata does not execute an Unreal Engine task.

Task objectives and verifier/metric definitions are in `task_catalog`.
Generator options and evaluation-side reference quantities are preserved in
`parameters_json`; the published harness determines which information the model
receives. Do not add hidden reference quantities to visual-only prompts.
The pinned task implementation provides the complete action and verifier logic.
DropToTarget accepts a Python `surface_height(x, y)` function and checks the
ball's first ground contact against the target circle. CurvyBilliards accepts
launch parameters `v_angle` and `v_speed`; CUDA/Slang implements simulation.

The released checks exercise representative task/seed/model combinations;
they do not establish exhaustive compatibility with every model or seed.
Runtime or API errors are distinct from valid model FAIL outcomes. API keys
are not included. Later API services can change or become unavailable.

## Provenance and metadata version

The task records retain the `paper-artifacts-2026-11-06-rc3` source-data
provenance. The historical metadata revision `1.1.0` changed descriptions only.
This repository metadata revision `1.1.3` corrects the task source-data provenance to the rc3 task source (the rc1 tag is the separate runtime-binary release) and records the F runtime/distribution
update; it leaves the three CSV record sets and initialization parameters unchanged.
Croissant includes these three task record sets, immutable content URLs,
SHA256 digests, Responsible AI fields, seed sources and generation activities.
The small CSV fixtures can be inspected without downloading the runtime.

Existing score CSVs and `SUPPORTING_DATA.txt` are retained as **separate historical
paper-support files**. They are excluded from this task dataset's Croissant and
viewer configurations, and do not constitute a complete or current run archive.
No model scores or sample counts have been replaced by this metadata update.

## Existing license scopes

This update grants no new rights and changes no existing license terms.
Task fixtures, runtime recipes, code and project material retain the project
license/notices. The historical numeric export retains its previously recorded
CC0 declaration; this includes the original `task_catalog.csv` view. Unreal
Engine and third-party runtime content retain their own terms. Consult the
artifact-specific license notice, rather than treating the entire distribution
as CC0. A metadata validation result does not certify asset redistribution rights.


Runtime update, 2026-11-04: the qualified F binaries are distributed in the paper-artifacts-2026-11-03-rc1 release on yanyanzheng96/VeriWorld-release. This changes the runtime release, not the three task-dataset record sets or their historical collection provenance. Existing CSV/fixture checksums remain unchanged. Linux GPU graphics is unqualified for F.
