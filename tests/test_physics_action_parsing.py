import math
import unittest

from veriworld.benchmark.computational_physics.paper_tasks.action_parsing import surface_source, curvy_action


def evaluate(source, x=2.0, y=3.0):
    env = {'math': math, 'SURFACE_Z': 395.0, '__builtins__': {'abs': abs, 'min': min, 'max': max}}
    exec(compile(surface_source(source), '<test>', 'exec'), env)
    return env['surface_height'](x, y)


class ActionParsing(unittest.TestCase):
    def test_common_numeric_python(self):
        self.assertEqual(evaluate('import math\ndef surface_height(x,y):\n a,b=x,y\n a+=b\n return a+math.sqrt(4)'), 7.0)

    def test_inner_import_and_hyperbolic_function(self):
        self.assertAlmostEqual(evaluate('def surface_height(x,y):\n import math\n return math.tanh(x)'), math.tanh(2))

    def test_numeric_import_aliases(self):
        self.assertEqual(evaluate('import math as m\nfrom math import sqrt as root\ndef surface_height(x,y):\n return root(4)+m.floor(y)'), 5.0)

    def test_constants_cannot_overwrite_runtime_globals(self):
        self.assertEqual(evaluate('import math\nGRAVITY=2\ndef surface_height(x,y):\n return GRAVITY*x+y'), 7.0)

    def test_markdown_wrapper(self):
        self.assertEqual(evaluate('Explanation\n```python\ndef surface_height(x,y):\n return x+y\n```\nDone.'), 5.0)

    def test_preserves_final_code_block_convention(self):
        self.assertEqual(evaluate('```python\ndef surface_height(x,y):\n return x\n```\n```python\ndef surface_height(x,y):\n return y\n```'), 3.0)

    def test_rejects_unsafe_or_non_numeric_python(self):
        bad = [
            'import os\ndef surface_height(x,y):\n return 1',
            'def surface_height(x,y):\n return open("file")',
            'def surface_height(x,y):\n return x.__class__',
            'def surface_height(x,y):\n while True: pass\n return x',
            'def surface_height(x,y):\n return surface_height(x,y)',
            'def surface_height(x,y):\n math.pi=0\n return 1',
            'def surface_height(x,y):\n return "a"*1000000000',
            'def surface_height(x,y):\n return 2**1000000000',
            'def surface_height(x,y):\n x **= 1000000000\n return x',
            'from math import *\ndef surface_height(x,y):\n return x',
            'def surface_height(x,y=1):\n return x',
        ]
        for source in bad:
            with self.subTest(source=source), self.assertRaises(ValueError):
                surface_source(source)

    def test_json_prose_and_fences_preserve_numbers(self):
        for text in ['{"v_angle":-1.2,"v_speed":280}', 'Reasoning.\n{"v_angle":-1.2,"v_speed":280}', '```json\n{"v_angle":-1.2,"v_speed":280}\n```']:
            with self.subTest(text=text):
                action, angle, speed = curvy_action(text, 500)
                self.assertEqual((angle, speed), (-1.2, 280.0))
                self.assertEqual(action['v_speed'], 280)

    def test_json_rejects_ambiguity_truncation_and_invalid_values(self):
        bad = ['{"v_angle":0,"v_speed":1} or {"v_angle":2,"v_speed":3}',
               '{"v_angle":0,"v_speed":', 'No attempts left.',
               '{"v_angle":NaN,"v_speed":1}', '{"v_angle":true,"v_speed":1}',
               '{"v_angle":0,"v_speed":501}', '{"v_angle":0,"v_speed":-1}',
               '{"v_angle":0,"v_angle":1,"v_speed":1}',
               '[{"v_angle":0,"v_speed":1}]']
        for text in bad:
            with self.subTest(text=text), self.assertRaises(ValueError):
                curvy_action(text, 500)


if __name__ == '__main__':
    unittest.main()
