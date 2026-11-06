# Agent API — billiards / harness_visionposition

Each round you respond with a `thought:` line followed by a single
JSON block that wraps both the cue shot AND an optional list of
ball IDs whose exact (x, y, z) coordinates you want for next
round's planning:

````
thought: <reasoning, plain text>

```json
{
  "shot": {"angle_deg": <number>, "speed_cm_s": <number>},
  "query_balls": ["cue", "ball_0", ...]   // optional; empty/omitted = no query
}
```
````

Backward-compatible flat form `{"angle_deg": ..., "speed_cm_s": ...}`
still works (treated as no query).

## Fields

| Field | Type | Description |
|---|---|---|
| `shot.angle_deg` | number | Direction of cue shot, deg, [-180, 180]. `0` = +X toward the rack. |
| `shot.speed_cm_s` | number | Initial cue speed, cm/s, (0, 1500] (clamped). |
| `query_balls` | list[str] | OPTIONAL. Ball IDs you want positions for next round. Valid IDs: `"cue"`, `"ball_0"` … `"ball_4"`. Pass empty list or omit to query nothing. |

## Active-query loop (timing matters)

This harness gives you NO ball coordinates by default — you only
see the screenshot. To learn an exact (x, y, z) for a ball,
include its ID in `query_balls`. The harness reads
`workspace/state.json` after the round settles and includes those
balls' positions in the **next** round's prompt under
`## Your last query_balls request returned`.

```
Round N    : see image + last round's query results   → choose shot + queries
Harness    : apply shot, settle physics, dump state.json
Round N+1  : see image + Round-N's queries' results
```

The first round has no query data (the loop starts empty). Plan
accordingly: a defensible Round-1 strategy is to request positions
for the cue + the ball you intend to attack on Round 2, take a
gentle / scouting Round-1 shot, and then commit to a precise shot
on Round 2 with exact coords in hand.

## Strongly recommended workflow (per round)

The whole point of this harness is that **selective queries** —
asking only for the balls relevant to the next decision — should
let you reason precisely without having to parse every ball off
the image. Use that:

1. **Decide the target** — which coloured ball am I attacking, and
   into which corner pocket? (Pocket centres are a known function
   of the table geometry; you can read them off the image's four
   dark corner discs.)
2. **Decide what info you need to compute the shot** — typically
   the (x, y) of the cue and of the target. Sometimes also a
   blocker ball whose path you must clear. Add those IDs to
   `query_balls`.
3. **Reason geometrically with the queried coordinates**:
   - Pocket position `P`, target position `T`, cue position `C`
     (all known after queries arrive next round).
   - Ghost-ball point `G = T + 2 * BALL_RADIUS * normalise(T - P)`
     — this is where the cue's centre must arrive at the moment
     of contact for the target to leave along `T → P`.
   - Cue shot direction = `normalise(G - C)`,
     `angle_deg = degrees(atan2(G.y - C.y, G.x - C.x))`.
   - Speed: depends on cue→G distance, T→P distance, and ball
     damping. A starting heuristic is
     `speed = max(700, 5 * |T → P| + 2 * |C → G|)` cm/s, capped
     at the 1500 cm/s engine limit; tune based on what you see.
4. **Verify in your `thought` block** — write out the queried
   coords and the arithmetic that produced your shot. The text is
   logged; if you're wrong you can audit yourself round-to-round.

## Constraints

- You only kick the **cue**.
- Cue position carries across rounds (UE is persistent), **except**
  on a scratch.
- **Cue scratch → respawn at table centre.** Episode does NOT end.

## What's different from sibling harnesses

| Harness | What you see each round |
|---|---|
| `harness_image` | screenshot only |
| **`harness_visionposition`** (this one) | screenshot + only the ball coords you asked for last round |
| `harness_visiongroundtruth` | screenshot + every ball coord + every static physics param |

`harness_visionposition` tests whether **selectively-queried
structured information** is more efficient than a global
ground-truth firehose. The agent has to identify which numerical
data actually matters for its next decision and ask just for
that. Used well, the prompt is shorter (less for the model to
parse) and the reasoning is forced to be explicit. Used badly
(no queries, or wrong queries), the agent is effectively running
in vision-only mode.
