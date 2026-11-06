"""Portable CLI replay recipes. Credentials remain in the user's private config."""
import json
from pathlib import Path
import subprocess
import sys

_SWITCHES = {'attach', 'no_cleanup', 'random_view', 'random_spawn', 'skip_gt_check'}
_ALIASES = {'max_balls_override': 'max_balls'}


def argument_vector(module, arguments):
    argv = ['-m', module]
    for key, value in arguments.items():
        if value is None or key == 'log_level':
            continue
        flag = '--' + _ALIASES.get(key, key).replace('_', '-')
        if key in _SWITCHES:
            if value is True:
                argv.append(flag)
            elif value is not False:
                raise ValueError('Expected a boolean switch: ' + key)
            continue
        if isinstance(value, bool):
            value = 'true' if value else 'false'
        elif isinstance(value, (list, tuple)):
            value = ','.join(str(item) for item in value)
        argv.extend([flag, str(value)])
    return argv


def write_recipe(directory, module, arguments, *, requested_model=None):
    directory = Path(directory)
    recipe = {'module': module, 'arguments': arguments,
              'requested_model': requested_model,
              'instructions': 'Run from the installed release directory. Supply --configs, --build and --output-root overrides for your machine. --dry-run prints argv without launching anything.'}
    (directory / 'reproduce.json').write_text(json.dumps(recipe, indent=2, default=str), encoding='utf-8')
    (directory / 'reproduce.py').write_text(
        'from pathlib import Path\nfrom veriworld.common.reproduce import run_recipe\n'
        'if __name__ == "__main__":\n'
        '    raise SystemExit(run_recipe(Path(__file__).with_name("reproduce.json")))\n', encoding='utf-8')
    (directory / 'reproduce.bat').write_text(
        '@echo off\r\npython "%~dp0reproduce.py" %*\r\n', encoding='utf-8')
    (directory / 'reproduce.sh').write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\n'
        'python "$(dirname -- "$0")/reproduce.py" "$@"\n', encoding='utf-8')


def replay_argv(recipe, extra):
    arguments = dict(recipe['arguments'])
    # Overrides also inform config-name resolution; argparse takes the last value.
    for flag in ('configs', 'model', 'models'):
        for index, token in enumerate(extra):
            if token == '--' + flag and index + 1 < len(extra):
                arguments[flag] = extra[index + 1]
            elif token.startswith('--' + flag + '='):
                arguments[flag] = token.split('=', 1)[1]
    selector = 'models' if recipe['module'] == 'veriworld.scripts.run_parallel' else 'model'
    requested = recipe.get('requested_model')
    if requested is not None and not arguments.get(selector):
        config_path = Path(arguments.get('configs', 'model_configs.json'))
        rows = json.loads(config_path.read_text(encoding='utf-8'))
        matches = [row['name'] for row in rows
                   if row.get('name') == requested or row.get('model') == requested]
        if len(matches) != 1:
            raise ValueError('Select one explicit --' + selector + ' configuration; the recorded model is missing or ambiguous.')
        arguments[selector] = matches[0]
    return argument_vector(recipe['module'], arguments) + list(extra)


def run_recipe(path):
    recipe = json.loads(Path(path).read_text(encoding='utf-8'))
    extra = sys.argv[1:]
    dry = '--dry-run' in extra
    extra = [item for item in extra if item != '--dry-run']
    argv = [sys.executable] + replay_argv(recipe, extra)
    if dry:
        print(json.dumps(argv, indent=2))
        return 0
    return subprocess.call(argv)
