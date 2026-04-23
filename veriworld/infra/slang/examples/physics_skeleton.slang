// ============================================================================
// Physics Skeleton — template for a ball-on-surface dynamics shader.
//
// Copy this file into your task's `lean_verify/` (or equivalent) directory
// and fill in the four marked TODO sections. Based on the structure of the
// shipped `billiard_ball.slang` but with the physics rules stripped so you
// can see the scaffolding clearly.
//
// Slot assignment (adjust per your task):
//   nodes[0]._pad  = gridN           — surface resolution (metadata)
//   nodes[1]._pad  = spacing         — grid cell size, cm (metadata)
//   nodes[5]._pad  = ball X, cm
//   nodes[6]._pad  = ball Y, cm
//   nodes[7]._pad  = ball Z, cm
//   nodes[8]._pad  = ball radius, cm
//   nodes[9]._pad  = velocity X
//   nodes[10]._pad = velocity Y
//   nodes[11]._pad = velocity Z
// ============================================================================

struct ClothNode
{
    float px, py, pz;
    float invMass;
    float vx, vy, vz;
    float _pad;
};

[shader("compute")]
[numthreads(256, 1, 1)]
void PhysicsKernel(
    uint3 tid : SV_DispatchThreadID,
    uniform RWStructuredBuffer<ClothNode> nodes)
{
    // ── 1. Metadata ─────────────────────────────────────────────
    uint gridN = (uint)nodes[0]._pad;
    if (gridN < 3) gridN = 80;

    float spacing = nodes[1]._pad;
    if (spacing <= 0.0f) spacing = 12.0f;

    uint totalN = gridN * gridN;
    uint i = tid.x;
    if (i >= totalN) return;
    if (i != 1) return;   // single-thread physics

    // ── 2. Constants ───────────────────────────────────────────
    float dt   = 0.003f;              // per-dispatch step
    float grav = 300.0f;              // cm / s²
    // TODO: add task-specific constants (friction, restitution, etc.)

    float ballR = nodes[8]._pad;
    if (ballR <= 0.0f) ballR = 12.0f;

    // ── 3. Read current state ───────────────────────────────────
    float bx = nodes[5]._pad;
    float by = nodes[6]._pad;
    float bz = nodes[7]._pad;
    float bvx = nodes[9]._pad;
    float bvy = nodes[10]._pad;
    float bvz = nodes[11]._pad;

    // ── 4. Integrate one step ───────────────────────────────────
    // TODO: apply forces (gravity, drag, surface normal, collisions).
    //       The shipped shaders pattern is:
    //         a) apply gravity:             bvz -= grav * dt
    //         b) predict position:          bx += bvx * dt  (etc.)
    //         c) collide with surface:      compute surface Z at (bx, by),
    //                                       if bz < surfZ + ballR, reflect
    //         d) apply friction:            bvx *= (1 - mu * dt), etc.
    //         e) ground collision:          if bz < groundZ + ballR, stop z.

    // Placeholder step — free fall only, no collisions:
    bvz -= grav * dt;
    bx += bvx * dt;
    by += bvy * dt;
    bz += bvz * dt;

    // ── 5. Write back ───────────────────────────────────────────
    nodes[5]._pad  = bx;
    nodes[6]._pad  = by;
    nodes[7]._pad  = bz;
    nodes[9]._pad  = bvx;
    nodes[10]._pad = bvy;
    nodes[11]._pad = bvz;
}
