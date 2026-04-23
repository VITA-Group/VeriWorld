"""Launch a single UE instance for --attach debug workflows.

Reads the build path from ``run_defaults.json`` so there is no second
place to keep it in sync with the orchestrator. Only used by
``example_starters/<task>/1_launch_ue.bat`` — the normal
parallel runs let the orchestrator launch its own UEs.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser(
        description="Launch a UE instance for --attach debugging")
    p.add_argument("--port", type=int, default=9003)
    p.add_argument("--defaults", type=Path, default=Path("run_defaults.json"),
                   help="Path to run_defaults.json")
    p.add_argument("--kind", choices=["interactive", "computational"], default="interactive",
                   help="Which build to launch (default: interactive)")
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=480)
    args = p.parse_args()

    if not args.defaults.exists():
        sys.exit(
            f"[launch_ue] {args.defaults} not found. "
            "Copy run_defaults.example.json and fill in builds.interactive."
        )
    data = json.loads(args.defaults.read_text(encoding="utf-8"))
    builds = data.get("builds", {}) or {}
    build = builds.get(args.kind)
    if not build:
        sys.exit(f"[launch_ue] builds.{args.kind} not set in {args.defaults}")

    exe = Path(build)
    if not exe.exists():
        sys.exit(f"[launch_ue] build not found: {exe}")

    cmd = [
        str(exe), exe.stem,
        "-AudioMixer", f"-WebSocketPort={args.port}",
        "-windowed", f"-ResX={args.width}", f"-ResY={args.height}",
        "-ForceRes", "-nosplash", "-log",
    ]
    print(f"[launch_ue] kind={args.kind}  port={args.port}")
    print(f"[launch_ue] exe={exe}")
    print("[launch_ue] keep this window open, then run 2_run_*.bat with --attach.")
    print()
    subprocess.run(cmd)


if __name__ == "__main__":
    main()
