"""box_fold — fold a flat cube net back into a closed cube.

Per episode the agent sees the 6-face T-cross flat net of a cube and
must emit one ``fold(hinge_id, angle)`` action per round to close it.
After the agent's last action the harness reads back each face's
final pose and compares to the canonical closed-cube pose; PASS if
all 6 faces are within tolerance.
"""
