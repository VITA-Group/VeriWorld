import contextlib,importlib,io,json,unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

class GeneratorIsolationTests(unittest.TestCase):
    def test_parallel_seeds_are_pure_and_match_sequential_values(self):
        modules=['interactive_control.mazenavfps','interactive_control.tunnel_nav3d','interactive_control.billiards','computational_physics.drop_to_target']
        for name in modules:
            m=importlib.import_module('veriworld.benchmark.'+name+'.generate_params')
            seedfile=Path(m.__file__).with_name('params.json');before=seedfile.read_bytes() if seedfile.exists() else None
            with contextlib.redirect_stdout(io.StringIO()):
                expected=[m.generate(seed) for seed in range(20)]
                with patch('builtins.open',side_effect=AssertionError('generator must not read/write files')):
                    with ThreadPoolExecutor(max_workers=5) as pool:actual=list(pool.map(m.generate,range(20)))
            self.assertEqual(actual,expected)
            self.assertEqual(seedfile.read_bytes() if seedfile.exists() else None,before)

if __name__=='__main__':unittest.main()
