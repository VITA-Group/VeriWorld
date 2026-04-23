"""Tunnel — 3D HermitePipe navigation task.

Each ablation is a sibling subpackage with its own hard-coded conditions
and its own CLI:

* :mod:`veriworld.benchmark.interactive.navigation.tunnel.vp_bf` —
  screenshot + position log, batch free 3D commands.
* :mod:`veriworld.benchmark.interactive.navigation.tunnel.af` —
  aim-and-fly compound action (``{see, yaw, pitch, forward}``),
  vision-only.

Shared stateless helpers in :mod:`._common`; task assets
(``generate_params.py``, ``ue_setup.py``, ``move_camera.py``) sit at
this top level.
"""
