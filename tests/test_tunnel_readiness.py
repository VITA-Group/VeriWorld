import asyncio,json,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock,patch
from veriworld.benchmark.interactive_control.tunnel_nav3d.harness_structured import _common as c

class TunnelReadiness(unittest.TestCase):
 def test_missing_collision_never_releases_a_frame(self):
  state={'labels':['MZ_Floor','MZ_Start','MZ_Goal','MZ_VoxelCeiling','MZ_Walls_mat24'],'pawn':[200,200,670],'floor':None,'wall':None}
  engine=type('Engine',(),{})();engine.python_exec=AsyncMock(return_value={'result':'TUNNEL_READY '+json.dumps(state)})
  with TemporaryDirectory() as td,patch('veriworld.common.screenshot.capture_runtime_screenshot',AsyncMock()) as capture,patch.object(c.asyncio,'sleep',AsyncMock()):
   with self.assertRaises(TimeoutError):asyncio.run(c.wait_scene_ready(engine,{'cell_size':200,'grid_rows':11},Path(td),timeout=.005))
   capture.assert_not_awaited()
   self.assertFalse(json.loads((Path(td)/'scene_ready.json').read_text())['ready'])
 def test_ready_scene_drains_exactly_two_frames(self):
  state={'labels':['MZ_Floor','MZ_Start','MZ_Goal','MZ_VoxelCeiling','MZ_Walls_mat24'],'pawn':[200,200,670],'floor':[200,200,501],'wall':[-20,1100,700]}
  engine=type('Engine',(),{})();engine.python_exec=AsyncMock(return_value={'result':'TUNNEL_READY '+json.dumps(state)})
  with TemporaryDirectory() as td,patch('veriworld.common.screenshot.capture_runtime_screenshot',AsyncMock()) as capture:
   asyncio.run(c.wait_scene_ready(engine,{'cell_size':200,'grid_rows':11},Path(td)))
   self.assertEqual(capture.await_count,2)
   self.assertTrue(json.loads((Path(td)/'scene_ready.json').read_text())['ready'])
