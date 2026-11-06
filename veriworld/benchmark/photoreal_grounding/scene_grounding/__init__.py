"""scene_ground — view-recovery navigation in v2 photoreal levels.

The agent is shown a target screenshot taken from a deterministic pose
in one of 4 levels (living_room / office / forest / aiue) and must
recover that view within ``max_rounds=15``. The agent spawns within
~300-600 cm of the target (with a 60-150° yaw offset) so the task is
tractable.

PASS = final pose within ``tolerance_xy_cm`` (default 200 cm) and
``tolerance_yaw_deg`` (default 15°) of the target. Same ``view_seed``
→ same (target_pose, spawn_pose) across all models AND across both
sub-harnesses for clean apples-to-apples comparison.

Sub-harness layout (action-space ablation):

* ``harness_walk/`` — collision-aware embodied locomotion via
  ``walk_tick.py`` (CharacterMovement.AddMovementInput). Actions:
  ``walk(distance)``, ``set_view(yaw, pitch)``, ``stop``. Agent must
  navigate around obstacles.
* ``harness_teleport/`` — free-observer no-collision pose specification
  via ``K2_SetActorLocation(sweep=False)``. Actions:
  ``set_pose(X, Y, Z, yaw, pitch)``, ``stop``. Agent teleports
  anywhere instantly.

Comparing the two isolates "does collision-aware embodiment help or
hurt view recovery?".
"""
