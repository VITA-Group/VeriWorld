# Requesting Access to the Packaged Unreal Engine Build

The VeriWorld tasks require a packaged Unreal Engine build (`demo1.exe` and associated assets) to run. The build is **not distributed through this repository**. It is made available separately via an access-gated Google Drive, by individual request.

This page explains how to request access.

---

## Before You Apply

Please read the following documents in full. Submission of the request form constitutes acceptance of these terms.

- [LICENSE](../LICENSE) — PolyForm Noncommercial License 1.0.0
- [NOTICE](../NOTICE.md) — scope of permitted use, prohibited commercial uses, redistribution ban, reverse-engineering ban

A short summary of what you must agree to (not a substitute for the full documents):

1. **Noncommercial use only.** Permitted: academic research, coursework, personal study, use by nonprofit and public research institutions. Permitted specifically: running the build to evaluate models and reporting scores in academic publications, **and building new academic benchmarks / datasets / research tools that extend VeriWorld**.
2. **No commercial training, products, or commercial benchmark republication.** You may not use the build, its outputs, or any derived data to train or improve any commercial model, to build or evaluate a commercial product, or to republish as a commercial benchmark / dataset / evaluation suite.
3. **No redistribution.** Access granted to you is personal. You may not share the build, mirror it, post download links, or transfer it to any third party.
4. **No reverse engineering.** You may not decompile the build, extract assets, or circumvent access controls.
5. **Mandatory attribution for academic follow-up work.** If your publication, preprint, benchmark release, or other public academic work builds on, extends, or is substantially informed by VeriWorld, you must **both** (a) cite VeriWorld per `CITATION.cff` **and** the VoxelCodeBench paper (Zheng & Bordes, 2026 — `arXiv:2604.02580`; full BibTeX in the repository README's *Citation* section). Both citations are required for any use of VeriWorld, not just work that touches the voxel API directly. Then (b) explicitly acknowledge VeriWorld in the body of the work (an "Acknowledgements" section, "Prior Work" section, or equivalent — citation in the reference list alone is not sufficient). See NOTICE §2.2.1.

---

## How to Apply

1. **Fill out the request form**: [REQUEST ACCESS FORM](https://docs.google.com/forms/d/e/1FAIpQLSfEJuktF1lUhlhHTz0i0P9-rMevgQHZGHGkKoZWHwwUMsflTQ/viewform)
2. You will be asked for:
   - Your name, affiliation, and role
   - The Google account email to which access should be granted
   - A short description of your intended use
   - Explicit acknowledgment of each of the terms above
3. Submit. The form goes to `axisworld.team@gmail.com`.
4. You will typically receive a response within **5 business days**. If the request is approved, the packaged build folder will be shared to the Google account email you provided.

## If You Don't Hear Back

- Check your spam folder — the approval email comes from `axisworld.team@gmail.com` and includes a Google Drive sharing notification.
- If more than 7 business days have passed, email `axisworld.team@gmail.com` with the subject line: `VeriWorld access follow-up — <your name>`.

---

## Scope of Access

- Access is **personal and non-transferable**. If you change institutions or want a colleague to have access, they must submit their own request.
- Access may be **revoked** at any time if terms are violated.
- Access does **not** include source code of the Unreal Engine project, shader sources, scene definitions, or any assets beyond the packaged runtime. Reverse engineering to obtain these is prohibited (see NOTICE §4).

## What's in the Build

Two separate Windows packaged builds are distributed. Each task category uses its own build:

- **Interactive tasks** (recognition, navigation) → `PackagedOutput`
- **Computational tasks** (feedback, coding) → `PackagedOutput_dev`

```
VeriWorld-UE-<version>-Windows/
├── PackagedOutput/            # used by interactive harnesses
│   ├── demo1.exe
│   ├── demo1/
│   └── Engine/
├── PackagedOutput_dev/        # used by computational harnesses
│   ├── demo1.exe
│   ├── demo1/
│   └── Engine/
└── README_INSIDE_BUILD.txt    (checksums, version, quick-start)
```

After extracting, the build is launched by the Python harnesses in this repository. See the top-level [README](../README.md) for harness setup — each harness points to the correct build.

---

## Other Platforms

Currently only a Windows 64-bit build is distributed. Linux and macOS are not supported by the packaged build. If you need a different platform, note it in the request form; we track demand but make no commitment to provide it.

---

## Questions

- **Access requests / build issues / form problems** → `axisworld.team@gmail.com`
- **Licensing, commercial use, legal** → `yan.zheng.mat@gmail.com` (see [NOTICE §6.2](../NOTICE.md#62-licensing-copyright-and-legal-inquiries))
