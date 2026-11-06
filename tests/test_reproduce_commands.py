import argparse
import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

from veriworld.common.logger import RunLogger
from veriworld.common.reproduce import argument_vector, replay_argv

PHOTO='veriworld.benchmark.photoreal_grounding.object_grounding.harness_grid_iterate_offcenter_bboxv2'
BOX='veriworld.benchmark.trial_error_prediction.box_fold.harness_vision'
TUNNEL='veriworld.benchmark.interactive_control.tunnel_nav3d.harness_structured.vp_bf'


class Parsed(BaseException):
    def __init__(self, args): self.args_value=args


def parse_only(module, arguments):
    original=argparse.ArgumentParser.parse_args
    def capture(parser, args=None, namespace=None):
        result=original(parser, args, namespace)
        raise Parsed(vars(result))
    with patch.object(sys,'argv',[module]+arguments), patch.object(argparse.ArgumentParser,'parse_args',capture):
        try: runpy.run_module(module,run_name='__main__')
        except Parsed as caught: return caught.args_value
    raise AssertionError('Parser was not reached')


class ReproduceCommands(unittest.TestCase):
    def test_real_parsers_round_trip_switches_labels_task_and_paths(self):
        cases=[(PHOTO,['--task','find_wooden_spoon_pot_view','--random-view','--attach','--model','my configuration',
                       '--build','C:/runtime with spaces/build.exe','--configs','C:/private configs/models.json']),
               (BOX,['--skip-gt-check','--seed','19','--model','alias']),
               (TUNNEL,['--colorful','false','--seed','2']),
               ('veriworld.scripts.run_parallel',['--task',BOX,'--random-view','--max-balls','2',
                        '--models','one,two','--seeds','0,19','--colorful','false'])]
        for module, argv in cases:
            with self.subTest(module=module):
                args=parse_only(module,argv)
                rebuilt=argument_vector(module,args)
                parsed=parse_only(module,rebuilt[2:])
                self.assertEqual(args,parsed)

    def test_per_model_keeps_actual_config_label_not_run_id(self):
        with tempfile.TemporaryDirectory() as directory:
            logger=RunLogger(directory,19)
            args=parse_only(PHOTO,['--model','my configuration','--random-view','--task','find_black_spatula_pot_view'])
            logger.snapshot_task(PHOTO,resolved_args=args,invocation='single')
            logger.snapshot_model(PHOTO,'provider-id__task__seed19',19,resolved_args={'model':'provider-id'})
            recipe=json.loads((logger.model_dir('provider-id__task__seed19')/'reproduce.json').read_text())
            argv=replay_argv(recipe,[])
            parsed=parse_only(PHOTO,argv[2:])
            self.assertEqual(parsed['model'],'my configuration')
            self.assertEqual(parsed['task'],'find_black_spatula_pot_view')
            self.assertEqual(parsed['seed'],19)
            self.assertTrue(parsed['random_view'])

    def test_batch_replays_one_seed_and_one_config_without_secret_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            config=Path(directory)/'private config.json'
            config.write_text(json.dumps([{'name':'one','model':'provider-a','api_key':'PRIVATE TEST VALUE'},
                                          {'name':'two','model':'provider-b'}]))
            logger=RunLogger(directory,7)
            args=parse_only('veriworld.scripts.run_parallel',['--task',BOX,'--configs',str(config),'--models','one,two','--seeds','0-19'])
            logger.snapshot_task(BOX,resolved_args=args,invocation='parallel')
            logger.snapshot_model(BOX,'provider-a-task-s7',7,resolved_args={'model':'provider-a'})
            path=logger.model_dir('provider-a-task-s7')/'reproduce.json'
            self.assertNotIn('PRIVATE TEST VALUE',path.read_text())
            recipe=json.loads(path.read_text())
            parsed=parse_only('veriworld.scripts.run_parallel',replay_argv(recipe,[])[2:])
            self.assertEqual((parsed['models'],parsed['seeds'],parsed['max_instances']),('one','7',1))

    def test_missing_or_ambiguous_config_fails_before_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            config=Path(directory)/'models.json'
            config.write_text(json.dumps([{'name':'one','model':'same'},{'name':'two','model':'same'}]))
            recipe={'module':BOX,'arguments':{'configs':str(config)},'requested_model':'same'}
            with self.assertRaises(ValueError): replay_argv(recipe,[])
            argv=replay_argv(recipe,['--model','two'])
            self.assertEqual(parse_only(BOX,argv[2:])['model'],'two')

    def test_wrapper_uses_json_argv_and_preserves_special_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            logger=RunLogger(directory,0)
            args={'configs':r'C:/space & amp/100% models.json','build':r'C:/Runtime & Files/runtime.exe','seed':0}
            logger._write_reproducers(BOX,args,'single')
            recipe=json.loads((logger.root/'reproduce.json').read_text())
            argv=replay_argv(recipe,[])
            self.assertEqual(argv[argv.index('--configs')+1],args['configs'])
            self.assertNotIn(args['configs'],(logger.root/'reproduce.bat').read_text())
            self.assertIn('"%~dp0reproduce.py"',(logger.root/'reproduce.bat').read_text())


if __name__=='__main__': unittest.main()
