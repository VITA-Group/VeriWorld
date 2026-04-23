"""Harness-local shared helpers — example stub.

Everything that THIS harness's ablations share goes here. Things that
are genuinely harness-agnostic (screenshot encode/decode, WS wire
format, voxel setup code templates) should live in
``veriworld/common/`` or at the task root, not here.

Anti-pattern: importing from a sibling harness's ``_common.py`` — copy
what you need instead, so each harness can evolve independently.
"""

# from veriworld.common import ...   # harness-agnostic imports OK
# from .._sibling._common import ... # ← DON'T. Copy the helper instead.


def example_helper() -> str:
    """Placeholder — replace with actual harness-specific helpers."""
    return "example"
