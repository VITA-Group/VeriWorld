"""Parse unambiguous actions without changing their numerical content.

Only bounded numerical Python is admitted. Math imports are normalized to
the already supplied math module, never executed as arbitrary imports.
"""
from __future__ import annotations

import ast
import json
import math
import re

PARSER_VERSION = 'numeric-actions-v2-20261102'
_FUNCTIONS = {
    'sin', 'cos', 'tan', 'asin', 'acos', 'atan', 'atan2', 'sinh', 'cosh',
    'tanh', 'sqrt', 'exp', 'expm1', 'log', 'log10', 'log2', 'log1p',
    'fabs', 'floor', 'ceil', 'hypot', 'copysign', 'radians', 'degrees',
}
_CONSTANTS = {'pi', 'e', 'tau'}
_FENCE = re.compile(r'^\s*```([^\r\n]*)\r?\n(.*?)^\s*```\s*$', re.M | re.S)


def _python_candidate(response):
    blocks = [body.strip() for language, body in _FENCE.findall(response)
              if language.strip().lower() in ('', 'python', 'py')
              and re.search(r'\bdef\s+surface_height\s*\(', body)]
    # Preserve the published harness's final-code-block convention when a
    # response revises an earlier draft. Never choose by the resulting score.
    return blocks[-1] if blocks else response.strip()


def surface_source(response):
    source = _python_candidate(response)
    tree = ast.parse(source)
    if len(list(ast.walk(tree))) > 500:
        raise ValueError('Surface function is too large')
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    if len(functions) != 1:
        raise ValueError('Return only def surface_height(x, y)')
    fn = functions[0]
    args = fn.args
    if (fn.name != 'surface_height' or
            [a.arg for a in args.posonlyargs + args.args] != ['x', 'y'] or
            args.vararg or args.kwarg or args.kwonlyargs or args.defaults or
            fn.decorator_list or fn.returns or any(a.annotation for a in args.args + args.posonlyargs)):
        raise ValueError('Expected surface_height(x, y) without defaults or decorators')
    aliases = {}
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name != 'math' for a in node.names):
                raise ValueError('Only the math module may be imported')
            for alias in node.names:
                aliases[alias.asname or 'math'] = ('module', 'math')
            imports.append(node)
        elif isinstance(node, ast.ImportFrom):
            if node.module != 'math' or node.level or any(a.name not in _FUNCTIONS | _CONSTANTS for a in node.names):
                raise ValueError('Only explicit numeric math names may be imported')
            for alias in node.names:
                aliases[alias.asname or alias.name] = ('member', alias.name)
            imports.append(node)
    for name in aliases:
        if name.startswith('_') or name in {'x', 'y', 'SURFACE_Z', 'abs', 'min', 'max'}:
            raise ValueError('Math import alias shadows an input or numeric builtin')
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id in set(aliases) | {'math'}:
            raise ValueError('Do not reassign math imports')

    # Module-level numeric constants become local bindings. They cannot
    # overwrite the runtime's physics globals when the function is inserted.
    constants = []
    for node in tree.body:
        if node is fn or node in imports:
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if not isinstance(node, ast.Assign):
            raise ValueError('Only numeric constants may accompany the surface function')
        if any(isinstance(n, ast.Name) and n.id in {'x', 'y'} for n in ast.walk(node)):
            raise ValueError('Top-level constants cannot depend on function arguments')
        constants.append(node)
    fn.body = constants + fn.body
    tree.body = [fn]

    class Normalize(ast.NodeTransformer):
        def visit_Import(self, node):
            return None

        def visit_ImportFrom(self, node):
            return None

        def visit_Name(self, node):
            if node.id not in aliases:
                return node
            kind, name = aliases[node.id]
            replacement = (ast.Name(id='math', ctx=node.ctx) if kind == 'module'
                           else ast.Attribute(value=ast.Name(id='math', ctx=ast.Load()), attr=name, ctx=node.ctx))
            return ast.copy_location(replacement, node)

    tree = ast.fix_missing_locations(Normalize().visit(tree))
    def numeric_target(node):
        return (isinstance(node, ast.Name) or
                isinstance(node, (ast.Tuple, ast.List)) and all(numeric_target(x) for x in node.elts))

    docstrings = {id(n.value) for n in ast.walk(tree)
                  if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                  and isinstance(n.value.value, str)}
    allowed = (ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return,
               ast.Assign, ast.AugAssign, ast.If, ast.IfExp, ast.Expr, ast.Name,
               ast.Load, ast.Store, ast.Constant, ast.Tuple, ast.List, ast.BinOp,
               ast.UnaryOp, ast.BoolOp, ast.Compare, ast.Call, ast.Attribute,
               ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.USub,
               ast.UAdd, ast.And, ast.Or, ast.Not, ast.Lt, ast.LtE, ast.Gt,
               ast.GtE, ast.Eq, ast.NotEq, ast.keyword)
    for node in ast.walk(tree):
        if not isinstance(node, allowed):
            raise ValueError('Unsupported surface syntax: ' + type(node).__name__)
        if isinstance(node, ast.FunctionDef) and node is not fn:
            raise ValueError('Nested functions are not allowed')
        if isinstance(node, ast.Constant) and type(node.value) not in (int, float, bool) and id(node) not in docstrings:
            raise ValueError('Only numeric values are allowed')
        if isinstance(node, ast.Assign) and not all(numeric_target(t) for t in node.targets):
            raise ValueError('Only local numeric assignments are allowed')
        if isinstance(node, ast.Name) and (node.id.startswith('_') or node.id == 'surface_height'):
            raise ValueError('Private names and recursive calls are not allowed')
        if isinstance(node, ast.Attribute) and (not isinstance(node.value, ast.Name) or
                node.value.id != 'math' or node.attr not in _FUNCTIONS | _CONSTANTS):
            raise ValueError('Only numeric math attributes are allowed')
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id not in {'abs', 'min', 'max'}:
                raise ValueError('Unsupported function call')
            if not isinstance(node.func, (ast.Name, ast.Attribute)):
                raise ValueError('Unsupported function call')
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            if (not isinstance(node.right, ast.Constant) or type(node.right.value) not in (int, float)
                    or abs(node.right.value) > 8):
                raise ValueError('Powers require a small constant exponent')
        if isinstance(node, ast.AugAssign) and (not isinstance(node.target, ast.Name) or isinstance(node.op, ast.Pow)):
            raise ValueError('Only scalar arithmetic augmented assignments are allowed')
    if not any(isinstance(n, ast.Return) for n in ast.walk(tree)):
        raise ValueError('Surface function must return a numeric height')
    return ast.unparse(tree)


def curvy_action(response, max_speed):
    def unique_object(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                raise ValueError('Duplicate JSON action key')
            result[k] = v
        return result

    decoder = json.JSONDecoder(object_pairs_hook=unique_object)
    text = response.strip()
    try:
        action = decoder.decode(text)
    except (ValueError, json.JSONDecodeError):
        candidates = []
        # Keep exactly one complete action; do not repair numbers, partial
        # JSON, or choose among competing actions in an explanation.
        for match in re.finditer(r'\{', text):
            try:
                item, _ = decoder.raw_decode(text, match.start())
            except (ValueError, json.JSONDecodeError):
                continue
            if isinstance(item, dict) and {'v_angle', 'v_speed'} <= item.keys():
                candidates.append(item)
        unique = {json.dumps(c, sort_keys=True): c for c in candidates}
        if len(unique) != 1:
            raise ValueError('Return one unambiguous JSON object with v_angle and v_speed')
        action = next(iter(unique.values()))
    if not isinstance(action, dict):
        raise ValueError('Return a JSON object, not an array')
    if isinstance(action.get('v_angle'), bool) or isinstance(action.get('v_speed'), bool):
        raise ValueError('Direction and speed must be numbers')
    angle, speed = float(action['v_angle']), float(action['v_speed'])
    if not math.isfinite(angle) or not math.isfinite(speed) or not 0 <= speed <= max_speed:
        raise ValueError('Invalid direction or speed')
    return action, angle, speed
