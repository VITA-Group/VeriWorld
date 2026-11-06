"""
17_surface_billiards_hard — Larger map, side-valley target, central decoy.

Harder than test 16:
  - 80x80 grid (was 50x50) — bigger terrain
  - Central crater is a DECOY (deepest point, ball naturally rolls there)
  - Target in a SIDE VALLEY at r~150-200, NOT the global attractor
  - Side valley guarded by ridges — must aim precisely
  - Smaller target radius (25-35 vs 40-60)
  - Higher friction (0.20 vs 0.15)
  - Start at edge (radius 420 vs 240)

Usage:
    python generate_params.py [seed]
"""
import json, math, os, random, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def eval_surface(x, y, base_z, gaussians):
    z = base_z
    for g in gaussians:
        dx, dy = x - g["cx"], y - g["cy"]
        z += g["height"] * math.exp(-(dx * dx + dy * dy) / (2 * g["sigma"] ** 2))
    return z


def generate_gaussians(rng):
    """
    Hard landscape:
      - Central crater = DECOY (deepest, natural attractor, NOT the target)
      - Side valley = TARGET (shallower, off-center, guarded by ridges)
      - Multiple other decoy valleys
      - Blocking ridges everywhere
    """
    gs = []

    # ── Central DECOY crater: deepest pit near origin ──
    # This is where careless shots end up — NOT the target!
    decoy_cx = round(rng.uniform(-25, 25), 1)
    decoy_cy = round(rng.uniform(-25, 25), 1)
    gs.append({
        "cx": decoy_cx,
        "cy": decoy_cy,
        "sigma": round(rng.uniform(35, 50), 1),
        "height": -round(rng.uniform(120, 170), 1),  # very deep
    })

    # ── Target side valley: at r~150-200 from center ──
    target_angle = rng.uniform(0, 2 * math.pi)
    target_r = rng.uniform(150, 200)
    target_cx = round(target_r * math.cos(target_angle), 1)
    target_cy = round(target_r * math.sin(target_angle), 1)
    gs.append({
        "cx": target_cx,
        "cy": target_cy,
        "sigma": round(rng.uniform(25, 38), 1),
        "height": -round(rng.uniform(55, 80), 1),  # shallower than central
    })

    # ── Ridges guarding the target valley: 3-4 around it ──
    n_guard = rng.randint(3, 4)
    guard_base = rng.uniform(0, 2 * math.pi)
    # Leave one gap for access
    gap_angle = guard_base + rng.uniform(0.8, 1.5) * 2 * math.pi / n_guard
    for i in range(n_guard):
        angle = guard_base + i * 2 * math.pi / n_guard + rng.uniform(-0.2, 0.2)
        diff = abs(angle - gap_angle) % (2 * math.pi)
        if diff > math.pi:
            diff = 2 * math.pi - diff
        if diff < 0.5:
            continue  # skip gap
        guard_r = rng.uniform(40, 65)
        gs.append({
            "cx": round(target_cx + guard_r * math.cos(angle), 1),
            "cy": round(target_cy + guard_r * math.sin(angle), 1),
            "sigma": round(rng.uniform(20, 35), 1),
            "height": round(rng.uniform(50, 90), 1),
        })

    # ── Ridge between central decoy and target: deflects balls away ──
    mid_x = (decoy_cx + target_cx) / 2
    mid_y = (decoy_cy + target_cy) / 2
    gs.append({
        "cx": round(mid_x + rng.uniform(-20, 20), 1),
        "cy": round(mid_y + rng.uniform(-20, 20), 1),
        "sigma": round(rng.uniform(30, 50), 1),
        "height": round(rng.uniform(55, 90), 1),
    })

    # ── Decoy craters: 4-6 deep valleys scattered around ──
    n_decoys = rng.randint(4, 6)
    decoy_base = rng.uniform(0, 2 * math.pi)
    for i in range(n_decoys):
        angle = decoy_base + i * 2 * math.pi / n_decoys + rng.uniform(-0.3, 0.3)
        dr = rng.uniform(180, 320)
        # Avoid placing decoy on top of the target valley
        dx = dr * math.cos(angle) - target_cx
        dy = dr * math.sin(angle) - target_cy
        if math.sqrt(dx * dx + dy * dy) < 80:
            continue
        gs.append({
            "cx": round(dr * math.cos(angle) + rng.uniform(-20, 20), 1),
            "cy": round(dr * math.sin(angle) + rng.uniform(-20, 20), 1),
            "sigma": round(rng.uniform(30, 55), 1),
            "height": -round(rng.uniform(70, 130), 1),
        })

    # ── Blocking ridges: 5-7 at r~200-380 ──
    n_blocks = rng.randint(5, 7)
    for i in range(n_blocks):
        angle = rng.uniform(0, 2 * math.pi)
        block_r = rng.uniform(200, 380)
        gs.append({
            "cx": round(block_r * math.cos(angle), 1),
            "cy": round(block_r * math.sin(angle), 1),
            "sigma": round(rng.uniform(30, 55), 1),
            "height": round(rng.uniform(40, 85), 1),
        })

    # ── Noise features: 12-18 random small bumps ──
    for _ in range(rng.randint(12, 18)):
        gs.append({
            "cx": round(rng.uniform(-400, 400), 1),
            "cy": round(rng.uniform(-400, 400), 1),
            "sigma": round(rng.uniform(15, 40), 1),
            "height": round(rng.choice([-1, 1]) * rng.uniform(10, 35), 1),
        })

    # ── Sinusoidal undulation: 5-7 large gentle features ──
    for _ in range(rng.randint(5, 7)):
        gs.append({
            "cx": round(rng.uniform(-200, 200), 1),
            "cy": round(rng.uniform(-200, 200), 1),
            "sigma": round(rng.uniform(90, 140), 1),
            "height": round(rng.choice([-1, 1]) * rng.uniform(15, 45), 1),
        })

    return gs, (target_cx, target_cy)


def build_grid(base_z, gaussians, grid_n, spacing):
    grid = []
    for gi in range(grid_n):
        row = []
        for gj in range(grid_n):
            x = (gi - grid_n / 2.0) * spacing
            y = (gj - grid_n / 2.0) * spacing
            row.append(eval_surface(x, y, base_z, gaussians))
        grid.append(row)
    return grid


def simulate(grid, grid_n, spacing, sx, sy, vx, vy,
             ball_r=15.0, gravity=300.0, friction=0.20,
             dt=0.003, max_steps=8000):
    half = grid_n / 2.0

    def gz(gi, gj):
        gi = max(0, min(grid_n - 1, gi))
        gj = max(0, min(grid_n - 1, gj))
        return grid[gi][gj]

    x, y = sx, sy
    gi0 = max(0, min(grid_n - 1, int(round(x / spacing + half))))
    gj0 = max(0, min(grid_n - 1, int(round(y / spacing + half))))
    z = grid[gi0][gj0] + ball_r + 2.0
    vz = 0.0

    for step in range(max_steps):
        vz -= gravity * dt
        px = x + vx * dt
        py = y + vy * dt
        pz = z + vz * dt

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
            c = ball_r - ds
            px += c * nx; py += c * ny; pz += c * nz
            vn = vx * nx + vy * ny + vz * nz
            if vn < 0:
                vx -= vn * nx; vy -= vn * ny; vz -= vn * nz
            vn2 = vx * nx + vy * ny + vz * nz
            vtx, vty, vtz = vx - vn2 * nx, vy - vn2 * ny, vz - vn2 * nz
            vm = (vtx * vtx + vty * vty + vtz * vtz) ** 0.5
            if vm > 0.1:
                s = max(0, 1 - friction * gravity * dt / vm)
                vx = vn2 * nx + vtx * s
                vy = vn2 * ny + vty * s
                vz = vn2 * nz + vtz * s

        if pz < 15 + ball_r:
            pz = 15 + ball_r
            if vz < 0: vz = 0
            vx *= 0.95; vy *= 0.95

        x, y, z = px, py, pz
        if step > 300 and vx * vx + vy * vy + vz * vz < 0.5:
            break

    return x, y, z


def pick_fixed_start_and_target(grid, grid_n, spacing, start_r, ball_r,
                                 friction, target_center, target_r, rng):
    """
    Target is in the side valley. Start at the edge, preferably on the
    OPPOSITE side so the central decoy is between start and target.
    Verify a solution exists by simulating many shots.
    """
    half_w = (grid_n / 2.0) * spacing
    tx, ty = target_center
    target_angle = math.atan2(ty, tx)
    attempts = []

    for _ in range(2000):
        # Start on opposite side from target (central decoy in between)
        sa = target_angle + math.pi + rng.uniform(-0.6, 0.6)
        sx = start_r * math.cos(sa)
        sy = start_r * math.sin(sa)

        # Random shot
        angle = rng.uniform(0, 2 * math.pi)
        speed = rng.uniform(80, 480)

        ex, ey, ez = simulate(grid, grid_n, spacing, sx, sy,
                              speed * math.cos(angle), speed * math.sin(angle),
                              ball_r=ball_r, friction=friction)

        if ez < 30 + ball_r:
            continue

        # Check if ball lands near target
        dist_to_target = math.sqrt((ex - tx) ** 2 + (ey - ty) ** 2)
        if dist_to_target > target_r:
            continue

        # Must NOT be in the central decoy (origin area)
        dist_to_center = math.sqrt(ex * ex + ey * ey)
        if dist_to_center < 60:
            continue  # Landed in central decoy, not target

        travel = math.sqrt((ex - sx) ** 2 + (ey - sy) ** 2)
        if travel < 200:
            continue

        attempts.append({
            "start_x": round(sx, 1), "start_y": round(sy, 1),
            "angle": round(angle, 4), "speed": round(speed, 1),
            "end_x": round(ex, 1), "end_y": round(ey, 1),
            "end_z": round(ez, 1),
            "travel": round(travel, 1),
            "dist_to_target": round(dist_to_target, 1),
        })

    if not attempts:
        return None, None, None

    # Pick a good solution: prefer medium speed (not trivially easy)
    attempts.sort(key=lambda a: abs(a["speed"] - 280))
    shot = attempts[0]
    target = {"x": round(tx, 1), "y": round(ty, 1)}
    start = {"x": shot["start_x"], "y": shot["start_y"]}
    return shot, target, start


def generate(seed=0):
    rng = random.Random(seed)
    base_z = round(rng.uniform(350, 430), 1)
    grid_n, spacing, ball_r = 80, 12.0, 15.0
    friction = 0.20
    start_r = 420.0
    max_speed = 500.0
    target_r = round(rng.uniform(25, 35), 1)

    gaussians, target_valley = generate_gaussians(rng)
    grid = build_grid(base_z, gaussians, grid_n, spacing)

    print(f"  Surface base_z={base_z}, {len(gaussians)} gaussians")
    print(f"  Grid: {grid_n}x{grid_n}, spacing={spacing}")
    print(f"  Target valley: ({target_valley[0]}, {target_valley[1]})")
    print(f"  Central decoy near origin (deepest pit)")
    print(f"  Picking fixed start (opposite side from target)...")

    solution, target, start = pick_fixed_start_and_target(
        grid, grid_n, spacing, start_r, ball_r, friction,
        target_valley, target_r, rng)

    if solution:
        target["radius"] = target_r
        dist = math.sqrt((start["x"] - target["x"]) ** 2 + (start["y"] - target["y"]) ** 2)
        print(f"  Fixed start: ({start['x']}, {start['y']})")
        print(f"  Target: ({target['x']}, {target['y']}), R={target_r}")
        print(f"  Start-to-target distance: {dist:.1f}")
        print(f"  Solution angle={math.degrees(solution['angle']):.0f}deg speed={solution['speed']}")
        print(f"  Travel distance: {solution['travel']}")
        print(f"  Landing dist to target: {solution['dist_to_target']}")
    else:
        print("  WARNING: no valid shot found! Using fallback.")
        start = {"x": round(start_r, 1), "y": 0.0}
        target = {"x": round(target_valley[0], 1),
                   "y": round(target_valley[1], 1), "radius": target_r}
        solution = {"start_x": start["x"], "start_y": start["y"],
                     "angle": math.pi, "speed": 200.0,
                     "end_x": target["x"], "end_y": target["y"],
                     "end_z": base_z, "travel": 0.0, "dist_to_target": 999.0}

    return {
        "seed": seed, "surface_z_base": base_z,
        "grid_n": grid_n, "grid_spacing": spacing,
        "ball_radius": ball_r, "friction": friction,
        "start": start,
        "max_speed": max_speed,
        "gaussians": gaussians,
        "target": target,
        "solution": solution,
    }


if __name__ == "__main__":
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    params = generate(seed)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "params.json")
    with open(out, "w") as f:
        json.dump(params, f, indent=2)
    print(f"\nSaved params.json  seed={seed}")
