# Slang Shader API Reference

## The `ClothNode` buffer

Every VeriWorld shader receives the same buffer declaration — a
`RWStructuredBuffer<ClothNode>` owned by the UE RDG pass:

```slang
struct ClothNode {
    float px, py, pz;     // mesh vertex position
    float invMass;        // reciprocal mass (0 = pinned vertex)
    float vx, vy, vz;     // mesh vertex velocity
    float _pad;           // task-defined data channel
};
```

The first fields (`px/py/pz/invMass/vx/vy/vz`) are the mesh simulation
state that UE wants for its renderable surface. You may read and write
them — tasks that modify the visible mesh do. Tasks that don't care
about the mesh leave them alone.

The **`_pad` field is yours to repurpose**. It's the data channel
between Python harness and shader.

### Slot-assignment convention

Document the slot layout at the **top of every shader file** as a
comment block:

```
// ============================================================
// <TaskName> — <one-line purpose>
//
// Metadata:
//   nodes[0]._pad  = <name> <units>
//   nodes[1]._pad  = <name> <units>
//
// <Dynamic state block>:
//   nodes[5]._pad  = <name>
//   nodes[6]._pad  = <name>
//   ...
// ============================================================
```

This is how the Python harness knows which slots to write into before
dispatch and which to read after. Keep it up to date.

### Reserved slots (convention only)

- `nodes[0]._pad` — primary dimension (gridN, N, …). Used with an
  integer cast: `(uint)nodes[0]._pad`.
- `nodes[1]._pad` — spacing / unit length.

Subsequent slots are task-private. Shipped shaders commonly use:

- `nodes[5..7]` — position (x/y/z)
- `nodes[8]` — radius / size
- `nodes[9..11]` — velocity (vx/vy/vz)

Copy this if your task is similar.

## Kernel entry point

```slang
[shader("compute")]
[numthreads(256, 1, 1)]
void MyKernel(
    uint3 tid : SV_DispatchThreadID,
    uniform RWStructuredBuffer<ClothNode> nodes)
{
    // ...
}
```

- Entry-point **name** is task-specific and must match what the
  harness dispatches.
- `[numthreads(X, Y, Z)]` sets workgroup size; `256, 1, 1` is the
  standard for linear-over-`nodes` workloads.
- `tid.x` is the thread / node index.

### Single-thread physics pattern

If your physics logic is serial (the whole ball's state is one datum),
gate with:

```slang
uint totalN = gridN * gridN;
uint i = tid.x;
if (i >= totalN) return;        // bounds guard
if (i != 1) return;              // only thread 1 does ball logic
```

This is what `bouncy_ball.slang` and `billiard_ball.slang` do — the
simulation is serial, but the shader is dispatched as a parallel
compute for RDG uniformity.

## Dispatch protocol (what the harness does)

1. Harness pipes your `.slang` source to UE (via `python_exec` that
   writes the string to a file in the engine's staging area, then
   invokes UE's Slang compile path — see `surface_billiards/example.py`
   for the worked flow).
2. UE compiles + links + enqueues a compute pass that runs your
   `MyKernel` every frame against the mesh's `ClothNode` buffer.
3. Harness writes initial state into `nodes[i]._pad` via
   `python_exec`, sleeps while the engine ticks, reads state back.
4. For per-round tasks, harness stops recording → kills UE → next
   round gets a fresh shader compile.

Tasks do **not** manually dispatch — they set up and let UE tick.

## Auto-differentiation

Slang supports native reverse-mode autodiff. Used by tasks that want
the agent to fit parameters via gradients:

```slang
[Differentiable]
float loss(float x) { return (x - target) * (x - target); }
```

See the [Slang autodiff docs](https://shader-slang.com/slang/user-guide/autodiff.html).
VeriWorld's shipped tasks don't use autodiff yet — it's there for new
tasks that want it.

## Common pitfalls

- **Forgetting the bounds guard** (`if (i >= totalN) return;`). Even a
  single-thread shader needs it — dispatches round up to a multiple of
  `numthreads`.
- **Using `cos` / `sin` on `int` variables**. Slang is stricter than
  HLSL about type mismatches — cast explicitly.
- **Relying on a specific number of frames**. The harness doesn't
  control dispatch cadence directly — it observes outcomes after a
  wall-clock sleep. Design your simulation to converge or explicitly
  flag "done" via a `_pad` slot the harness can poll.
- **Trying to use CUDA features**. If you find yourself wanting
  `cudaMalloc` or `cublasSgemm`, stop — your task doesn't belong in
  VeriWorld.

## References

- Shipped shaders:
  - [`billiard_ball.slang`](../../benchmark/computational/feedback/surface_billiards/lean_verify/billiard_ball.slang)
  - [`bouncy_ball.slang` / `math_surface.slang`](https://github.com/axisworld-team) (in the private R&D tree; not in public repo — the `billiard_ball` one in VeriWorld is representative).

- Templates:
  - [`examples/trivial_kernel.slang`](examples/trivial_kernel.slang) —
    simplest complete shader
  - [`examples/physics_skeleton.slang`](examples/physics_skeleton.slang)
    — ball-on-surface skeleton with physics stripped
