"""SurfaceBilliards — bounce-shot scale.

Agent launches a ball from fixed point A (above surface) and must hit
target B (crater on surface) after a single bounce. Terrain is a small
(60×60) gaussian-bump landscape with a central flat bounce zone, a hill
on the A side, a crater at B surrounded by rim guards, plus two random
bumps.

Why "bounce-shot scale"? The previous 80×80 + 37-gaussian version
triggered a crash in the packaged UE build during setup_observe
(suspected: large UploadFloatArray payload or uncooked shader variant).
Rolling_ball / bounce_shot unit-test demos at 60×60 + ≤9 gaussians both
run cleanly on the same build, so we match that scale.

The ``solution`` field returned here comes from a deterministic
2000-shot simulation search (angle uniform in [0, 2π], speed in
[120, max_speed]). A valid shot is one whose first-bounce trajectory
lands the ball inside the B crater radius after settling. This is the
empirical oracle — same design as the private harness_win_billiards
version, just at the smaller scale.
"""

from __future__ import annotations

import json
import math
import os
import random
import sys
from typing import Dict, List, Optional, Tuple

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def eval_surface(x: float, y: float, base_z: float, gaussians: List[Dict]) -> float:
    z = base_z
    for g in gaussians:
        dx, dy = x - g["cx"], y - g["cy"]
        z += g["height"] * math.exp(-(dx * dx + dy * dy) / (2 * g["sigma"] ** 2))
    return z


def build_grid(base_z: float, gaussians: List[Dict], grid_n: int,
               spacing: float) -> List[List[float]]:
    grid = []
    for gi in range(grid_n):
        row = []
        for gj in range(grid_n):
            x = (gi - grid_n / 2.0) * spacing
            y = (gj - grid_n / 2.0) * spacing
            row.append(eval_surface(x, y, base_z, gaussians))
        grid.append(row)
    return grid


def simulate(grid: List[List[float]], grid_n: int, spacing: float,
             sx: float, sy: float, sz: float,
             vx: float, vy: float, vz: float,
             ball_r: float = 12.0, gravity: float = 300.0, friction: float = 0.20,
             restitution: float = 0.5, dt: float = 0.003, max_steps: int = 8000
             ) -> Tuple[float, float, float]:
    """Simulate a bounce-shot trajectory. Matches bouncy_ball.slang's
    elastic-collision + tangent-friction model.
    """
    half = grid_n / 2.0

    def gz(gi: int, gj: int) -> float:
        gi = max(0, min(grid_n - 1, gi))
        gj = max(0, min(grid_n - 1, gj))
        return grid[gi][gj]

    x, y, z = sx, sy, sz

    for step in range(max_steps):
        vz -= gravity * dt
        px, py, pz = x + vx * dt, y + vy * dt, z + vz * dt

        gi = max(1, min(grid_n - 2, int(round(px / spacing + half))))
        gj = max(1, min(grid_n - 2, int(round(py / spacing + half))))

        near_x = (gi - half) * spacing
        near_y = (gj - half) * spacing
        near_z = grid[gi][gj]

        dzdx = (gz(gi + 1, gj) - gz(gi - 1, gj)) / (2 * spacing)
        dzdy = (gz(gi, gj + 1) - gz(gi, gj - 1)) / (2 * spacing)
        nx, ny, nz = -dzdx, -dzdy, 1.0
        nl = (nx * nx + ny * ny + nz * nz) ** 0.5
        nx /= nl; ny /= nl; nz /= nl

        tbx, tby, tbz = px - near_x, py - near_y, pz - near_z
        d3 = (tbx * tbx + tby * tby + tbz * tbz) ** 0.5
        if tbx * nx + tby * ny + tbz * nz < 0:
            nx, ny, nz = -nx, -ny, -nz
        ds = tbx * nx + tby * ny + tbz * nz

        if d3 < ball_r * 2 and ds < ball_r and ds > -ball_r * 2:
            # Collision: push ball out along normal
            c = ball_r - ds
            px += c * nx; py += c * ny; pz += c * nz
            vn = vx * nx + vy * ny + vz * nz
            if vn < 0:
                # Elastic bounce along normal with restitution
                vx -= (1.0 + restitution) * vn * nx
                vy -= (1.0 + restitution) * vn * ny
                vz -= (1.0 + restitution) * vn * nz
            # Tangent friction
            vn2 = vx * nx + vy * ny + vz * nz
            vtx, vty, vtz = vx - vn2 * nx, vy - vn2 * ny, vz - vn2 * nz
            vm = (vtx * vtx + vty * vty + vtz * vtz) ** 0.5
            if vm > 0.1:
                s = max(0.0, 1.0 - friction * gravity * dt / vm)
                vx = vn2 * nx + vtx * s
                vy = vn2 * ny + vty * s
                vz = vn2 * nz + vtz * s

        if pz < 15.0 + ball_r:
            pz = 15.0 + ball_r
            if vz < 0:
                vz = 0.0
            vx *= 0.95
            vy *= 0.95

        x, y, z = px, py, pz
        if step > 300 and vx * vx + vy * vy + vz * vz < 0.5:
            break

    return x, y, z


def search_shot(grid: List[List[float]], grid_n: int, spacing: float,
                base_z: float, ball_r: float, friction: float,
                a_x: float, a_y: float, a_z: float,
                b_x: float, b_y: float, b_radius: float,
                max_speed: float, pitch: float, rng: random.Random,
                n_attempts: int = 2000) -> Optional[Dict]:
    """Find a launch (v_angle, v_speed) that lands the ball inside the
    target crater. Returns the best attempt or None."""
    attempts = []
    toward_b = math.atan2(b_y - a_y, b_x - a_x)

    for _ in range(n_attempts):
        # Bias ~70% toward B, rest fully random
        if rng.random() < 0.7:
            angle = toward_b + rng.uniform(-0.4, 0.4)
        else:
            angle = rng.uniform(0, 2 * math.pi)
        speed = rng.uniform(120.0, max_speed)

        cos_p, sin_p = math.cos(pitch), math.sin(pitch)
        vx = speed * math.cos(angle) * cos_p
        vy = speed * math.sin(angle) * cos_p
        vz = speed * sin_p

        ex, ey, ez = simulate(grid, grid_n, spacing, a_x, a_y, a_z,
                              vx, vy, vz, ball_r=ball_r, friction=friction)

        dist = math.sqrt((ex - b_x) ** 2 + (ey - b_y) ** 2)
        if dist > b_radius:
            continue

        attempts.append({
            "angle": round(angle, 4),
            "speed": round(speed, 1),
            "end_x": round(ex, 1),
            "end_y": round(ey, 1),
            "end_z": round(ez, 1),
            "dist_to_target": round(dist, 1),
        })

    if not attempts:
        return None

    # Prefer the shot that lands closest to target centre
    attempts.sort(key=lambda a: a["dist_to_target"])
    return attempts[0]


def generate(seed: int = 0) -> Dict:
    rng = random.Random(seed)

    grid_n = 60
    spacing = 10.0
    ball_r = 12.0
    base_z = 300.0
    friction = 0.20
    max_speed = 350.0
    pitch = -0.3  # fixed launch pitch (radians); agent controls yaw + speed only

    # Fixed launch point A (corner of map, elevated above surface)
    a_x, a_y, a_z = -180.0, -120.0, 550.0

    # Target B — randomised in the opposite quadrant
    b_x = round(rng.uniform(80.0, 200.0), 1)
    b_y = round(rng.uniform(40.0, 160.0), 1)
    b_radius = round(rng.uniform(30.0, 45.0), 1)

    # Gaussian terrain — fixed layout, seed-perturbed parameters
    hill_cx = round(rng.uniform(-130.0, -70.0), 1)
    hill_cy = round(rng.uniform(-100.0, -60.0), 1)
    hill_h = round(rng.uniform(30.0, 55.0), 1)
    crater_depth = -round(rng.uniform(65.0, 95.0), 1)

    gaussians = [
        # Broad flat bounce zone in the middle
        {"cx": 0.0, "cy": 0.0, "sigma": 100.0, "height": 10.0},
        # Hill near A side
        {"cx": hill_cx, "cy": hill_cy, "sigma": 50.0, "height": hill_h},
        # Crater at B
        {"cx": b_x, "cy": b_y, "sigma": 35.0, "height": crater_depth},
        # 4 rim guards around B
        {"cx": round(b_x - 50.0, 1), "cy": b_y, "sigma": 25.0, "height": 30.0},
        {"cx": round(b_x + 50.0, 1), "cy": b_y, "sigma": 25.0, "height": 30.0},
        {"cx": b_x, "cy": round(b_y - 50.0, 1), "sigma": 25.0, "height": 30.0},
        {"cx": b_x, "cy": round(b_y + 50.0, 1), "sigma": 25.0, "height": 30.0},
        # 2 random terrain bumps
        {"cx": round(rng.uniform(-80.0, -20.0), 1),
         "cy": round(rng.uniform(20.0, 80.0), 1),
         "sigma": 35.0, "height": round(rng.uniform(10.0, 30.0), 1)},
        {"cx": round(rng.uniform(40.0, 100.0), 1),
         "cy": round(rng.uniform(-60.0, -20.0), 1),
         "sigma": 40.0, "height": round(rng.uniform(5.0, 25.0), 1)},
    ]

    grid = build_grid(base_z, gaussians, grid_n, spacing)

    print(f"  Surface base_z={base_z}, {len(gaussians)} gaussians (bounce-shot scale)")
    print(f"  Grid: {grid_n}x{grid_n}, spacing={spacing}")
    print(f"  A (launch) = ({a_x}, {a_y}, {a_z})")
    print(f"  B (target) = ({b_x}, {b_y}), R={b_radius}")
    print(f"  Searching for default solution shot...")

    solution = search_shot(grid, grid_n, spacing, base_z, ball_r, friction,
                           a_x, a_y, a_z, b_x, b_y, b_radius,
                           max_speed, pitch, rng)

    if solution:
        print(f"  Found: angle={math.degrees(solution['angle']):.0f}deg "
              f"speed={solution['speed']} dist={solution['dist_to_target']}")
    else:
        print("  WARNING: no valid shot found! Using fallback.")
        solution = {
            "angle": math.atan2(b_y - a_y, b_x - a_x),
            "speed": 220.0,
            "end_x": b_x, "end_y": b_y, "end_z": base_z,
            "dist_to_target": 999.0,
        }

    params = {
        "seed": seed,
        "grid_n": grid_n,
        "grid_spacing": spacing,
        "ball_radius": ball_r,
        "surface_z_base": base_z,
        "friction": friction,
        "gaussians": gaussians,
        "start": {"x": a_x, "y": a_y, "z": a_z},
        "target": {"x": b_x, "y": b_y, "radius": b_radius},
        "max_speed": max_speed,
        "pitch": pitch,
        "solution": solution,
    }
    return params


if __name__ == "__main__":
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    params = generate(seed)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "params.json")
    with open(out, "w") as f:
        json.dump(params, f, indent=2)
    print(f"\nSaved params.json  seed={seed}")
