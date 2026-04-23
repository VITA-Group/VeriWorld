"""Standard VeriWorld run-directory layout.

Every task writes artifacts into a timestamped directory under
``veriworld/results/<task_path>/`` so that:

1. Outputs mirror the ``veriworld/benchmark/`` source tree, making it
   easy to navigate to a specific ablation's history.
2. Task → run → model forms three clear levels of nesting.
3. Multiple runs of the same task/seed don't collide (timestamped).

Example::

    veriworld/results/
    └── computational/feedback/surface_billiards/
        └── seed_0000_20260419_143012/
            ├── params.json              # seeded state
            ├── orchestrator.json        # batch info (when fair-compare)
            └── <model_name>/
                ├── summary.json
                ├── round_00_observe.mp4
                └── ...

The :class:`RunLogger` class is the single place that encodes this
convention. Task code should not build paths by hand.
"""

from __future__ import annotations

import importlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# Prefix stripped when deriving task paths from ``veriworld.benchmark.*`` module
# names. Kept as a constant so the build-a-path logic is visible in one place.
_BENCHMARK_PREFIX = "veriworld.benchmark."


def task_path_from_module(module_path: str) -> str:
    """Convert a dotted task module path to a relative directory path.

    ``veriworld.benchmark.interactive.navigation.mazenavfps.vp_bf`` ->
    ``interactive/navigation/mazenavfps/vp_bf``. If the module doesn't
    live under ``veriworld.benchmark``, the full dotted path is used
    verbatim (with dots → slashes).
    """
    stripped = module_path[len(_BENCHMARK_PREFIX):] if module_path.startswith(_BENCHMARK_PREFIX) else module_path
    return stripped.replace(".", "/")


class RunLogger:
    """Create and write into a ``results/<task_path>/seed_XXXX_TIMESTAMP/``
    directory.

    Parameters
    ----------
    root : Path | str
        Repository root. The full output path becomes
        ``<root>/veriworld/results/<task_path>/seed_XXXX_TS/`` when
        ``task_path`` is given, else the legacy
        ``<root>/runs/seed_XXXX_TS/``.
    seed : int
        Used in the directory name.
    task_path : str | None
        Relative task path under ``results/`` — typically derived via
        :func:`task_path_from_module`. When ``None``, the legacy
        ``runs/`` layout is used (kept so older code paths continue to
        work while the migration lands).
    timestamp : str | None
        Defaults to ``strftime("%Y%m%d_%H%M%S")``. Override for tests.
    """

    def __init__(
        self,
        root: Path | str,
        seed: int,
        *,
        task_path: Optional[str] = None,
        timestamp: Optional[str] = None,
    ) -> None:
        ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
        if task_path is not None:
            self.root = (Path(root) / "veriworld" / "results" / task_path
                         / f"seed_{seed:04d}_{ts}")
        else:
            self.root = Path(root) / "runs" / f"seed_{seed:04d}_{ts}"
        self.root.mkdir(parents=True, exist_ok=True)
        self._model_dirs: dict[str, Path] = {}
        # Mirror harness READMEs from benchmark/ into results/ the first
        # time any run under a given harness writes there. Keeps the
        # results tree self-documenting about the harness design that
        # produced the runs, even on fresh machines where the local
        # ``veriworld/results/`` directory was wiped or never existed.
        # See ``veriworld/infra/harness/SKILL.md`` for the convention.
        self._sync_harness_readmes(Path(root))

    def _sync_harness_readmes(self, repo_root: Path) -> None:
        """For every ``harness_*`` segment in the ancestry of ``self.root``,
        copy the corresponding benchmark-side ``README.md`` into the
        results-side harness directory if missing. Silent no-op on any
        path oddity (missing benchmark README, non-standard layout,
        legacy ``runs/`` output)."""
        results_root = repo_root / "veriworld" / "results"
        benchmark_root = repo_root / "veriworld" / "benchmark"
        try:
            resolved = self.root.resolve()
            results_resolved = results_root.resolve()
            rel = resolved.relative_to(results_resolved)
        except (ValueError, OSError):
            return  # legacy layout or path outside results/ — skip silently
        cur = results_resolved
        for segment in rel.parts[:-1]:  # all ancestors between results/ and seed_*/
            cur = cur / segment
            if not segment.startswith("harness_"):
                continue
            dst = cur / "README.md"
            if dst.exists():
                continue
            src = benchmark_root / cur.relative_to(results_resolved) / "README.md"
            if src.exists():
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                except OSError:
                    pass

    def model_dir(self, model_name: str) -> Path:
        """Get (creating if needed) the per-model subdirectory."""
        d = self._model_dirs.get(model_name)
        if d is None:
            d = self.root / model_name
            d.mkdir(parents=True, exist_ok=True)
            self._model_dirs[model_name] = d
        return d

    def write_json(self, relative: str, data: Any) -> Path:
        """Write ``data`` as JSON at ``relative`` inside the run root."""
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def write_text(self, relative: str, text: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def write_bytes(self, relative: str, data: bytes) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def write_prompt(
        self,
        model_name: str,
        key: str,
        messages: list,
        image_refs: Optional[list[str]] = None,
    ) -> Path:
        """Write a human-readable prompt transcript for post-hoc audit.

        Each step / round's prompt bakes in history from all prior
        steps — so it's a genuine per-call artefact, not derivable from
        the static ``task.md`` template.

        Format (matches the private harness's ``step_NNN_prompt.txt``):

            === SYSTEM PROMPT ===
            <system content>

            === USER MESSAGE ===
            <user text part 1>
            ...
            === IMAGE: <filename> ===
            <user text part 2>
            ...

        Images (``image_url`` parts) are NOT inlined as base64 — the
        caller is expected to have already saved them separately with a
        stable filename, and passes that filename via ``image_refs`` in
        order of appearance.

        Parameters
        ----------
        model_name : str
            Per-model subdirectory under the run root.
        key : str
            Filename-safe identifier — usually ``f"round_{n:02d}"`` for
            per-round tasks or ``f"step_{n:03d}"`` for per-tick tasks.
        messages : list
            The exact ``messages`` list being sent to the model.
        image_refs : list[str] | None
            Filenames (relative to the model dir) or URLs, one per
            ``image_url`` part in ``messages``. If ``None`` or too
            short, missing entries render as ``<inline data URL>``.
        """
        path = self.model_dir(model_name) / f"{key}_prompt.txt"
        path.write_text(
            format_prompt_txt(messages, image_refs or []),
            encoding="utf-8",
        )
        return path

    # ------------------------------------------------------------------
    # Snapshotting for self-contained reproducibility
    # ------------------------------------------------------------------
    def snapshot_model(
        self,
        task_module: str,
        model_name: str,
        seed: int,
        *,
        resolved_args: Optional[dict[str, Any]] = None,
        extra_config: Optional[dict[str, Any]] = None,
    ) -> None:
        """Per-model self-containment: write ``config.json`` plus
        ``reproduce.bat`` / ``reproduce.sh`` in the model dir.

        The run-level ``snapshot_task`` captures sources shared by all
        models on this seed (task.md, generate_params.py, lean_verify/,
        etc.). This complement covers the *single*-model context —
        enough that someone handed just the model subfolder can rerun
        it in isolation without the rest of the batch.

        Parameters
        ----------
        task_module : str
            Dotted path, e.g. ``veriworld.benchmark....surface_billiards``.
        model_name : str
            Value of ``--model`` when re-invoking.
        seed : int
            Seed used by this episode.
        resolved_args : dict | None
            Final CLI args (post-defaults merge). Saved verbatim to
            ``config.json`` and folded into the reproducer commands.
        """
        model_dir = self.model_dir(model_name)
        args = dict(resolved_args or {})

        config = {
            "task": task_module,
            "seed": seed,
            "model": model_name,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "resolved_args": args,
            **(extra_config or {}),
        }
        (model_dir / "config.json").write_text(
            json.dumps(config, indent=2, default=str), encoding="utf-8",
        )

        self._write_model_reproducer(model_dir, task_module, model_name, seed, args)

    def _write_model_reproducer(
        self,
        model_dir: Path,
        task_module: str,
        model_name: str,
        seed: int,
        resolved_args: dict[str, Any],
    ) -> None:
        """Emit reproduce.bat / reproduce.sh at model_dir that call the
        task's single-seed single-model CLI directly (``python -m
        <task> --seed N --model M ...``)."""
        def fmt_value(v: Any) -> str:
            if isinstance(v, bool):
                return "true" if v else "false"
            if isinstance(v, (list, tuple)):
                return ",".join(str(x) for x in v)
            return str(v)

        skip = {"task", "log_level", "seed", "seeds", "model", "models",
                "defaults", "configs", "output_root", "max_instances",
                "base_port", "width", "height", "attach"}
        flag_items: list[tuple[str, str]] = [
            ("--seed", str(seed)),
            ("--model", model_name),
        ]
        for key, value in resolved_args.items():
            if value is None or key in skip:
                continue
            flag = "--" + key.replace("_", "-")
            flag_items.append((flag, fmt_value(value)))

        base = f"python -m {task_module}"

        sh_lines = ["#!/usr/bin/env bash",
                    f"# Rerun {model_name} on seed {seed} in isolation.",
                    "set -euo pipefail",
                    "",
                    f"{base} \\"]
        for flag, value in flag_items:
            sh_lines.append(f"  {flag} {value} \\")
        if sh_lines[-1].endswith("\\"):
            sh_lines[-1] = sh_lines[-1].rstrip(" \\")
        (model_dir / "reproduce.sh").write_text(
            "\n".join(sh_lines) + "\n", encoding="utf-8")

        bat_lines = ["@echo off",
                    f"REM Rerun {model_name} on seed {seed} in isolation.",
                    ""]
        if flag_items:
            bat_lines.append(f"{base} ^")
            for i, (flag, value) in enumerate(flag_items):
                cont = " ^" if i < len(flag_items) - 1 else ""
                bat_lines.append(f"  {flag} {value}{cont}")
        else:
            bat_lines.append(base)
        (model_dir / "reproduce.bat").write_text(
            "\r\n".join(bat_lines) + "\r\n", encoding="utf-8")

    def snapshot_task(
        self,
        task_module: str,
        *,
        resolved_args: Optional[dict[str, Any]] = None,
        invocation: str = "parallel",
    ) -> None:
        """Copy task-defining sources into this run dir and write a
        reproducer script.

        Reading only this directory should let anyone regenerate the
        seed, inspect the task prompt the agent saw, and rerun the exact
        configuration. Specifically, copies (from the task module's own
        folder, falling back to its parent — this handles tasks whose
        shared assets live one level up like
        ``mazenavfps/generate_params.py`` shared by ``vp_bf``/``pv_bf``):

        - ``task.md``, ``api.md``, ``example.py``, ``generate_params.py``
        - the whole ``lean_verify/`` subfolder (skipping ``__pycache__``
          and stale runtime artefacts like ``log_for_verify.txt``)

        Plus writes:

        - ``run.json`` — resolved CLI args / conditions / timestamp
        - ``reproduce.bat`` (Windows) and ``reproduce.sh`` (POSIX) that
          re-invoke the exact command; both noop-safe on the wrong OS.

        Parameters
        ----------
        task_module : str
            Dotted path to the task (the same value passed as
            ``--task`` to the orchestrator). Used both to locate source
            files and to write the reproducer command.
        resolved_args : dict | None
            The final merged args (CLI + defaults). Everything in here
            lands in ``run.json`` so nothing is hidden.
        invocation : str
            ``"parallel"`` for ``run_parallel.py`` runs, ``"single"`` for
            a per-task ``__main__.py``. Controls the shape of the
            generated reproducer command.
        """
        task_mod = importlib.import_module(task_module)
        task_dir = Path(task_mod.__file__).parent

        # Files to snapshot. Each may live at the ablation folder, at
        # the harness folder one level up, or at the task root two
        # levels up (``generate_params.py``/``ue_setup.py`` are
        # task-level, shared across harnesses). ``_copy_task_asset``
        # walks ancestors and takes the first match.
        for name in (
            "task.md", "api.md", "example.py",
            "generate_params.py",
            "ue_setup.py", "move_camera.py",   # UE scene plumbing (task root)
            "_common.py",                        # harness-shared helpers (1 level up)
            "setup_observe.py", "setup_shot.py",  # computational tasks
        ):
            self._copy_task_asset(task_dir, name)
        # ``task.py`` holds the system prompt / harness loop — the single
        # most authoritative record of what the agent was shown.
        self._copy_task_asset(task_dir, "task.py")
        self._copy_lean_verify(task_dir)

        (self.root / "run.json").write_text(
            json.dumps({
                "task": task_module,
                "invocation": invocation,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "python": sys.version.split()[0],
                "resolved_args": resolved_args or {},
            }, indent=2, default=str),
            encoding="utf-8",
        )

        self._write_reproducers(task_module, resolved_args or {}, invocation)

    # -- snapshotting helpers --------------------------------------------------
    def _copy_task_asset(self, task_dir: Path, name: str) -> None:
        """Copy ``name`` from ``task_dir`` or any ancestor up to 4 levels
        (ablation → harness → task → family → super), taking the first
        match. No-op if none of them has it.

        The 4-level walk is what makes post-harness-wrap layouts work:
        ``mazenavfps/harness_structured/vp_bf/task.py``'s own dir is
        the ablation (0), the harness is 1 up, the task root (where
        ``generate_params.py`` / ``ue_setup.py`` live) is 2 up.
        """
        candidates = [task_dir]
        for _ in range(4):
            candidates.append(candidates[-1].parent)
        for d in candidates:
            p = d / name
            if p.is_file():
                shutil.copy2(p, self.root / name)
                return

    def _copy_lean_verify(self, task_dir: Path) -> None:
        """Copy the whole ``lean_verify/`` folder from the closest
        ancestor that has one (same ``4`` level walk as
        :meth:`_copy_task_asset`). Skips ``__pycache__`` and stale
        runtime artefacts."""
        candidates = [task_dir]
        for _ in range(4):
            candidates.append(candidates[-1].parent)
        for d in candidates:
            lv = d / "lean_verify"
            if lv.is_dir():
                shutil.copytree(
                    lv,
                    self.root / "lean_verify",
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(
                        "__pycache__", "*.pyc", "log_for_verify.txt",
                    ),
                )
                return
                return

    def _write_reproducers(
        self,
        task_module: str,
        resolved_args: dict[str, Any],
        invocation: str,
    ) -> None:
        """Emit reproduce.bat (cmd.exe) and reproduce.sh (bash) with the
        exact command to rerun this configuration."""
        def fmt_value(v: Any) -> str:
            if isinstance(v, bool):
                return "true" if v else "false"
            if isinstance(v, (list, tuple)):
                return ",".join(str(x) for x in v)
            return str(v)

        flag_items: list[tuple[str, str]] = []
        if invocation == "parallel":
            base = f"python -m veriworld.scripts.run_parallel --task {task_module}"
            for key, value in resolved_args.items():
                if value is None or key in ("task", "log_level"):
                    continue
                flag = "--" + key.replace("_", "-")
                flag_items.append((flag, fmt_value(value)))
        else:
            base = f"python -m {task_module}"
            for key, value in resolved_args.items():
                if value is None or key in ("task", "log_level"):
                    continue
                flag = "--" + key.replace("_", "-")
                flag_items.append((flag, fmt_value(value)))

        # POSIX shell (one flag per line, line-continued)
        sh_lines = ["#!/usr/bin/env bash",
                    "# Rerun the exact configuration that produced this run dir.",
                    "set -euo pipefail",
                    "",
                    f"{base} \\"]
        for flag, value in flag_items:
            sh_lines.append(f"  {flag} {value} \\")
        if sh_lines[-1].endswith("\\"):
            sh_lines[-1] = sh_lines[-1].rstrip(" \\")
        (self.root / "reproduce.sh").write_text("\n".join(sh_lines) + "\n",
                                                encoding="utf-8")

        # Windows batch (one flag per line with ^)
        bat_lines = ["@echo off",
                    "REM Rerun the exact configuration that produced this run dir.",
                    ""]
        if flag_items:
            bat_lines.append(f"{base} ^")
            for i, (flag, value) in enumerate(flag_items):
                cont = " ^" if i < len(flag_items) - 1 else ""
                bat_lines.append(f"  {flag} {value}{cont}")
        else:
            bat_lines.append(base)
        (self.root / "reproduce.bat").write_text("\r\n".join(bat_lines) + "\r\n",
                                                 encoding="utf-8")


def format_prompt_txt(messages: list, image_refs: Optional[list[str]] = None) -> str:
    """Render an OpenAI-style ``messages`` list as human-readable text
    with ``=== <ROLE> ===`` section headers and image references.

    See :meth:`RunLogger.write_prompt` for the format specification.
    Exposed at module level for ad-hoc use by task code that wants to
    build the string without going through a logger.
    """
    refs = list(image_refs or [])
    img_idx = 0
    out: list[str] = []
    for msg in messages:
        role = str(msg.get("role", "")).upper()
        header = (f"=== {role} PROMPT ===" if role == "SYSTEM"
                  else f"=== {role} MESSAGE ===")
        out.append(header)
        content = msg.get("content", "")
        if isinstance(content, str):
            out.append(content)
        else:
            for part in content or []:
                ptype = part.get("type")
                if ptype == "text":
                    out.append(part.get("text", ""))
                elif ptype == "image_url":
                    ref = refs[img_idx] if img_idx < len(refs) else "<inline data URL>"
                    out.append(f"=== IMAGE: {ref} ===")
                    img_idx += 1
                else:
                    out.append(f"<unknown part type: {ptype}>")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


__all__ = ["RunLogger", "task_path_from_module", "format_prompt_txt"]
