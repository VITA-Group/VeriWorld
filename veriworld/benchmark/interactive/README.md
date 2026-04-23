# Interactive Tasks

Per-tick probing with high agent freedom. The agent drives a tight observation loop: screenshot → think → act → screenshot.

**Build**: `PackagedOutput`

Launch a UE instance before running any harness here:

```
PackagedOutput\Windows\demo1.exe demo1 -AudioMixer -WebSocketPort=9003 -windowed -ResX=640 -ResY=480 -ForceRes -nosplash -log
```

## Sub-categories

### [`recognition/`](recognition/) — ground spatial structure, then execute

Agent probes the scene to identify spatial relationships (hinge topology, label-to-object mapping, geometric dimensions), then executes a short action sequence.

- Sampled tasks: **PlaceOnPlatform**, **BoxFold**
- Observation: custom screenshots per tick
- Action: per-tick API calls (move, fold, place)

### [`navigation/`](navigation/) — build a spatial model and move through it

Agent issues per-tick movement from an egocentric view; receives screenshot and collision feedback after each step. Must build a spatial model incrementally.

- Sampled tasks: **MazeNavFPS**
- Observation: screenshots + collision feedback per step
- Action: per-tick movement commands
