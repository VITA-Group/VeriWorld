"""object_ground — harness_single_iterate_offcenter.

Off-centre variant of harness_single_iterate. The random-view sampler
aims the camera at a point 50-90 cm OFFSET from the target (not at the
target itself), so the optical-axis hit / first-round marker is
guaranteed to be off the target. Forces real visual localisation
instead of "the centre ball is already on the target, don't move it".
Otherwise identical to harness_single_iterate.
"""

from .task import run

__all__ = ["run"]
