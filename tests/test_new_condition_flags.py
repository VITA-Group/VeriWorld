"""Routing tests for the 2026-11 additions: PlaceOnBottom pure-visual condition
and the MazeNavFPS equal primitive-move-budget protocol. These exercise
``run_task.arguments`` (the same resolution ``--dry-run`` prints) without a
runtime or API call."""
import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("run_task_mod", ROOT / "run_task.py")
run_task = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_task)


def ns(**over):
    base = dict(task="PlaceOnBottom", seed=0, model="example-model",
                configs=Path("model_configs.json"), port=19001, condition="V",
                protocol=None, move_budget=None, rounds=None, variant="standard",
                grid_size=5, level="living_room", target="find_wooden_spoon_pot_view",
                smoke=False)
    base.update(over)
    return SimpleNamespace(**base)


def resolve(**over):
    return run_task.arguments(ns(**over), build=Path("build.exe"), out=Path("out"))


def after(args, flag):
    return args[args.index(flag) + 1]


class NewFlagTests(unittest.TestCase):
    def test_place_v_pure_routes_to_vision_harness(self):
        module, _ = resolve(task="PlaceOnBottom", condition="V-pure")
        self.assertTrue(module.endswith("place_on_bottom.harness_vision"), module)
        # same harness module as plain V — the transform is applied by the worker, not a new harness
        self.assertEqual(module, resolve(task="PlaceOnBottom", condition="V")[0])

    def test_boxfold_v_pure_routes_to_vision_harness(self):
        module, _ = resolve(task="BoxFold", condition="V-pure")
        self.assertTrue(module.endswith("box_fold.harness_vision"), module)
        self.assertEqual(module, resolve(task="BoxFold", condition="V")[0])

    def test_lightwar_v_pure_routes_to_visual_only_harness(self):
        module, _ = resolve(task="LightWar", condition="V-pure")
        self.assertTrue(module.endswith("light_war.harness_arc_visual_only"), module)
        self.assertEqual(module, resolve(task="LightWar", condition="V")[0])

    def test_v_pure_rejected_off_pure_visual_tasks(self):
        # V-pure is defined only for PlaceOnBottom, BoxFold and LightWar.
        for task in ("MovingShooter", "MazeNavFPS", "TunnelNav3D", "Billiards", "DropToTarget"):
            with self.assertRaises(ValueError):
                resolve(task=task, condition="V-pure")

    def test_maze_batch_eq_defaults_budget_by_grid(self):
        for grid, k in ((3, "125"), (4, "170"), (5, "210")):
            module, args = resolve(task="MazeNavFPS", protocol="batch-eq", grid_size=grid)
            self.assertTrue(module.endswith("harness_structured.vp_bf_eq"), module)
            self.assertEqual(after(args, "--move-budget"), k)
            self.assertEqual(after(args, "--max-steps"), k)
            self.assertEqual(after(args, "--grid-size"), str(grid))

    def test_maze_single_uses_vp_sa_and_no_move_budget(self):
        module, args = resolve(task="MazeNavFPS", protocol="single", grid_size=3)
        self.assertTrue(module.endswith("harness_structured.vp_sa"), module)
        self.assertEqual(after(args, "--max-steps"), "125")
        self.assertNotIn("--move-budget", args)

    def test_maze_move_budget_override(self):
        module, args = resolve(task="MazeNavFPS", protocol="batch-eq", grid_size=4, move_budget=99)
        self.assertEqual(after(args, "--move-budget"), "99")
        self.assertEqual(after(args, "--max-steps"), "99")

    def test_maze_batch_eq_smoke_is_one_turn_with_budget(self):
        _module, args = resolve(task="MazeNavFPS", protocol="batch-eq", grid_size=5, smoke=True)
        self.assertEqual(after(args, "--max-steps"), "1")
        self.assertEqual(after(args, "--move-budget"), "210")

    def test_protocol_rejected_off_maze(self):
        with self.assertRaises(ValueError):
            resolve(task="PlaceOnBottom", protocol="batch-eq")

    def test_plain_maze_quickstart_unchanged(self):
        module, args = resolve(task="MazeNavFPS", smoke=True)
        self.assertTrue(module.endswith("harness_structured.vp_bf"), module)
        self.assertNotIn("--move-budget", args)
        self.assertEqual(after(args, "--max-steps"), "1")


if __name__ == "__main__":
    unittest.main()
