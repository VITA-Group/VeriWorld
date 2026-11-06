"""Transport failures must never produce navigation scores or reuse stale frames."""
import asyncio
import importlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, Mock, patch
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
MODULES = [str(p.relative_to(ROOT).with_suffix('')).replace('\\', '.').replace('/', '.')
           for family in ('mazenavfps', 'tunnel_nav3d')
           for p in sorted((ROOT/'veriworld/benchmark/interactive_control'/family/'harness_structured').glob('*/task.py'))]


class NavigationTransportTests(unittest.TestCase):
    def test_api_timeout_and_missing_feedback_leave_no_summary(self):
        for name in MODULES:
            module = importlib.import_module(name)
            for failure in ('api_error', 'movement_timeout', 'missing_feedback'):
                with self.subTest(module=name, failure=failure), TemporaryDirectory() as temp:
                    root = Path(temp)
                    logger = Mock(); logger.model_dir.return_value = root
                    shot = root/'image.png'; Image.new('RGB', (8, 8), (32, 64, 96)).save(shot)
                    client = Mock(model='transport-test')
                    client.chat.return_value = '{"moves":[{"cmd":"turn","degrees":90}]}'
                    if failure == 'api_error': client.chat.side_effect = RuntimeError('simulated API outage')
                    async def execute(code):
                        if "print(getattr(builtins, '_NAV_DONE'" in code:
                            stdout = 'False' if failure == 'movement_timeout' else 'True'
                        elif "print('NAVLOG:'" in code: stdout = 'NAVLOG:null'
                        elif 'RAYCAST:' in code: stdout = 'RAYCAST:Front: clear|Back: clear'
                        else: stdout = 'LEVEL_READY'
                        return {'result': {'result': stdout}}
                    engine = Mock(); engine.python_exec = AsyncMock(side_effect=execute)
                    with patch.object(module.asyncio, 'sleep', AsyncMock()), \
                         patch.object(module, 'wait_scene_ready', AsyncMock(), create=True), \
                         patch.object(module, 'take_screenshot', AsyncMock(return_value=shot), create=True):
                        with self.assertRaises((RuntimeError, TimeoutError)):
                            asyncio.run(module.run(engine, 0, client, logger, max_steps=1))
                    self.assertFalse((root/'episode_summary.json').exists())
                    self.assertEqual(client.chat.call_count, 1)
                    if failure != 'missing_feedback':
                        receipt=json.loads((root/'step_001_transport_error.json').read_text())
                        self.assertFalse(receipt['scored'])

    def test_feedback_validation_and_instance_capture(self):
        for family in ('mazenavfps', 'tunnel_nav3d'):
            common=importlib.import_module('veriworld.benchmark.interactive_control.'+family+'.harness_structured._common')
            for data in ('', 'NAVLOG:null', 'NAVLOG:[null]', 'NAVLOG:[]', 'NAVLOG:{}', 'NAVLOG:{bad'):
                with self.subTest(family=family, data=data), self.assertRaises(RuntimeError):
                    common.parse_navlog(data)
            self.assertEqual(common.parse_navlog('NAVLOG:{"cmd":"turn","yaw":90}'), [{'cmd':'turn','yaw':90}])
            for envelope in ({'error':'failed'}, {'result':{'success':False}},
                             {'result':json.dumps({'error':'failed'})}, {'result':{'result':'Traceback (most recent call last):'}}):
                with self.subTest(family=family, envelope=envelope), self.assertRaises(RuntimeError):
                    common.unwrap(envelope)
            self.assertEqual(common.unwrap({'result':json.dumps({'result':'True'})}), 'True')
            with patch('veriworld.common.screenshot.capture_runtime_screenshot', AsyncMock(return_value=Path('new.png'))) as capture:
                engine=object(); workspace=Path('own-episode')
                self.assertEqual(asyncio.run(common.take_screenshot(engine,'step_001',Path('old-shared-folder'),workspace)),Path('new.png'))
                capture.assert_awaited_once_with(engine,'step_001',workspace)


if __name__ == '__main__': unittest.main()
