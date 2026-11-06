"""Offline validation of the parameter fixtures shipped with this release."""
import contextlib
import hashlib
import importlib
import io
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
def digest(params):
    return hashlib.sha256(json.dumps(params,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def main():
    fixtures=json.loads((ROOT/'release_seed_fixtures.json').read_text(encoding='utf-8'))['fixtures']
    failures=[]
    for fixture in fixtures:
        module=importlib.import_module(fixture['generator'])
        path=Path(module.__file__).resolve()
        if not path.is_relative_to(ROOT):
            raise RuntimeError(f'Generator resolved outside this release: {path}')
        sibling=path.with_name('params.json');before=sibling.read_bytes() if sibling.exists() else None
        with contextlib.redirect_stdout(io.StringIO()):
            first=module.generate(fixture['seed'],**fixture['config'])
            second=module.generate(fixture['seed'],**fixture['config'])
        if digest(first)!=fixture['parameters_sha256'] or digest(second)!=fixture['parameters_sha256']:
            failures.append({'task':fixture['task'],'seed':fixture['seed'],'reason':'parameter mismatch'})
        if (sibling.read_bytes() if sibling.exists() else None)!=before:
            failures.append({'task':fixture['task'],'seed':fixture['seed'],'reason':'generator modified shared params.json'})
    print(json.dumps({'fixtures_checked':len(fixtures),'failures':failures,'api_calls':0,'runtime_launches':0},indent=2))
    if failures:raise SystemExit(1)

if __name__=='__main__':main()
