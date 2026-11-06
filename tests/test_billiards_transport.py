"""Check missing observations, outages and partial verifier records stay unscored."""
import asyncio
import importlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, Mock, patch
from PIL import Image
from veriworld.benchmark.interactive_control.billiards._shared import parse_verify_log
from veriworld.benchmark.interactive_control.billiards.harness_video._common import harvest_video
from veriworld.benchmark.interactive_control.billiards._transport import require_state

ROOT=Path(__file__).resolve().parents[1]/'veriworld/benchmark/interactive_control/billiards'
ARMS=sorted(p.parent.name for p in ROOT.glob('harness_*/_common.py'))


class BilliardsTransportTests(unittest.TestCase):
    def test_api_and_capture_failures_do_not_write_scores(self):
        for arm in ARMS:
            module=importlib.import_module('veriworld.benchmark.interactive_control.billiards.'+arm+'.shot_code.task')
            for failure in ('api_error','missing_capture'):
                with self.subTest(arm=arm,failure=failure), TemporaryDirectory() as temp:
                    root=Path(temp)
                    (root/'state.json').write_text(json.dumps({'balls':{'cue':[0,0,0]},'alive_target':5,'cue_scratched':False}))
                    logger=Mock();logger.model_dir.return_value=root
                    client=Mock(model='fake-model',name='fake-model')
                    client.chat.side_effect=RuntimeError('simulated unavailable API')
                    client.chat_with_video.side_effect=RuntimeError('simulated unavailable API')
                    engine=Mock();engine.switch_level=AsyncMock();engine.python_exec=AsyncMock(return_value={'result':{'result':''}})
                    async def screenshot(engine,directory,dest):
                        if failure=='missing_capture':raise RuntimeError('simulated missing frame')
                        Image.new('RGB',(8,8),(24,64,32)).save(dest);return dest
                    def video(recording,dest):
                        if failure=='missing_capture':raise RuntimeError('simulated missing video')
                        dest.write_bytes(b'fixture-only-not-decodable');return dest
                    with patch.object(module.asyncio,'sleep',AsyncMock()), \
                         patch.object(module,'harvest_screenshot',AsyncMock(side_effect=screenshot),create=True), \
                         patch.object(module,'harvest_video',video,create=True), \
                         patch.object(module,'stop_recording',AsyncMock(return_value={'full_path':'fixture'}),create=True):
                        with self.assertRaises(RuntimeError):
                            asyncio.run(module.run(engine,0,client,logger,exe=Path('unused'),max_rounds=1))
                    self.assertFalse((root/'episode_summary.json').exists())
                    total=client.chat.call_count+client.chat_with_video.call_count
                    self.assertEqual(total,0 if failure=='missing_capture' else 3)

    def test_settle_timeout_leaves_no_score(self):
        for arm in ('harness_image','harness_video'):
            module=importlib.import_module('veriworld.benchmark.interactive_control.billiards.'+arm+'.shot_code.task')
            with self.subTest(arm=arm), TemporaryDirectory() as temp:
                root=Path(temp);logger=Mock();logger.model_dir.return_value=root
                client=Mock(model='fake',name='fake',last_response=None)
                client.chat.return_value=client.chat_with_video.return_value='```json\n{"angle_deg":0,"speed_cm_s":300}\n```'
                engine=Mock();engine.switch_level=AsyncMock();engine.python_exec=AsyncMock(return_value={'result':{'result':''}})
                async def image(engine,directory,dest):Image.new('RGB',(8,8)).save(dest);return dest
                def video(recording,dest):dest.write_bytes(b'fixture');return dest
                with patch.object(module.asyncio,'sleep',AsyncMock()), \
                     patch.object(module,'harvest_screenshot',AsyncMock(side_effect=image),create=True), \
                     patch.object(module,'harvest_video',video,create=True), \
                     patch.object(module,'stop_recording',AsyncMock(return_value={'full_path':'fixture'}),create=True):
                    with self.assertRaises(RuntimeError):
                        asyncio.run(module.run(engine,0,client,logger,exe=Path('unused'),max_rounds=1,settle_timeout=0))
                self.assertFalse((root/'episode_summary.json').exists())

    def test_missing_counts_are_not_all_down(self):
        for text in ('', '# RESULT: ALL_DOWN', '# alive_target=0', '# alive_target=0\n# RESULT: GARBAGE'):
            with self.subTest(text=text), self.assertRaises(RuntimeError):parse_verify_log(text)
        self.assertEqual(parse_verify_log('# alive_target=0\n# RESULT: ALL_DOWN'),('ALL_DOWN',0,False))
        self.assertEqual(parse_verify_log('# alive_target=4\n# cue_scratched=yes\n# RESULT: ONGOING'),('ONGOING',4,True))

    def test_video_never_falls_back_to_newest_neighbor(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);neighbor=root/'other-worker.h264';neighbor.write_bytes(b'another episode')
            for receipt in (None,{}, {'full_path':str(root/'missing.h264')}):
                with self.subTest(receipt=receipt), self.assertRaises(RuntimeError):
                    harvest_video(receipt,root/'result.mp4')
            self.assertEqual(neighbor.read_bytes(),b'another episode')
            self.assertFalse((root/'result.mp4').exists())

    def test_missing_or_invalid_structured_channel_is_not_sent(self):
        with TemporaryDirectory() as temp:
            path=Path(temp)/'state.json'
            with self.assertRaises(RuntimeError):require_state(path)
            for state in ({},{'balls':{}}, {'balls':{'cue':[0,0]},'alive_target':1,'cue_scratched':False}):
                path.write_text(json.dumps(state))
                with self.subTest(state=state),self.assertRaises(RuntimeError):require_state(path)
            valid={'balls':{'cue':[1,2,3]},'alive_target':5,'cue_scratched':False}
            path.write_text(json.dumps(valid))
            self.assertEqual(require_state(path),valid)


if __name__=='__main__':unittest.main()
