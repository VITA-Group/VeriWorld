import ast,asyncio,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from veriworld.common.api_receipt import save_api_receipt

class PhotoApiFailureTests(unittest.TestCase):
    def test_api_errors_abort_in_all_eight_wrappers(self):
        root=Path(__file__).resolve().parents[1]/'veriworld/benchmark/photoreal_grounding'
        tested=0
        for p in root.rglob('task.py'):
            tree=ast.parse(p.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if not isinstance(node,ast.Try) or 'vlm.chat' not in ast.unparse(node):continue
                if not any(isinstance(x,ast.Call) and getattr(x.func,'id',None)=='save_api_receipt' for h in node.handlers for x in ast.walk(h)):continue
                tested+=1
                function=ast.AsyncFunctionDef(name='exercise',args=ast.arguments(posonlyargs=[],args=[],kwonlyargs=[],kw_defaults=[],defaults=[]),body=[node],decorator_list=[])
                code=compile(ast.fix_missing_locations(ast.Module(body=[function],type_ignores=[])),str(p),'exec')
                for error in (asyncio.TimeoutError(),RuntimeError('provider unavailable'),ValueError('model identity mismatch')):
                    with self.subTest(path=str(p),error=type(error).__name__),tempfile.TemporaryDirectory() as tmp:
                        def fail(*args):raise error
                        env=dict(asyncio=asyncio,vlm=SimpleNamespace(chat=fail,model='exact'),messages=[],workspace=Path(tmp),round_idx=1,save_api_receipt=save_api_receipt,VLM_TIMEOUT_S=2,VLM_ROUND_TIMEOUT_S=2)
                        exec(code,env)
                        with self.assertRaisesRegex(RuntimeError,'unscored'):asyncio.run(env['exercise']())
                        marker=json.loads((Path(tmp)/'unscored_error.json').read_text())
                        self.assertFalse(marker['valid_scored_episode'])
                        self.assertFalse((Path(tmp)/'episode_summary.json').exists())
        self.assertEqual(tested,8)

if __name__=='__main__':unittest.main()
