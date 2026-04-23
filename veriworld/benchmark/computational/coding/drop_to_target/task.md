# Task: Deform a surface to drop a ball into a target circle

A ball sits on a flat green surface high above the ground. Somewhere below on the ground, there is a red target circle.
Your job: deform the surface (tilt, curve, ramp, funnel) so the ball rolls off and lands inside the red circle.

You do NOT know the target's coordinates. You must observe the video to determine:
- Which direction is the red circle relative to the ball?
- How far away is it?
- How steep should the surface tilt be?

## Your script must:
1. Clean old actors
2. Spawn GPUClothActor + InitCloth
3. Load slide_ball.slang (SetShader with entry point "SlideBall")
4. Compute your deformed surface shape in Python + UploadFloatArray
5. Upload ball initial position at (0, 0, {surface_z} + 25)
6. Material + camera + ball sync tick
7. Spawn target circle marker (the harness tells you the target position for spawning the marker only — do NOT use it to compute your surface tilt. Pretend you only know it from watching the video.)

Follow the api.md template EXACTLY. Only change the surface shape computation.

## What you observe in the video
- Green flat surface up high
- Red ball on the surface
- Red circle/disc on the ground below
- Camera is positioned to see both surface and ground

## Scene parameters (for setup only, NOT for solving)
Surface height: {surface_z}
Ball radius: 15cm, Grid: 40x40, spacing: 12cm
Ball start: (0, 0, {surface_z} + 25) — center of surface
