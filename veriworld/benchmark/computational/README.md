# Computational Tasks

Per-round submission with video + log feedback. The agent submits code or parameters once per round; the engine executes, and results return as video plus structured verification logs.

**Build**: `PackagedOutput_dev`

Launch a UE instance before running any harness here:

```
PackagedOutput_dev\Windows\demo1.exe demo1 -AudioMixer -WebSocketPort=9003 -windowed -ResX=640 -ResY=480 -ForceRes -nosplash -log
```

## Sub-categories

### [`feedback/`](feedback/) — simple action space, complex dynamics

Agent submits a parameter vector (angle, velocity, timing) per round. The engine simulates the physics. Action space is simple but correct parameters require complex visual or symbolic reasoning.

- Sampled tasks: **MovingShooter1**, **MovingShooter2**, **SurfaceBilliards**
- Observation: video + structured verification log per round
- Action: parameter vector submission

### [`coding/`](coding/) — write verifiable mathematical programs

Agent writes executable code per round: pathfinding algorithms, Slang compute shaders, coordinate-parametric trajectory functions. The engine runs the code and deterministically verifies correctness.

- Sampled tasks: **DropToTarget**, **MazeNavGrid**, **MazeNavCollide**
- Observation: video + verification log per round
- Action: executable code submission (Python or Slang — see [`../../infra/slang/SKILL.md`](../../infra/slang/SKILL.md))
