"""MazeNavFPS — first-person maze navigation task.

Each ablation is a sibling subpackage. Each runs independently, has its
own hard-coded conditions, and owns its own CLI via ``__main__.py``.

Available ablations:

* :mod:`veriworld.benchmark.interactive.navigation.mazenavfps.vp_bf` —
  screenshot + position log, batch free actions.
* :mod:`veriworld.benchmark.interactive.navigation.mazenavfps.pv_bf` —
  pure vision (screenshot history grid, no coordinates).

Shared, stateless helpers live in :mod:`._common`. Task assets
(``generate_params.py``, ``ue_setup.py``, ``move_camera.py``) sit at
this top level and are reused by every ablation.
"""
