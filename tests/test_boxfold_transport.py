"""Missing state and API outages must not become BoxFold scores."""
import asyncio
import copy
import importlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, Mock, patch
from PIL import Image
from veriworld.benchmark.trial_error_prediction.box_fold import _shared as shared
from veriworld.benchmark.trial_error_prediction.box_fold.generate_params import generate


def closed_states():
    params=generate(0);centers=shared.expected_closed_centers(params)
    return [{'index':f['index'],'label':f['label'],'position':list(centers[f['label']])} for f in params['faces']]


class BoxFoldTransportTests(unittest.TestCase):
    def test_incomplete_state_cannot_pass(self):
        good=closed_states();duplicate=copy.deepcopy(good);duplicate[-1]=duplicate[0]
        nan=copy.deepcopy(good);nan[0]['position'][0]=float('nan')
        for bad in ([],good[:1],duplicate,nan,None):
            with self.subTest(state=bad),self.assertRaises(RuntimeError):shared.verify_states(bad,generate(0))
        self.assertTrue(shared.verify_states(good,generate(0))['passed'])
        good[0]['position'][0]+=1000
        self.assertFalse(shared.verify_states(good,generate(0))['passed'])

    def test_bad_runtime_payload_raises(self):
        for value in (None,{'result':{'error':'disconnected'}},{'success':False}, {'result':'Traceback (most recent call last): bad'}):
            with self.subTest(value=value),self.assertRaises(RuntimeError):shared.unwrap(value)
        for value in ('','BFOLD_STATES:[]','BFOLD_STATES:{broken'):
            with self.subTest(value=value),self.assertRaises(RuntimeError):shared.parse_states(value)

    def run_failure(self,arm,kind):
        module=importlib.import_module('veriworld.benchmark.trial_error_prediction.box_fold.harness_'+arm+'.task')
        with TemporaryDirectory() as temp:
            root=Path(temp);logger=Mock();logger.model_dir.return_value=root
            client=Mock(model='fixture',timeout=180)
            if kind=='api':client.chat.side_effect=RuntimeError('simulated API outage')
            else:client.chat.return_value='```json\n{"cmd":"stop"}\n```'
            async def execute(code):
                output='' if kind=='state' and client.chat.call_count else 'BFOLD_STATES:'+json.dumps(closed_states())
                return {'result':{'result':output}}
            engine=Mock();engine.python_exec=AsyncMock(side_effect=execute)
            async def capture(engine,name,workspace):
                dest=workspace/(name+'.png');Image.new('RGB',(8,8)).save(dest);return dest
            with patch.object(module,'open_level_and_setup',AsyncMock()), \
                 patch.object(module.asyncio,'sleep',AsyncMock()), \
                 patch.object(module,'take_screenshot',AsyncMock(side_effect=capture),create=True):
                with self.assertRaises(RuntimeError):asyncio.run(module.run(engine,0,client,logger,max_rounds=1))
            self.assertFalse((root/'episode_summary.json').exists())

    def test_api_failure_leaves_all_arms_unscored(self):
        for arm in ('vision','symbolic','combined'):
            with self.subTest(arm=arm):self.run_failure(arm,'api')

    def test_lost_state_leaves_all_arms_unscored(self):
        for arm in ('vision','symbolic','combined'):
            with self.subTest(arm=arm):self.run_failure(arm,'state')


if __name__=='__main__':unittest.main()
