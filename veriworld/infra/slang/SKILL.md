# Slang Compute Shader Skill

## Scope

Writing Slang compute shaders that are dispatched at runtime through
Unreal Engine's **Render Dependency Graph** (RDG). Used by coding /
feedback tasks where the agent submits a shader and the engine runs it
as deterministic ground-truth physics.

This is **not** CUDA. VeriWorld has no `.cu` files and no
`cudaMalloc`/cuBLAS/cuDNN interop. The skill documents a deliberately
narrower capability surface — see the table below.

## Capability table

| | Available |
|---|---|
| Thread groups, `groupshared` memory, atomics | ✅ |
| `RWStructuredBuffer` read/write | ✅ |
| Wave / subgroup ops (shuffle, ballot) | ⚠ partial (HLSL-compatible subset) |
| Auto-differentiation (Slang-native) | ✅ |
| Tensor cores (WMMA) | ⚠ very limited, not recommended |
| Dynamic memory allocation | ❌ buffers are UE-bound |
| Dynamic parallelism (kernel-launches-kernel) | ❌ |
| `cuBLAS` / `cuDNN` / `Thrust` | ❌ |
| Host-device streams | ❌ UE owns the render thread |

If your task requires anything in the ❌ rows, it does not belong in
VeriWorld — open an issue to discuss a separate benchmark.

## The `ClothNode._pad` convention

Every VeriWorld shader reads and writes a single
`RWStructuredBuffer<ClothNode>`, where `ClothNode._pad` is a free
float-per-node that the task uses as its **data channel** — metadata,
ball state, parameter vector, whatever the task needs.

```slang
struct ClothNode {
    float px, py, pz;     // mesh position
    float invMass;
    float vx, vy, vz;     // mesh velocity
    float _pad;           // TASK-DEFINED — see per-shader comment header
};
```

The header comment of each shipped shader documents its slot
assignment. For example, `bouncy_ball.slang`:

```
Ball state:        Metadata:
  nodes[5]._pad  = ballX        nodes[0]._pad = gridN
  nodes[6]._pad  = ballY        nodes[1]._pad = spacing
  nodes[7]._pad  = ballZ
  nodes[8]._pad  = radius
  nodes[9]._pad  = vX
  nodes[10]._pad = vY
  nodes[11]._pad = vZ
```

**Do the same for any new shader you write** — the header comment is
the contract with the harness.

## Dispatch pattern

Shaders are single-kernel compute dispatches:

```slang
[shader("compute")]
[numthreads(256, 1, 1)]
void MyKernel(
    uint3 tid : SV_DispatchThreadID,
    uniform RWStructuredBuffer<ClothNode> nodes)
{
    uint totalN = (uint)nodes[0]._pad * (uint)nodes[0]._pad;
    uint i = tid.x;
    if (i >= totalN) return;
    // ... compute ...
}
```

The harness pipes the source string (via `python_exec` or
`file_upload`), UE compiles it through its Slang path, and RDG
dispatches it every frame until the task stops. Per-frame state flows
entirely through the `nodes` buffer.

Some shaders (like the physics ones) have **only one thread doing the
meaningful work** — they gate with `if (i != 1) return;` after reading
metadata. That's fine: the dispatch is still per-tick, the physics
just doesn't parallelise.

## When to use

- Physics simulation (rigid body, soft body, particle, fluid).
- Pathfinding / geometric algorithms expressible as a fixed-size
  dispatch.
- Ray-marching / signed-distance-field evaluation.
- Auto-differentiable forward simulators for parameter fitting.

## When NOT to use

- Neural network inference in-engine.
- Dynamic tree / graph algorithms with growing memory (MCTS, adaptive
  mesh refinement).
- Anything needing CUDA-specific libraries.

## Skill contents

- [`api.md`](api.md) — buffer layout, slot assignment conventions,
  dispatch protocol, auto-diff notes, common pitfalls.
- [`examples/trivial_kernel.slang`](examples/trivial_kernel.slang) —
  the smallest possible shader that reads metadata, does something,
  writes back.
- [`examples/physics_skeleton.slang`](examples/physics_skeleton.slang)
  — annotated skeleton based on `bouncy_ball.slang`, with the physics
  stripped out. Copy and fill in.

## References (shipped in VeriWorld)

- [`veriworld/benchmark/computational/feedback/surface_billiards/lean_verify/billiard_ball.slang`](../../benchmark/computational/feedback/surface_billiards/lean_verify/billiard_ball.slang)
  — hard billiards physics (inelastic ground, friction 0.20).

## References (Slang language itself)

- Slang docs: <https://shader-slang.com/slang/user-guide/>
- UE RDG: <https://docs.unrealengine.com/5.3/en-US/render-dependency-graph-in-unreal-engine/>
