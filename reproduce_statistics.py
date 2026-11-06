"""Recompute supporting summaries from the shipped CSVs. Python standard library only.

Run: python reproduce_statistics.py
Writes reproduced_statistics.json next to this script. No network access or model calls.
"""
import collections, csv, json, math, statistics
from pathlib import Path
B=Path(__file__).resolve().parent
def read(name):
    with (B/'data'/name).open(encoding='utf8',newline='') as f:return list(csv.DictReader(f))
def wilson(k,n):
    z=1.959963984540054;p=k/n;den=1+z*z/n
    c=(p+z*z/(2*n))/den;h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [max(0,c-h),min(1,c+h)]
groups=collections.defaultdict(list)
for r in read('episode_metrics.csv'):groups[r['task'],r['model'],r['condition']].append(r)
core=[];macros=collections.defaultdict(list)
for (t,m,c),rs in sorted(groups.items()):
    k=sum(int(r['success']) for r in rs);n=len(rs)
    core.append(dict(task=t,model=m,condition=c,successes=k,n=n,rate=k/n,wilson95=wilson(k,n)))
    macros[t,c].append(k/n)
continuous=[];groups=collections.defaultdict(list)
for r in read('continuous_metrics.csv'):groups[r['task'],r['condition']].append(r)
for (t,c),rs in sorted(groups.items()):
    errors=[float(r['error_cm']) for r in rs if r['error_cm']!='']
    rounds=[int(r['rounds']) for r in rs if int(r['success'])]
    faces=[float(r['faces_correct_fraction']) for r in rs if r['faces_correct_fraction']!='']
    continuous.append(dict(task=t,condition=c,n=len(rs),metric_n=len(errors),median_error_cm=statistics.median(errors) if errors else None,
        median_success_rounds=statistics.median(rounds) if rounds else None,success_n=len(rounds),mean_face_completion=statistics.mean(faces) if faces else None))
groups=collections.defaultdict(list)
for r in read('prompt_and_span_controls.csv'):groups[r['task'],r['harness'],r['model']].append(r)
controls=[dict(task=t,harness=h,model=m,n=len(rs),successes=sum(int(r['success']) for r in rs)) for (t,h,m),rs in sorted(groups.items())]
control_rows=read('prompt_and_span_controls.csv')
lookup={(r['task'],r['harness'],r['model'],int(r['seed'])):r for r in control_rows}
assert len(lookup)==len(control_rows), 'Duplicate control cell'
paired_controls={}
variants={'span':('harness_vision','harness_symbolic','harness_vision_batch','harness_symbolic_batch'),
          'padding':('harness_symbolic','harness_symbolic_padded','harness_symbolic_padded_natural','harness_combined'),
          'serialization':('harness_symbolic','harness_symbolic_shuffled')}
for name,arms in variants.items():
    paired_controls[name]=[]
    for model in sorted({r['model'] for r in control_rows if r['task']=='PlaceOnBottom'}):
        sets=[{seed for task,harness,m,seed in lookup if (task,harness,m)==('PlaceOnBottom',arm,model)} for arm in arms]
        seeds=set.intersection(*sets)
        if not seeds:continue
        ks=[sum(int(lookup['PlaceOnBottom',arm,model,seed]['success']) for seed in seeds) for arm in arms]
        paired_controls[name].append(dict(model=model,n=len(seeds),seeds=sorted(seeds),
             successes=ks,rates=[k/len(seeds) for k in ks]))
unpaired=read('core_unpaired_episodes.csv')
paired_ids={r['record_id'] for r in read('episode_metrics.csv')}
assert not paired_ids.intersection(r['record_id'] for r in unpaired)
assert len({r['record_id'] for r in unpaired})==len(unpaired)
anchors=read('structural_probe.csv');correct=[r for r in anchors if int(r['correct'])];incorrect=[r for r in anchors if not int(r['correct'])]
probe=dict(declared=len(anchors),correct=len(correct),accuracy=len(correct)/len(anchors),
    passed_after_correct=sum(int(r['success']) for r in correct),passed_after_incorrect=sum(int(r['success']) for r in incorrect))
groups=collections.defaultdict(list)
for r in read('ranking_inputs.csv'):groups[r['comparison']].append(r)
ranks=[]
for name,rs in groups.items():
    concordant=discordant=tie_a=tie_b=0
    for i,a in enumerate(rs):
        for b in rs[i+1:]:
            da=float(a['score_a'])-float(b['score_a']);db=float(a['score_b'])-float(b['score_b'])
            if da==0 and db==0:continue
            if da==0:tie_a+=1
            elif db==0:tie_b+=1
            elif da*db>0:concordant+=1
            else:discordant+=1
    n=concordant+discordant;den=math.sqrt((n+tie_a)*(n+tie_b))
    ranks.append(dict(comparison=name,n_models=len(rs),tau_b=(concordant-discordant)/den if den else None))
physics_rows=read('computational_physics_episodes.csv')
physics_groups=collections.defaultdict(list)
seen=set()
for r in physics_rows:
    key=(r['task'],r['model'],r['condition'],int(r['seed']))
    assert key not in seen, key
    seen.add(key)
    physics_groups[key[:3]].append(r)
physics_complete=[]
for model in sorted({r['model'] for r in physics_rows}):
    if all({int(r['seed']) for r in physics_groups[task,model,c]}==seeds
           for task,seeds in [('DropToTarget',{0,1,2}),('CurvyBilliards',{0,2,9})] for c in ('V','S','C')):
        physics_complete.append(model)
physics_cells=[dict(task=t,model=m,condition=c,successes=sum(int(r['success']) for r in rs),n=len(rs),
                    seeds=sorted(int(r['seed']) for r in rs),complete_model_panel=m in physics_complete)
               for (t,m,c),rs in sorted(physics_groups.items())]
physics_pooled=[]
for task in ['DropToTarget','CurvyBilliards']:
    for c in ['V','S','C']:
        selected=[r for r in physics_rows if r['task']==task and r['condition']==c and r['model'] in physics_complete]
        physics_pooled.append(dict(task=task,condition=c,successes=sum(int(r['success']) for r in selected),n=len(selected)))
photo_rows=read('photoreal_episodes.csv')
photo_groups=collections.defaultdict(list)
photo_seen=set()
for r in photo_rows:
    assert r['cell_id'] not in photo_seen
    photo_seen.add(r['cell_id'])
    photo_groups[r['model'],r['task'],r['harness'],r['condition']].append(r)
photo_models=sorted({r['model'] for r in photo_rows})
photo_complete=[]
for model in photo_models:
    if (all(len(photo_groups[model,'ObjectGrounding',h,c])==30 for h in ['grid','single'] for c in ['V','C'])
        and all(len(photo_groups[model,'SceneGrounding',h,'V'])==10 for h in ['walk','teleport'])):
        photo_complete.append(model)
for r in photo_rows:
    assert bool(int(r['complete_model_panel']))==(r['model'] in photo_complete)
photo_cells=[dict(model=m,task=t,harness=h,condition=c,n=len(rs),successes=sum(int(r['success']) for r in rs))
             for (m,t,h,c),rs in sorted(photo_groups.items()) if rs]
photo_pooled=[]
for task,harnesses,conditions in [('ObjectGrounding',['grid','single'],['V','C']),('SceneGrounding',['walk','teleport'],['V'])]:
    for h in harnesses:
        for c in conditions:
            rows=[r for r in photo_rows if r['model'] in photo_complete and (r['task'],r['harness'],r['condition'])==(task,h,c)]
            photo_pooled.append(dict(task=task,harness=h,condition=c,n=len(rows),successes=sum(int(r['success']) for r in rows)))
out=dict(core_per_model=core,core_macro_means=[dict(task=t,condition=c,macro_rate=statistics.mean(vs)) for (t,c),vs in sorted(macros.items())],
    continuous=continuous,controls=controls,paired_controls=paired_controls,
    core_valid_unpaired=dict(records=len(unpaired),by_task=dict(collections.Counter(r['task'] for r in unpaired))),
    structural_probe=probe,rank_stability=ranks,
    computational_physics=dict(cells=physics_cells,complete_models=physics_complete,pooled_complete_models=physics_pooled),
    photoreal=dict(cells=photo_cells,complete_models=photo_complete,pooled_complete_models=photo_pooled))
(B/'reproduced_statistics.json').write_text(json.dumps(out,indent=2)+'\n',encoding='utf8')
print('Reproduced four core panels, continuous outcomes, prompt/span controls, the anchor probe, five ranking comparisons, computational physics, and Photoreal cells.')
