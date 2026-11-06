import asyncio
import importlib
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from veriworld.common.cli import default_build, select_config
from veriworld.common.vlm import ModelConfig

class ReleaseLauncherTests(unittest.TestCase):
    def test_exact_model_selection_and_ambiguous_configuration(self):
        a=ModelConfig('first','model-a','unused','https://test.invalid')
        b=ModelConfig('second','model-b','unused','https://test.invalid')
        self.assertIs(select_config([a,b],'second'),b)
        self.assertIs(select_config([a,b],None),a)
        for configs,name in [([a,b],'typo'),([],None),([a,a],'first')]:
            with self.assertRaises(ValueError):select_config(configs,name)

    def test_all_repaired_launchers_reject_wrong_model_before_external_actions(self):
        root=Path(__file__).resolve().parents[1]
        paths=[p for p in (root/'veriworld').rglob('__main__.py') if 'cfg = select_config(' in p.read_text(encoding='utf-8')]
        self.assertGreaterEqual(len(paths),20)
        for p in paths:
            module=importlib.import_module('.'.join(p.relative_to(root).with_suffix('').parts))
            parse='_args' if hasattr(module,'_args') else '_parse_args'
            args=SimpleNamespace(model='typo',configs=Path('unused'),log_level='ERROR')
            with self.subTest(module=module.__name__),patch.object(module,parse,return_value=args),patch.object(module,'load_configs',return_value=[ModelConfig('real','pinned','unused','https://test.invalid')]),patch.object(module.VLMClient,'from_config',side_effect=AssertionError('client must not be constructed')):
                with self.assertRaisesRegex(ValueError,'Unknown model'):asyncio.run(module._amain())

    def test_photoreal_launcher_leaves_other_processes_alone_and_stops_owned_engine(self):
        root=Path(__file__).resolve().parents[1]
        paths=list((root/'veriworld/benchmark/photoreal_grounding').rglob('__main__.py'))
        for p in paths:
            module=importlib.import_module('.'.join(p.relative_to(root).with_suffix('').parts))
            engine=Mock();engine.stop=AsyncMock()
            with self.subTest(module=module.__name__),patch('sys.argv',[str(p),'--model','real','--build',str(p)]),patch.object(module,'load_configs',return_value=[ModelConfig('real','pinned','unused','https://test.invalid')]),patch.object(module.VLMClient,'from_config',return_value=Mock()),patch.object(module,'RunLogger'),patch.object(module.InteractiveEngine,'launch',new=AsyncMock(return_value=engine)),patch.object(module,'run',new=AsyncMock(side_effect=RuntimeError('simulated task failure'))),patch('subprocess.run',side_effect=AssertionError('global process cleanup forbidden')):
                with self.assertRaisesRegex(RuntimeError,'simulated task failure'):asyncio.run(module._amain())
                engine.stop.assert_awaited_once()

    def test_runtime_default_is_explicit_and_cross_platform(self):
        with patch.dict(os.environ,{},clear=True):self.assertIsNone(default_build())
        with patch.dict(os.environ,{'VERIWORLD_BUILD':'/runtime/demo1.sh'}):self.assertEqual(default_build(),Path('/runtime/demo1.sh'))

if __name__=='__main__':unittest.main()
