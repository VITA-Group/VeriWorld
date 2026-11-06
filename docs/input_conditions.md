# Task input conditions

This document describes the information sent to the model by the released
task harnesses. It distinguishes that information from simulator state,
reference solutions and verifier-only data. Use it with the
[task entry-point index](../task_entrypoints.json) and
[seed notes](../TASK_SEED_NOTES.txt).

The audited implementation is commit
[`c1b872426c890c3302d02f3e73fbb3e333a338e9`](https://github.com/VITA-Group/VeriWorld/tree/c1b872426c890c3302d02f3e73fbb3e333a338e9).
Its task implementation files were migrated from the published release
snapshot without changing their scientific contents; see
[migration provenance](../MIGRATION_PROVENANCE.json).
This documentation update does not modify task code, fixtures or scores.

## Meaning of V, S and C

- **V (visual):** rendered observations, action feedback and task-specific
  metadata. V does not mean that every numerical field is withheld.
- **S (structured):** task-specific structured information without rendered
  observations. The fields are specified below; S is not necessarily a
  complete state oracle.
- **C (combined):** rendered observations and the task's structured inputs.
  Read the exact harness name: navigation channels and marker-presentation
  variants are not interchangeable with a standard V/S/C triplet.

All conditions provide the objective, response/action schema and interaction
limits. Prompts and feedback differ where described below. An S-minus-V score
compares performance under those configurations; it is not universally an
error measure for reconstructing geometry from pixels. Likewise, a C-minus-S
gap alone does not identify a unique fusion mechanism. Compare protocols and
budgets explicitly when interpreting action-span effects.

## Inputs for all eleven task families

| Task | Visual observations and metadata | Structured/combined configuration | Interpretation |
| --- | --- | --- | --- |
| PlaceOnBottom | Image; anonymous object IDs, current positions and sizes; goal and placement rule. Per-object colour and shape attributes are withheld. | S includes the positions/sizes plus per-object colour and shape, without images. C includes that listing and the image. | Visible-object-to-ID correspondence and placement using already supplied geometry. |
| BoxFold | Image of the current folded state; flat face layout, hinge tree and dimensions. | S supplies hinge tree, dimensions, current face centres and explicit target face centres, without images. C combines images/layout with these current/target coordinates. | Use of explicit current and target state versus interpretation of the fold. Topology is already given in V. |
| MovingShooter | Replay frame grid; fire-time/yaw/pitch definitions; previous actions and HIT/MISS feedback. No equations. | S provides target, shooter and projectile equations plus action/simulation history. C adds replay frames. The equation frame has an undisclosed rotation relative to world-frame aiming. | Motion interpretation and equation use with a remaining frame-alignment requirement. |
| LightWar | Launch preview and shot frame stacks; launch coordinates, trajectory law, action bounds and tolerance. No target coordinates or scalar miss distance in the V arm. | S provides target apex/ground height and landing/miss/arc-ground telemetry without images. C adds images. V+Telemetry is the separate scalar-miss-distance variant. | Target localization and use of trajectory/terrain feedback. No global terrain height map. |
| Billiards | Settled image; shot API, coordinate conventions, physical-behaviour guidance and pocket/scratch counts. | S provides table/rail/pocket parameters, ball properties and live positions without images. C adds the image. Position-query and aim-line-preview variants are separate. | Use of explicit geometry and physics information; query/preview variants change interaction. |
| MazeNavFPS | Current first-person image and prior blocked/ok outcome, without coordinates, raycast or a harness-maintained position log. | VP adds position/facing and explored-position history. RP has raycasts and position/history without images. VRP adds images to RP. No full maze map or goal coordinates. | Visual navigation, memory support and local range sensing. RP is not VP with only its image removed. |
| TunnelNav3D | Released aim-and-fly: image and blocked/ok outcome. Released VP: image, position/facing and explored-position history. | RP/VRP in the manuscript denote raycasts plus position history without/with images. This release's structured directory exposes VP and aim-and-fly, not all historical RP/VRP entry points. | Sensing, memory and control-protocol comparisons must remain distinct. |
| SceneGrounding | Target reference image, current image, current camera pose and movement/blocked feedback. Target pose is withheld. | No corresponding image-free S condition in the released view-recovery task. | Viewpoint recovery from landmarks with known current pose. |
| ObjectGrounding | Both released V/C-labelled variants have images, target description, camera pose, screen-to-world axis mapping and current marker coordinates. The grid family also supplies an anchor index/colour/coordinate/distance table. Target coordinates are withheld. | `bboxv2` changes label size, orientation and camera offset. It does not newly introduce the coordinate table. No image-free S condition. | Target identification and marker-guided localization under different label presentations. |
| CurvyBilliards | Released rolling-ball task: images, grid geometry, ball radius, friction, fixed start and speed bound. Action: launch angle and speed. | S adds numerical surface/target parameters and rollout logs without images. C includes both. Reference solutions are excluded. | Interpreting surface/target geometry versus receiving explicit parameters. |
| DropToTarget | Released surface-design task: images, grid geometry, ball radius/start and initial surface height. Action: `surface_height(x, y)`. | S adds target coordinates/radius and rollout logs without images. C includes both. All conditions receive verifier verdicts and action-format errors. | Design a surface so the ball's first ground contact is within the target circle; not cloth-parameter recovery. |

## Source entry points and important details

### PlaceOnBottom

Source: [place_on_bottom](../veriworld/benchmark/interactive_control/place_on_bottom/).
The conditions are `harness_vision`, `harness_symbolic` and `harness_combined`.
The visual listing already includes `label`, position and `scale_cm`; the
structured listing adds `rgb` and `mesh`. Attribute-to-object correspondence
is therefore part of the comparison. Do not describe the V arm as withholding
all positions or sizes. Saved prompts, rather than the presence of an
unfiltered `params.json` in the result directory, establish model-visible input.

### BoxFold

Source: [box_fold](../veriworld/benchmark/trial_error_prediction/box_fold/).
The conditions are `harness_vision`, `harness_symbolic` and `harness_combined`.
The visual prompt contains the unfolded layout and hinge tree. Structured
prompts provide current face-centre coordinates and explicit per-face target
coordinates. These targets provide information about the goal, not just a
different rendering of current state. The condition prompts are not identical
text with an image simply switched on or off.

### MovingShooter

Source: [moving_shooter](../veriworld/benchmark/trial_error_prediction/moving_shooter/).
For the portable frame-grid panel use `harness_vision_frames`,
`harness_symbolic` and `harness_combined_frames`. The separate raw-video
`harness_vision`/`harness_combined` modules are distinct transport variants.

[`generate_params.py`](../veriworld/benchmark/trial_error_prediction/moving_shooter/generate_params.py)
samples `frame_rotation_deg`; `equations_text` expresses motion in a rotated
coordinate frame. The rotation value is stored in the instance parameters
but is not explicitly given as a frame transform in the model prompt.
Actions request absolute world-frame yaw. Thus even S has a frame-alignment
requirement. The frame-grid V/C arms include an initial observation replay;
S receives equations and text history. Record which transport was actually
used, including its attached image/video receipts.

### LightWar

Source: [light_war](../veriworld/benchmark/trial_error_prediction/light_war/).
The V arm is `harness_arc_visual_only`; S/C use `harness_symbolic` and
`harness_combined`. The base `harness_arc` scalar-miss-distance configuration
is a separate V+Telemetry variant. `_system_prompt` and the round-message
construction in `harness_arc/task.py` determine visibility.

All arms receive the launch coordinates and trajectory law. Structured
feedback adds the target apex, landing position, miss vector and sampled
terrain heights along executed arcs. It does not disclose the global
heightfield. The `state_first` instruction variants must be kept separate:
changing the strategy instruction is not an image-only intervention.

### Billiards

Source: [billiards](../veriworld/benchmark/interactive_control/billiards/).
The screenshot V/S/C entry points are `harness_image/shot_code`,
`harness_groundtruth/shot_code` and `harness_visiongroundtruth/shot_code`.
Their prompts combine `task.md`, the harness-specific `api.md`, a scoreboard,
an action example and, where applicable, static/live state blocks.

The shared instructions already contain coordinate conventions and some
physical-behaviour guidance, including damping. Structured arms provide an
explicit parameter/state listing; do not describe all physical knowledge as
exclusive to S. `harness_groundtruth` may save screenshots for human review
without attaching them to the model request. Position-query, aim-line preview,
raw-video transport, difficulty and shot-budget variants require separate
identifiers. This inventory does not certify the provenance of every older
aggregate Billiards score.

### MazeNavFPS and TunnelNav3D

Sources: [MazeNavFPS harnesses](../veriworld/benchmark/interactive_control/mazenavfps/harness_structured/)
and [TunnelNav3D harnesses](../veriworld/benchmark/interactive_control/tunnel_nav3d/harness_structured/).
The Maze directory uses `pr_*` module names for the RP channel in manuscript
notation. `v_*`, `vp_*`, `pr_*` and `vrp_*` are different input configurations;
the suffix also specifies action protocol. Pure V omits the harness position
tracker and gives blocked/ok outcomes instead of numerical movement logs.

The released Tunnel structured directory contains `vp_bf` and `af`.
Its aim-and-fly implementation uses visual input without the position log.
Do not infer that historical RP/VRP or every batch/single combination is
implemented by these two entry points. Also follow the seed notes: the legacy
tunnel geometry uses a fixed internal random seed, so distinct CLI seed values
alone do not establish distinct tunnel geometries.

### SceneGrounding and ObjectGrounding

Sources: [scene_grounding](../veriworld/benchmark/photoreal_grounding/scene_grounding/)
and [object_grounding](../veriworld/benchmark/photoreal_grounding/object_grounding/).
SceneGrounding includes the current camera pose in its prompt, but withholds
the target pose. Its target is specified by a reference image.

For ObjectGrounding the released marker pairs are
`harness_grid_iterate_offcenter` / `harness_grid_iterate_offcenter_bboxv2`
and `harness_single_iterate_offcenter` /
`harness_single_iterate_offcenter_bboxv2`. Both members of each pair supply
camera/marker context. The grid pair has the same prompt construction; the
rendering helper changes from `_bbox_markers.py` to `_bbox_markers_v2.py`.
The first helper already creates numeric index and axis labels. The second
makes labels larger, camera-facing and offset toward the camera. It is
incorrect to describe this released pair as adding a previously absent
index-to-coordinate table. Label visibility must be checked in saved images,
not inferred solely from a label-spawning code path. The single-marker family supplies its current marker coordinate instead of
the grid anchor table. Single- and multi-marker protocols are separate.

### CurvyBilliards and DropToTarget

Source: [paper_tasks](../veriworld/benchmark/computational_physics/paper_tasks/),
especially [`task.py`](../veriworld/benchmark/computational_physics/paper_tasks/task.py).
The runner's `common` dictionary defines shared numerical setup.
`clean_state` removes `solution`, `reference_action` and `witness` before
constructing S/C inputs. Additional state is supplied only in S/C; simulation
trajectory logs go to S/C while images go to V/C. Verdicts and action-format
feedback are available to all conditions.

Use this entry point for the rolling-ball launch and surface-design tasks.
Separate legacy CUDA directories are not interchangeable tasks. A task name
does not establish equivalence of their action formats, verifiers or prompts.
Likewise, documentation for the released runner does not prove which source
version produced an older paper table. Keep per-run provenance and distinguish
feedback sampling and action-parser versions. Do not relabel old results as
having used the latest parser or feedback policy.

## Reproduction records

Retain the exact module and source snapshot, task/seed/initialization,
information and action protocols, budget, saved model prompt, actual image or
video attachments, exact requested/returned model ID, raw response, parsed
action and verifier output. A file saved for audit is not necessarily included
in the request. An API or engine failure is not a valid model FAIL. Repeated
runs of an identical initialization are not additional independent seeds.

This is an input-contract inventory, not a claim that every combination of
task, seed, model and protocol has been executed or that every older aggregate
score has complete source attribution. Refer to the release validation scope
and the records for the specific comparison.
