"""Explicit parameter variants; original released instances remain immutable."""
import copy
import math

VARIANTS = ('standard', 'rotate90', 'mirror-x', 'wide-target')


def transform_xy(x, y, variant):
    if variant == 'rotate90':
        return -y, x
    if variant == 'mirror-x':
        return -x, y
    return x, y


def transform_angle(angle, variant):
    if variant == 'rotate90':
        return angle + math.pi / 2
    if variant == 'mirror-x':
        return math.pi - angle
    return angle


def parameters(task, original, variant):
    if task not in ('drop', 'curvy') or variant not in VARIANTS:
        raise ValueError('Unknown task or parameter variant')
    p = copy.deepcopy(original)
    if variant == 'standard':
        return p
    if task == 'drop':
        p['target'][:2] = transform_xy(*p['target'][:2], variant)
        p['ball_start'][:2] = transform_xy(*p['ball_start'][:2], variant)
        if variant == 'wide-target':
            p['target_radius'] *= 1.25
    else:
        for point in (p['target'], p['start']):
            point['x'], point['y'] = transform_xy(point['x'], point['y'], variant)
        for g in p['gaussians']:
            g['cx'], g['cy'] = transform_xy(g['cx'], g['cy'], variant)
        if variant == 'wide-target':
            p['target']['radius'] *= 1.25
    # Witnesses belong to the base instance. They are neither inputs to a model
    # nor certificates for a transformed instance: native qualification is required.
    for key in ('solution', 'reference_action', 'witness'):
        p.pop(key, None)
    return p
