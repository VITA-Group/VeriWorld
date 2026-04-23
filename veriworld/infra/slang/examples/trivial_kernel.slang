// ============================================================================
// TrivialKernel — smallest complete VeriWorld Slang shader.
//
// Demonstrates the three mandatory conventions:
//   1. `ClothNode` struct with a `_pad` data channel
//   2. Slot-assignment comment block in the header
//   3. Bounds guard before any work
//
// What it does: reads one scalar from slot 2, adds it to slot 3, writes the
// sum to slot 4. One-thread kernel — gated to thread 1.
//
// Slot assignment:
//   nodes[0]._pad = gridN        (metadata — surface resolution)
//   nodes[1]._pad = spacing      (metadata — unused here)
//   nodes[2]._pad = input A      (task input)
//   nodes[3]._pad = input B      (task input)
//   nodes[4]._pad = output = A + B
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
void TrivialKernel(
    uint3 tid : SV_DispatchThreadID,
    uniform RWStructuredBuffer<ClothNode> nodes)
{
    uint gridN = (uint)nodes[0]._pad;
    if (gridN < 1) gridN = 1;

    uint totalN = gridN * gridN;
    uint i = tid.x;
    if (i >= totalN) return;   // mandatory bounds guard
    if (i != 1) return;        // this task's logic is serial; thread 1 only

    float a = nodes[2]._pad;
    float b = nodes[3]._pad;
    nodes[4]._pad = a + b;
}
