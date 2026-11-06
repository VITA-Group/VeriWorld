"""object_ground — harness_grid_iterate_offcenter.

Off-centre variant of harness_grid_iterate. Random-view sampler aims
the camera at a point 50-90 cm OFFSET from the target so the central
grid hit (idx=4 for 3×3) is guaranteed to land OFF the target —
forcing real visual localisation instead of "the centre anchor is
gift-wrapped on the target". Otherwise identical to harness_grid_iterate.
"""

from .task import run

__all__ = ["run"]
