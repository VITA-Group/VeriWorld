"""Per-seed motion parameters for moving_shooter.

Each seed generates:
  - target circular orbit (XY) + Z oscillation
  - arm orbit at larger radius with radial + Z oscillation, constant
    angular speed
  - projectile with exponential speed decay, gravity, and a stall
    cutoff
  - a HIDDEN frame rotation between the world frame (used by the UE
    simulation) and the equation frame the symbolic harness exposes;
    aim_yaw computed in the equation frame ≠ aim_yaw in the world
    frame, so a model that solves the symbolic equations only and
    doesn't ground them visually will MISS

Ported from VeriWorld unit_test 09e_moving_shooter_v2.
"""

from __future__ import annotations

import math
import random
from typing import Any


def generate(seed: int = 0) -> dict[str, Any]:
    rng = random.Random(seed)

    # Hidden rotation between world frame (sim) and equation frame
    # (what the symbolic prompt exposes). NOT shown to the agent.
    frame_rotation_deg = rng.uniform(40, 140) * rng.choice([-1, 1])
    frame_rotation_rad = math.radians(frame_rotation_deg)

    # Target circular + Z oscillation (WORLD frame, used by simulation)
    target_orbit_r = rng.uniform(160, 240)
    target_period = rng.uniform(7, 12)
    target_phase = rng.uniform(0, 2 * math.pi)
    target_z_amp = rng.uniform(40, 80)
    target_z_freq = rng.uniform(1.5, 3.0)

    # Arm orbit (WORLD frame)
    arm_angular_speed = rng.uniform(55, 80)
    arm_orbit_r = rng.uniform(750, 1050)
    arm_r_amp = rng.uniform(130, 220)
    arm_r_freq = rng.uniform(2, 4)
    arm_z_amp = rng.uniform(40, 80)
    arm_z_freq = rng.uniform(1.5, 2.5)
    arm_phase = rng.uniform(0, 2 * math.pi)

    # Projectile — exponential decay + gravity, range ~115-135 % of
    # min arm-target distance (so the agent can JUST hit if it picks
    # a moment with arm at apoapsis).
    min_arm_dist = (arm_orbit_r - arm_r_amp) - target_orbit_r
    desired_max_range = min_arm_dist * rng.uniform(1.15, 1.35)
    proj_decay_k = rng.uniform(2.5, 4.0)
    proj_v0 = desired_max_range * proj_decay_k
    proj_gravity = rng.uniform(300, 500)

    target_omega = 2 * math.pi / target_period
    # Derive the published range from the ROUNDED v0 and k, because that
    # is what the simulation reloads from params.json and what the
    # equations quote. Deriving it from the full-precision values leaves
    # the model with two mutually inconsistent statements about the same
    # trajectory (v(dt) uses v0, s(dt) uses max_range).
    proj_max_range = round(proj_v0, 1) / round(proj_decay_k, 2)

    return {
        "test_id": "moving_shooter",
        "seed": seed,
        "frame_rotation_deg": round(frame_rotation_deg, 1),
        "frame_rotation_rad": round(frame_rotation_rad, 6),
        # Target (WORLD frame)
        "target_orbit_r": round(target_orbit_r, 1),
        "target_period": round(target_period, 2),
        "target_phase": round(target_phase, 4),
        "target_omega": round(target_omega, 6),
        "target_z_amp": round(target_z_amp, 1),
        "target_z_freq": round(target_z_freq, 2),
        # Arm (WORLD frame)
        "arm_angular_speed": round(arm_angular_speed, 1),
        "arm_orbit_r": round(arm_orbit_r, 1),
        "arm_r_amp": round(arm_r_amp, 1),
        "arm_r_freq": round(arm_r_freq, 2),
        "arm_z_amp": round(arm_z_amp, 1),
        "arm_z_freq": round(arm_z_freq, 2),
        "arm_phase": round(arm_phase, 4),
        # Projectile
        "proj_v0": round(proj_v0, 1),
        "proj_decay_k": round(proj_decay_k, 2),
        "proj_max_range": round(proj_max_range, 1),
        "proj_gravity": round(proj_gravity, 1),
        # Fixed
        "pillar_height": 300.0,
        "target_z_base": 325.0,
        "target_z": 325.0,
        "arm_length": 200.0,
        "hit_radius": 45.0,
        "proj_stall_speed": 50.0,
        "max_sim_time": 20.0,
        "observe_time": 10.0,
    }


def published_constants(p: dict) -> dict[str, float]:
    """The exact constants that go into :func:`equations_text`.

    Factored out so the equation text and the consistency checker
    (``_check_equations``) cannot drift apart — a silent drift here is
    what made the task unsolvable before 2026-07-25.

    The equation frame is the world frame rotated by ``-theta``, and
    ONLY that rotation may distinguish the two. Getting there needs
    more care than subtracting theta from every phase:

    * ``x/y`` are genuine vector components, so their orbital phase
      shifts by ``-theta``.
    * ``z`` is invariant under a yaw rotation. Shifting its phase too
      makes the published height a different function of time than the
      simulated one — an error no visual grounding can undo.
    * the arm's ``r(t)`` and ``z_arm(t)`` are modulated by
      ``sin(k * angle)``. Rewriting ``angle`` in the equation frame
      turns that into ``sin(k * (angle_eq + theta))``, so the printed
      form has to carry an explicit ``+ k * theta`` phase. Because
      ``k != 1`` this is emphatically not rotation-invariant.
    """
    theta = p["frame_rotation_rad"]
    return {
        # Orbital phase in the equation frame (x/y only).
        "target_phase_xy": round(p["target_phase"] - theta, 4),
        # Height phase is frame-independent — do NOT rotate it.
        "target_phase_z": round(p["target_phase"], 4),
        "omega": round(p["target_omega"], 4),
        "arm_angle0_deg": round(math.degrees(p["arm_phase"] - theta), 1),
        # Modulation phases pick up k * theta when the angle is
        # re-expressed in the equation frame.
        "arm_r_phase": round(p["arm_r_freq"] * theta, 4),
        "arm_z_phase": round(p["arm_z_freq"] * theta, 4),
    }


def equations_text(p: dict) -> str:
    """Equations exposed to the symbolic / combined harnesses, written
    in the ROTATED frame. The frame rotation is the intended hidden
    difficulty; everything else must describe the simulation exactly
    (enforced by ``_check_equations``).
    """
    c = published_constants(p)

    return f"""## Motion Equations

**Target** (blue ball on gray pillar):
```
x_target(t) = {p['target_orbit_r']} * cos({c['omega']:.4f} * t + {c['target_phase_xy']:.4f})
y_target(t) = {p['target_orbit_r']} * sin({c['omega']:.4f} * t + {c['target_phase_xy']:.4f})
z_target(t) = {p['target_z_base']} + {p['target_z_amp']} * sin({p['target_z_freq']} * {c['omega']:.4f} * t + {c['target_phase_z']:.4f})
```
Period = {p['target_period']}s.

**Arm** (green shooter):
```
angle(t) = {p['arm_angular_speed']} * t + {c['arm_angle0_deg']:.1f}    (degrees)
r(t)     = {p['arm_orbit_r']} + {p['arm_r_amp']} * sin({p['arm_r_freq']} * angle_rad + {c['arm_r_phase']:.4f})
x_arm(t) = r(t) * cos(angle_rad)
y_arm(t) = r(t) * sin(angle_rad)
z_arm(t) = {p['target_z_base']} + {p['arm_z_amp']} * sin({p['arm_z_freq']} * angle_rad + {c['arm_z_phase']:.4f})
```

**Projectile** (leaves the muzzle at {p['proj_v0']:.1f} cm/s along aim_yaw / aim_pitch):
```
Horizontal speed: v(dt) = {p['proj_v0']:.1f} * cos(aim_pitch) * exp(-{p['proj_decay_k']} * dt)
Horizontal distance: s(dt) = {p['proj_max_range']:.1f} * cos(aim_pitch) * (1 - exp(-{p['proj_decay_k']} * dt))
Vertical: vz_0 = {p['proj_v0']:.1f} * sin(aim_pitch)
          z(dt) = z_fire + vz_0*dt - 0.5 * {p['proj_gravity']} * dt^2
```
Gravity = {p['proj_gravity']} cm/s² downward.
Hit radius = {p['hit_radius']} cm.

**aim_yaw** = absolute horizontal direction (degrees). **aim_pitch** = absolute vertical tilt (degrees)."""


if __name__ == "__main__":
    import json, sys
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    p = generate(seed)
    print(json.dumps(p, indent=2))
    print(f"\nHIDDEN frame rotation: {p['frame_rotation_deg']:.1f}°")
    min_d = (p['arm_orbit_r'] - p['arm_r_amp']) - p['target_orbit_r']
    print(f"Min dist: ~{min_d:.0f} cm, Max range: {p['proj_max_range']:.0f} cm")
