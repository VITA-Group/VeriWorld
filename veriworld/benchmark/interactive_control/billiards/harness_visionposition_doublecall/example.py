# harness_visionposition_doublecall uses TWO sequential VLM calls per
# round.  These are the two action shapes the agent must produce.

# Call 1 (vision-only): declare intent + which ball coords you need.
call1 = {
    "intent": "Pocket the yellow ball into the NW corner pocket on a thin cut.",
    "query_balls": ["cue", "ball_2"],
}

# Call 2 (text-only with queried positions injected): emit the shot.
call2 = {
    "shot": {"angle_deg": 142.0, "speed_cm_s": 950.0},
}
