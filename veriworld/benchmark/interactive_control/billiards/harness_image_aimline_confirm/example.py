# harness_aimline_confirm uses a multi-turn REPL per round.  Three
# distinct action shapes the agent must produce:

# (1) INITIAL PROPOSAL (turn 0) — same shape as harness_visiongroundtruth.
proposal = {
    "angle_deg": 12.0,
    "speed_cm_s": 1100.0,
}

# After (1), the harness draws a red beam from the cue ball at angle_deg
# and sends back a screenshot. You then reply with EITHER:

# (2a) CONFIRM — fire the proposed shot as-is.
confirm = {"confirm": True}

# (2b) ADJUST — re-aim with a new angle/speed; the beam is redrawn and
# you'll be asked again. Up to max_adjusts_per_round adjustments per
# round (default 3); after that the current proposal fires anyway.
adjust = {
    "adjust": {"angle_deg": 18.5, "speed_cm_s": 950.0},
}
