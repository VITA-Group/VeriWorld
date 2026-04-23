// ============================================================================
// CUDA Slide + Ball — 3D nearest-point surface constraint
//
// Ball finds NEAREST surface node in 3D (not heightfield XY lookup).
// Surface normal from neighbors. Gravity projected onto tangent plane.
// Ball naturally follows curved surface in all 3 axes.
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
void SlideBall(
    uint3 tid : SV_DispatchThreadID,
    uniform RWStructuredBuffer<ClothNode> nodes)
{
    uint gridN = (uint)nodes[0]._pad;
    if (gridN < 3) gridN = 40;

    float spacing = nodes[1]._pad;
    if (spacing <= 0.0f) spacing = 12.0f;

    uint totalN = gridN * gridN;
    uint i = tid.x;
    if (i >= totalN) return;

    // Only thread 1 does ball dynamics
    if (i != 1) return;

    float dt = 0.003f;
    float ballR = nodes[8]._pad;
    if (ballR <= 0.0f) ballR = 30.0f;
    float mu = 0.4f;
    float grav = 300.0f;
    float groundZ = 15.0f;

    // Read ball state
    float bx = nodes[5]._pad;
    float by = nodes[6]._pad;
    float bz = nodes[7]._pad;
    float bvx = nodes[9]._pad;
    float bvy = nodes[10]._pad;
    float bvz = nodes[11]._pad;

    // Step 1: apply gravity to velocity
    bvz = bvz - grav * dt;

    // Step 2: predict position
    float px = bx + bvx * dt;
    float py = by + bvy * dt;
    float pz = bz + bvz * dt;

    // Step 3: find NEAREST surface node to ball in 3D
    float minDist2 = 999999999.0f;
    uint nearIdx = 0;
    for (uint si = 0; si < totalN; si = si + 1)
    {
        float dx = nodes[si].px - px;
        float dy = nodes[si].py - py;
        float dz = nodes[si].pz - pz;
        float d2 = dx * dx + dy * dy + dz * dz;
        if (d2 < minDist2)
        {
            minDist2 = d2;
            nearIdx = si;
        }
    }

    // Step 4: compute surface normal at nearest node
    uint ngi = nearIdx / gridN;
    uint ngj = nearIdx % gridN;

    float3 nearP = float3(nodes[nearIdx].px, nodes[nearIdx].py, nodes[nearIdx].pz);

    // Finite differences for normal
    float3 dPdu = float3(0.0f, 0.0f, 0.0f);
    float3 dPdv = float3(0.0f, 0.0f, 0.0f);

    if (ngi > 0 && ngi < gridN - 1)
    {
        uint ip = (ngi + 1) * gridN + ngj;
        uint im = (ngi - 1) * gridN + ngj;
        dPdu = float3(nodes[ip].px - nodes[im].px, nodes[ip].py - nodes[im].py, nodes[ip].pz - nodes[im].pz) * 0.5f;
    }
    else if (ngi < gridN - 1)
    {
        uint ip = (ngi + 1) * gridN + ngj;
        dPdu = float3(nodes[ip].px - nearP.x, nodes[ip].py - nearP.y, nodes[ip].pz - nearP.z);
    }
    else
    {
        uint im = (ngi - 1) * gridN + ngj;
        dPdu = float3(nearP.x - nodes[im].px, nearP.y - nodes[im].py, nearP.z - nodes[im].pz);
    }

    if (ngj > 0 && ngj < gridN - 1)
    {
        uint jp = ngi * gridN + (ngj + 1);
        uint jm = ngi * gridN + (ngj - 1);
        dPdv = float3(nodes[jp].px - nodes[jm].px, nodes[jp].py - nodes[jm].py, nodes[jp].pz - nodes[jm].pz) * 0.5f;
    }
    else if (ngj < gridN - 1)
    {
        uint jp = ngi * gridN + (ngj + 1);
        dPdv = float3(nodes[jp].px - nearP.x, nodes[jp].py - nearP.y, nodes[jp].pz - nearP.z);
    }
    else
    {
        uint jm = ngi * gridN + (ngj - 1);
        dPdv = float3(nearP.x - nodes[jm].px, nearP.y - nodes[jm].py, nearP.z - nodes[jm].pz);
    }

    // Normal = cross(dPdu, dPdv), pointing "outward" (away from surface center)
    float3 normal = cross(dPdu, dPdv);
    float nlen = length(normal);
    if (nlen > 0.001f) normal = normal / nlen;

    // Make sure normal points toward ball (outward)
    float3 toBall = float3(px, py, pz) - nearP;
    if (dot(toBall, normal) < 0.0f)
        normal = -normal;

    // Step 5: check if ball is close enough to surface to interact
    float dist3D = sqrt(minDist2);  // actual 3D distance to nearest node
    float distToSurf = dot(toBall, normal);

    // Only constrain if ball is NEAR the surface (within 2x ballR of nearest node)
    // Far away = free flight under gravity (parabolic trajectory)
    if (dist3D < ballR * 2.0f && distToSurf < ballR && distToSurf > -ballR * 2.0f)
    {
        // Project ball to surface + ballR along normal
        float correction = ballR - distToSurf;
        px = px + correction * normal.x;
        py = py + correction * normal.y;
        pz = pz + correction * normal.z;

        // Remove velocity component into surface
        float vn = bvx * normal.x + bvy * normal.y + bvz * normal.z;
        if (vn < 0.0f)
        {
            bvx = bvx - vn * normal.x;
            bvy = bvy - vn * normal.y;
            bvz = bvz - vn * normal.z;
        }

        // Friction: damp tangential velocity
        float vn2 = bvx * normal.x + bvy * normal.y + bvz * normal.z;
        float vtx = bvx - vn2 * normal.x;
        float vty = bvy - vn2 * normal.y;
        float vtz = bvz - vn2 * normal.z;
        float vt_mag = length(float3(vtx, vty, vtz));

        if (vt_mag > 0.1f)
        {
            float friction_decel = mu * grav * dt;
            float scale2 = max(0.0f, 1.0f - friction_decel / vt_mag);
            bvx = vn2 * normal.x + vtx * scale2;
            bvy = vn2 * normal.y + vty * scale2;
            bvz = vn2 * normal.z + vtz * scale2;
        }
    }

    // Ground floor
    if (pz < groundZ + ballR)
    {
        pz = groundZ + ballR;
        if (bvz < 0.0f) bvz = 0.0f;
        bvx = bvx * 0.95f;
        bvy = bvy * 0.95f;
    }

    // Write back
    nodes[5]._pad = px;
    nodes[6]._pad = py;
    nodes[7]._pad = pz;
    nodes[9]._pad = bvx;
    nodes[10]._pad = bvy;
    nodes[11]._pad = bvz;
}
