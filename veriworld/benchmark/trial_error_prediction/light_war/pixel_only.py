"""LightWar pure-visual ("pure V") condition (release).

Runs the unchanged V arm (harness_arc_visual_only, which already gives no numeric
miss distance) but replaces only the system prompt via install(), so the model
estimates everything by eye. Scene, physics, rendering, frame stacking, action
parsing, the per-round user text and the model's own thought/command history, and
the verifier are the unchanged scientific source.

Relative to the V arm the system prompt removes: the launch-site X/Y/Z; the
trajectory law, gravity and the "pitch ~10-25 deg" realistic-range line; the
numeric tolerance; the beacon range hint ("a few hundred metres"); the tower
height ("8 m"); and V's numeric strategy defaults ("pitch around 15 deg",
"+-5 deg / +-1000 cm/s"). It keeps: the images (including the renderer's burned-in
"t=...s" frame labels); the action names yaw_deg / pitch_deg / v0_cm_s with units,
the compass convention (0=+X, 90=+Y) and the v0 valid range; the round budget;
V's qualitative image-reading guidance; and the stop flag.

install() wraps the native harness_arc.task._system_prompt: it calls the original
to obtain the V reference (saved as reference_v_system_prompt.txt, never sent),
returns the pixel-only prompt, and fails closed if any removed quantity (a
FORBIDDEN phrase, a launch coordinate, or the tolerance value) would leak into the
sent prompt. Derived byte-for-byte from the 2026-11-06 experiment source; the only
change is the CONDITION label normalised to 'pure_visual' (proof metadata only).
"""
import hashlib, json, re
from pathlib import Path

CONDITION = 'pure_visual'
FORBIDDEN = ('980', 'tolerance:', 'TRAJECTORY MODEL', 'launch site: X=', 'few hundred', '8 m tower', 'pitch ~10',
             'pitch around 15', '1000 cm/s', 'missed by', 'miss distance (in metres)')


def system_prompt(spec, max_rounds, stack_layout, beacon_color_name):
    rows, cols = stack_layout
    v0_lo, v0_hi = spec['v0_range_cm_s']
    return (
        "You are an artillery agent in a UE5 forest scene. Your goal is\n"
        f"to land a projectile on a glowing **{beacon_color_name} beacon tower** standing on a distant hilltop.\n"
        "You are NOT told the launch site's or the beacon's world coordinates,\n"
        "the projectile's flight law, or the numeric scoring tolerance. Estimate\n"
        "everything by EYE from the launch-site preview (round 1) and from the\n"
        "per-shot feedback stack (rounds 2+).\n\n"
        "TASK: Hit the cylindrical lighthouse beacon on the distant hilltop (light at the top of a tower).\n"
        "PASS = your final-round landing point is within a fixed, undisclosed\n"
        "distance of the beacon's apex.\n"
        f"max_rounds: {max_rounds}\n\n"
        "The launch site is in a forest clearing; the beacon sits on hilly\n"
        "terrain in the bearing shown by the round-1 preview image.\n\n"
        "ACTION FORMAT (one JSON code fence per round; example values are syntax only):\n"
        "```json\n"
        "{\n"
        '  "yaw_deg":  245.0,       // compass yaw of the launch direction in degrees (0=+X, 90=+Y)\n'
        '  "pitch_deg": 30.0,       // elevation above horizon in degrees (0=horizontal, 90=straight up)\n'
        f'  "v0_cm_s":   {(v0_lo + v0_hi) / 2:.1f},     // initial speed in cm/s; valid {v0_lo:.0f}..{v0_hi:.0f}\n'
        '  "stop": false            // optional, true ends the episode\n'
        "}\n"
        "```\n\n"
        f"FEEDBACK each round — a {rows}×{cols} stacked image of {rows*cols}\n"
        "first-person frames sampled at evenly-spaced waypoints along the\n"
        "previous shot's arc (camera placed on the projectile, facing along\n"
        "the velocity vector). Frame labels read 't=...s'.\n"
        "Use the stack to judge whether your shot fell SHORT, LONG, LEFT,\n"
        "or RIGHT of the beacon, and how high it flew vs the hilltop.\n"
        # verbatim V sentence (including its hard-coded "cyan"; the beacon colour is randomised in both arms)
        "VISUAL-ONLY ABLATION: you will NOT receive a numerical miss\n"
        "distance. Judge over/under/left/right purely from the cyan\n"
        "beacon's position in the FPV stack and how it moves across\n"
        "the 6 frames.\n\n"
        "STRATEGY:\n"
        f"  - Round 1: read the launch-site preview. The {beacon_color_name} beacon glow\n"
        "    is visible somewhere in the frame; estimate its compass\n"
        "    bearing (yaw) from its left/right position and a rough\n"
        "    range from how small the glow is. Take your best opening guess.\n"
        f"  - Round 2+: read the 6-frame stack. If the {beacon_color_name} glow stays\n"
        "    ABOVE the horizon in late frames, you fell short — bump\n"
        "    pitch or v0. If it drops BELOW the horizon early, you flew\n"
        "    over — drop pitch or v0. If the glow drifts LEFT across the\n"
        "    sequence, your yaw was too LOW (CW rotation needed:\n"
        "    increase yaw); drift to the RIGHT means yaw was too HIGH.\n"
        "  - Make moderate adjustments at first; fine tune as the landing\n"
        "    closes in.\n"
        "  - Set `\"stop\": true` only if you're confident the shot landed\n"
        "    on or against the beacon. Otherwise the harness will fire\n"
        "    again with the next round's parameters.\n\n"
        "RESPONSE FORMAT each round:\n"
        "  Brief THOUGHT (plain text, optional), then ONE JSON code fence.\n"
    )


def install(arc, folder):
    """Wrap arc._system_prompt (already wrapped by the scene recorder). Returns the previous function."""
    previous = arc._system_prompt
    folder = Path(folder)

    def pixel_only(spec, max_rounds, stack_layout, **kwargs):
        assert kwargs.get('give_miss_distance') is False and not kwargs.get('give_state') and kwargs.get('give_images', True)
        reference = previous(spec, max_rounds, stack_layout, **kwargs)  # runs the native scene check; never sent
        launch = spec['launch_pose']
        sent = system_prompt(spec, max_rounds, stack_layout, kwargs.get('beacon_color_name', 'glowing'))
        leaks = [s for s in FORBIDDEN if s in sent]
        # whole-number match (v1 used a substring test: a launch Z of e.g. 1100 matched the v0 example "11000.0")
        numbers = set(re.findall(r'(?<![\d.])-?\d+(?:\.\d+)?(?![\d.])', sent))
        for axis in 'XYZ':
            value = float(launch[axis])
            if abs(value) >= 100 and any(abs(float(n) - value) < 0.5 for n in numbers):
                leaks.append('launch_' + axis)
        if any(abs(float(n) - float(spec['tolerance_cm'])) < 0.5 for n in numbers):
            leaks.append('tolerance_value')
        assert not leaks, leaks
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'reference_v_system_prompt.txt').write_text(reference, encoding='utf-8')
        (folder / 'pixel_only_system_prompt.txt').write_text(sent, encoding='utf-8')
        (folder / 'pixel_only_proof.json').write_text(json.dumps(dict(
            condition=CONDITION, source_arm='harness_arc_visual_only', reference_sha256=hashlib.sha256(reference.encode()).hexdigest(),
            sent_sha256=hashlib.sha256(sent.encode()).hexdigest(), forbidden_checked=list(FORBIDDEN) + ['launch_XYZ', 'tolerance_value'],
            reference_contains_launch=('launch site: X=' in reference), user_turn_text='unchanged source (harness_arc_visual_only)'), indent=1), encoding='utf-8')
        return sent

    arc._system_prompt = pixel_only
    return previous


def check_messages(messages, sent_prompt_path):
    """Per-call guard: system prompt is the saved pixel-only prompt; user text carries no numeric scene feedback."""
    sent = Path(sent_prompt_path).read_text(encoding='utf-8')
    assert messages[0]['role'] == 'system' and messages[0]['content'] == sent, 'system prompt is not the pixel-only prompt'
    text = ''.join(p.get('text', '') for p in messages[1]['content'] if isinstance(p, dict) and p.get('type') == 'text')
    # harness-authored feedback markers only (the history also echoes the model's own free-text thoughts)
    for s in ('**Result**:', 'Your previous shot missed by', 'Previous shot telemetry', 'TARGET (beacon apex)'):
        assert s not in text, 'numeric scene feedback in user turn: ' + s
