# Place on Platform

Pick up the cube and place it on the platform. The scene contains:

- one **platform** (large flat box)
- one **cube** (small box, physics-enabled)
- 1–3 **distractor** boxes of various sizes / colors

Every actor is given a **generic label** `OBJ_0`, `OBJ_1`, ... — the
agent doesn't know which OBJ index is the cube or the platform. The
mapping is shuffled per seed.

## Task

The cube starts on the ground off to the side of the platform.
The agent's job is to (a) figure out which OBJ is the cube and which
is the platform, then (b) move the cube so that, after physics
settle, its center sits on the platform's top surface within
tolerance.

## Pass condition

After the agent's last action, physics is allowed to settle for up
to 10 seconds. The cube passes if its final pose satisfies all of:

```
|cube.z - (platform.z + platform.dim_z/2 + cube.side/2)| < 5 cm
cube.x ∈ [platform.x - (platform.dim_x - cube.side)/2,
         platform.x + (platform.dim_x - cube.side)/2]
cube.y ∈ [platform.y - (platform.dim_y - cube.side)/2,
         platform.y + (platform.dim_y - cube.side)/2]
```

## Action format

Each round the agent emits exactly one JSON action inside a code
fence:

```json
{"cmd": "probe", "label": "OBJ_2"}
{"cmd": "move_to", "label": "OBJ_2", "to": [120.5, 30.0, 80.0]}
{"cmd": "stop"}
```

* `probe` lifts the named actor by 200 cm so the next screenshot
  reveals which physical object that label corresponds to. After the
  screenshot, the harness automatically restores it.
* `move_to` translates the named actor to absolute world coordinates
  `(x, y, z)` (cm). Use this to teleport the cube onto the platform.
  Velocity is reset to zero so physics settles cleanly afterward.
* `stop` ends the episode early. Settle + verify run on whatever the
  cube's final pose is.

## Harness variants

Three harnesses share `_shared.py` and `generate_params.py` and
differ only in what the agent observes:

| harness | screenshot | symbolic state list |
|---------|:---:|:---:|
| `harness_vision`    | ✓ | ✗ |
| `harness_symbolic`  | ✗ | ✓ (label, position, scale, rgb) |
| `harness_combined`  | ✓ | ✓ |

The symbolic state lists every `OBJ_i`'s `(label, position, scale_cm,
rgb)`. Distractor / cube / platform identities are implied (one is
small + bluish, one is large + flat + yellowish, others are random)
but never spelled out.
