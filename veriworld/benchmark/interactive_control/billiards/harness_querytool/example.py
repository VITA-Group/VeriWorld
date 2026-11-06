# harness_querytool: agent has a REPL within each round.  Two action shapes.

# (1) QUERY — try a hypothetical shot. Scene state is rolled back, so this
# costs nothing in the real game.  Returns the deterministic outcome:
# what got pocketed, where the cue ended up, what cue hit first.
query = {
    "action": "query",
    "tool": "simulate_shot",
    "args": {"angle_deg": 12.0, "speed_cm_s": 1100.0},
}

# (2) SHOOT — commit.  This actually kicks the cue and advances the
# round.  May only be issued once per round.
shoot = {
    "action": "shoot",
    "shot": {"angle_deg": 18.5, "speed_cm_s": 1100.0},
}
