"""DropToTarget — pure-visual ablation.

Agent sees only the video of the previous round. No numerical target
coordinates in the prompt. Must estimate target direction and distance
from the rendered frames, then write a Python script that deforms the
GPUCloth surface to steer the ball into the target.
"""

from .task import run

__all__ = ["run"]
