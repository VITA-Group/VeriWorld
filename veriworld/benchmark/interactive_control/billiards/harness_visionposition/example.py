# Action format for harness_visionposition — wrapped JSON with both
# the cue shot AND a list of ball IDs to query positions for next
# round. Empty `query_balls` (or omitting the key) means no query.
action = {
    "shot": {"angle_deg": 0.0, "speed_cm_s": 900.0},
    "query_balls": ["cue", "ball_0"],   # optional
}
