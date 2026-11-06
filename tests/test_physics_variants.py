import copy
import json
import math
from pathlib import Path
import unittest
from veriworld.benchmark.computational_physics.paper_tasks.variants import parameters, transform_angle

ROOT=Path(__file__).resolve().parents[1]/'veriworld/benchmark/computational_physics/paper_tasks'

class Variants(unittest.TestCase):
    def test_originals_unchanged(self):
        for task,seeds in [('drop',[0,1,2]),('curvy',[0,2,9])]:
            for seed in seeds:
                old=json.loads((ROOT/task/f'seed{seed}/params.json').read_text())
                saved=copy.deepcopy(old)
                self.assertEqual(parameters(task,old,'standard'),old)
                for variant in ['rotate90','mirror-x','wide-target']:
                    new=parameters(task,old,variant)
                    self.assertEqual(old,saved)
                    self.assertEqual(new['grid_n'],old['grid_n'])
                    self.assertEqual(new['grid_spacing'],old['grid_spacing'])
                    self.assertNotIn('solution',new)
    def test_curved_geometry_and_direction(self):
        p=json.loads((ROOT/'curvy/seed0/params.json').read_text())
        def height(p,x,y):
            return p['surface_z_base']+sum(g['height']*math.exp(-((x-g['cx'])**2+(y-g['cy'])**2)/(2*g['sigma']**2)) for g in p['gaussians'])
        for variant,xy in [('rotate90',lambda x,y:(-y,x)),('mirror-x',lambda x,y:(-x,y))]:
            new=parameters('curvy',p,variant)
            for x,y in [(0,0),(123,-204),(-300,200)]:
                self.assertAlmostEqual(height(p,x,y),height(new,*xy(x,y)),places=9)
            a=.7; b=transform_angle(a,variant)
            x,y=xy(math.cos(a),math.sin(a))
            self.assertAlmostEqual(x,math.cos(b));self.assertAlmostEqual(y,math.sin(b))
    def test_radius_only(self):
        for task,key in [('drop','target_radius'),('curvy',None)]:
            p=json.loads((ROOT/task/'seed0/params.json').read_text())
            n=parameters(task,p,'wide-target')
            self.assertAlmostEqual(n[key] if key else n['target']['radius'],1.25*(p[key] if key else p['target']['radius']))

if __name__=='__main__':unittest.main()
