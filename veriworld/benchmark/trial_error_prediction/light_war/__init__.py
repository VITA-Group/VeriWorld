"""lightwar — distant-terrain arc-projectile grounding.

Tests an agent's perception of long-range terrain features by giving it
control over a kinematic parabolic trajectory toward a glowing beacon
on a hilltop several hundred metres away. Each round it emits launch
parameters (yaw, pitch, v0); the harness analytically traces the
parabola, captures 6 FPV frames along the projectile's path, stacks
them into a single 2x3 grid screenshot, and feeds that back as the
"experience" of the previous shot. PASS = the final round's landing
point is within `tolerance_cm` of the beacon's 3D position.

No physics simulation — trajectory is computed analytically with
constant gravity (980 cm/s^2 down). This keeps the test about reading
a parabolic arc against terrain, not about UE physics-tick fidelity.
"""
