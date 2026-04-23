"""Layer-3 ablation stub — replace with a real task.py.

Every Layer-3 ablation under a harness is a self-contained
``task.py`` with a module-level ``run(...)`` coroutine matching the
engine's expected signature. See the real examples:

* ``veriworld/infra/interactive/task_template.py``
* ``veriworld/infra/computational/task_template.py``

For the harness convention this file is a placeholder. Its job here is
to show the **shape** (folder layout + entry point), not a runnable
task.
"""

from __future__ import annotations

from typing import Any

from .._common import example_helper


async def run(*_args: Any, **_kwargs: Any) -> None:
    """Placeholder entry point. Real harness ablations return a
    dataclass describing the episode result — see the copy-ready
    templates in ``veriworld/infra/{interactive,computational}/``."""
    _ = example_helper()
    raise NotImplementedError("stub — copy a real task.py into your harness folder")


__all__ = ["run"]
