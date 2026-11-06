"""Place on Platform — port of unit test 01_place_on_platform.

Spawns one platform (large flat box), one cube, and 1-3 distractor
boxes. Every actor gets a generic ``OBJ_i`` label; the agent must
identify which OBJ is the cube and which is the platform, then
move the cube onto the platform's top surface. After the agent's
final action the cube is allowed to settle under physics; the
verdict checks whether it ended up resting on the platform within
position tolerance.
"""
