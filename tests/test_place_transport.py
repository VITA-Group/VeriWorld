"""Infrastructure failures must not become placement scores or stale visual turns."""
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, Mock, patch

from veriworld.benchmark.interactive_control.place_on_bottom import _episode, _episode_batch
from veriworld.benchmark.interactive_control.place_on_bottom import _cli


class PlaceTransportTests(unittest.TestCase):
    def check_failure(self, module, kind):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            logger = Mock()
            logger.model_dir.return_value = workspace
            client = Mock(model='mock-model')
            client.timeout = 180
            client.last_finish_reason = ''
            client.last_response = None
            client.chat.return_value = '{"cmd":"move_to","label":"PLACE_OBJ_0","to":[0,0,0]}'
            shot = workspace/'initial.png'
            shot.write_bytes(b'not-read-by-mocked-observation')
            captures = [shot, None] if kind=='stale_image' else [None]
            async def observe(engine, params, previous):
                refs = [previous.name] if previous and previous.exists() else []
                state = [] if kind=='missing_state' else [{}]*params['n_total_objects']
                return [], refs, {'state':state}
            if kind=='api_error':
                client.chat.side_effect = RuntimeError('simulated unavailable provider')
            is_batch = module is _episode_batch
            harness = ('vision_batch' if is_batch else 'vision') if kind in ('missing_image','stale_image') else 'symbolic'
            run = module.run_episode_batch if is_batch else module.run_episode
            verify = AsyncMock()
            with patch.object(module,'open_level_and_setup',AsyncMock()), \
                 patch.object(module,'take_screenshot',AsyncMock(side_effect=captures)), \
                 patch.object(module,'settle_and_verify',verify), \
                 patch.object(module,'_apply_action',AsyncMock(return_value={'cmd':'move_to'})), \
                 patch.object(module.asyncio,'sleep',AsyncMock()):
                with self.assertRaises(RuntimeError):
                    asyncio.run(run(object(),0,client,logger,harness_id=harness,
                        system_prompt='Test',observation_fn=observe,package='qualification',max_rounds=2))
            verify.assert_not_awaited()
            self.assertFalse((workspace/'episode_summary.json').exists())
            self.assertEqual(client.chat.call_count, int(kind in ('api_error','stale_image')))
            if kind=='api_error':
                self.assertTrue((workspace/'round_001_transport_error.json').is_file())

    def test_failures_remain_unscored_in_both_action_spans(self):
        for module in (_episode,_episode_batch):
            for kind in ('missing_image','missing_state','api_error','stale_image'):
                with self.subTest(module=module.__name__,kind=kind):
                    self.check_failure(module,kind)

    def test_cli_defaults_match_retained_run_budgets(self):
        expected = {'vision':8, 'symbolic':8, 'combined':8,
                    'vision_batch':3, 'symbolic_batch':3,
                    'symbolic_padded':6, 'symbolic_padded_natural':6,
                    'symbolic_shuffled':8}
        for arm, rounds in expected.items():
            captured = []
            async def record(args, package):
                captured.append(args)
            with self.subTest(arm=arm), patch.object(_cli, 'run', record), \
                 patch('sys.argv', ['qualification','--attach']):
                _cli.main('veriworld.benchmark.interactive_control.place_on_bottom.harness_'+arm)
            self.assertEqual(captured[0].max_rounds,rounds)
            self.assertEqual(captured[0].max_total_actions,6)
            self.assertEqual(captured[0].batch_cap,5)
            self.assertEqual(captured[0].pad_words,750)

    def test_client_deadline_is_not_cancelled_by_shorter_outer_wait(self):
        for module in (_episode, _episode_batch):
            for client_timeout in (60, 600):
                with self.subTest(module=module.__name__, client_timeout=client_timeout), TemporaryDirectory() as temp:
                    workspace = Path(temp)
                    logger = Mock()
                    logger.model_dir.return_value = workspace
                    client = Mock(model='mock-model', timeout=client_timeout)
                    client.chat.return_value = '{"cmd":"stop"}'
                    observed = []
                    async def observe(engine, params, previous):
                        return [], [], {'state':[{}]*params['n_total_objects']}
                    async def fail_wait(future, timeout):
                        observed.append(timeout)
                        future.cancel()
                        raise asyncio.TimeoutError()
                    run = module.run_episode_batch if module is _episode_batch else module.run_episode
                    verify = AsyncMock()
                    with patch.object(module,'open_level_and_setup',AsyncMock()), \
                         patch.object(module,'take_screenshot',AsyncMock(return_value=None)), \
                         patch.object(module,'settle_and_verify',verify), \
                         patch.object(module.asyncio,'wait_for',fail_wait):
                        with self.assertRaises(asyncio.TimeoutError):
                            asyncio.run(run(object(),0,client,logger,harness_id='symbolic',
                                system_prompt='Test',observation_fn=observe,package='qualification',max_rounds=2))
                    self.assertEqual(observed,[max(180, client_timeout)])
                    self.assertFalse((workspace/'episode_summary.json').exists())
                    self.assertTrue((workspace/'round_001_transport_error.json').is_file())
                    verify.assert_not_awaited()


if __name__=='__main__':
    unittest.main()
