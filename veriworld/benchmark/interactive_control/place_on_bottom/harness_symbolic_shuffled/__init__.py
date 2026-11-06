"""place_on_platform — SYMBOLIC harness with shuffled serialization.

Serialization-order ablation (GHV8 W4/Q3, the "prompt design /
inconsistent information representation" alternative): identical to
harness_symbolic except the observation's object list is deterministically
permuted per round and each entry's key order is reversed. If S scores are
unchanged under scrambled serialization, representation format cannot be
what drives the S-vs-C gap.
"""
from .task import run

__all__ = ["run"]
