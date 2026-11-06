"""Transport outages cannot create terminal scored MovingShooter episodes."""
import asyncio,importlib,json,unittest
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock,Mock,patch

class MovingAPIFailureTests(unittest.TestCase):
    def test_all_six_harnesses_preserve_unscored_failure(self):
        for arm in ('vision','combined','vision_frames','combined_frames','symbolic','symbolic_padded'):
            module=importlib.import_module('veriworld.benchmark.trial_error_prediction.moving_shooter.harness_'+arm+'.task')
            for error in (TimeoutError('simulated timeout'),RuntimeError('simulated unavailable API')):
                with self.subTest(arm=arm,error=type(error).__name__),TemporaryDirectory() as temp,ExitStack() as stack:
                    root=Path(temp);logger=Mock();logger.model_dir.return_value=root
                    client=Mock(model='mock-model',timeout=600);client.chat.side_effect=error
                    setup=stack.enter_context(patch.object(module,'open_level_and_setup',AsyncMock()))
                    rollout=stack.enter_context(patch.object(module,'run_round',AsyncMock(return_value={'video_path':None})))
                    stack.enter_context(patch.object(module.asyncio,'sleep',AsyncMock()))
                    if hasattr(module,'_ask_with_frames'):stack.enter_context(patch.object(module,'_ask_with_frames',AsyncMock(side_effect=error)))
                    elif hasattr(module,'_ask_with_video'):stack.enter_context(patch.object(module,'_ask_with_video',AsyncMock(side_effect=error)))
                    with self.assertRaises(type(error)):asyncio.run(module.run(object(),0,client,logger,max_rounds=2))
                    setup.assert_awaited_once()
                    self.assertEqual(rollout.await_count,int(not arm.startswith('symbolic')))
                    summary=json.loads((root/'episode_summary.json').read_text())
                    self.assertEqual(summary['final_status'],'UNFINISHED');self.assertTrue(summary['crashed'])
                    self.assertFalse(json.loads((root/'round_01_transport_error.json').read_text())['scored'])
                    self.assertFalse((root/'round_02_prompt.txt').exists())

    def test_runtime_error_and_timeout_are_unscored(self):
        from veriworld.benchmark.trial_error_prediction.moving_shooter import _shared
        for output,timeout in [('RS_RESULT:ERROR',0.01),('RS_RESULT:NONE',0)]:
            with self.subTest(output=output), TemporaryDirectory() as temp:
                engine=Mock();engine.python_exec=AsyncMock(return_value={'result':{'result':output}})
                with patch.object(_shared.asyncio,'sleep',AsyncMock()), patch.object(_shared,'harvest_video') as harvest:
                    with self.assertRaisesRegex(RuntimeError,'unscored'):
                        asyncio.run(_shared.run_round(engine,{},0,0,0,1,Path(temp),poll_timeout_s=timeout))
                    harvest.assert_not_called()

if __name__=='__main__':unittest.main()
