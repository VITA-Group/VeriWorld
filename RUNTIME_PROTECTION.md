# Runtime protection scope

This document describes the F main executables in `paper-artifacts-2026-11-03-rc1`.
Complete archive and part checksums are in manifest.json and SHA256SUMS.

| Platform | Main executable SHA256 |
| --- | --- |
| Windows x86_64 | `423264d1312305b84b23388dbb2f8a64f61a957172cc79eb0660e3624d9b8dcd` |
| Linux x86_64 | `6eb0efb389be4ce4dff879e539d901be95c3f3794154b845cd496ba5f77ef010` |

F replaces the serialized bootstrap program and generic bootstrap interpreter
with specialized native effects and successor calls. Existing bridge-string,
registry and loader protection remains. This is a representation change; it
does not encrypt the entire executable or virtualize the entire engine.

New source-blind authors submitted independent replay programs, frozen before
private grading with the vendor Python runtime. Linux passed 18 normal checks
and eight interventions; Windows passed 18 normal checks and a missing-library
check. These are fixed configurations, not independent benchmark seeds. The
core Python loader and initialization behavior therefore remains recoverable.

This evidence does not establish a complete Unreal clone, every path/failure
branch or whole-engine lifecycle equivalence. Static recovery is partial on
some boundaries. Five supplemental Linux cases were measurement failures,
not measured behavioral differences or resistance. Different authors and
nonrandomized timings prevent causal resistance or analysis-time rankings.
No claim of superiority over D/E, unrecoverability, or protection inferred from
a decompiler timeout is made. The unqualified G prototype is excluded.

Unchanged Unreal resources retain the earlier audit's encrypted Pak indexes,
encrypted nonempty Pak payloads and encrypted main asset IoStore containers.
The global.utoc metadata containers remain unencrypted. Outer ZIP/tar.gz
containers are ordinary compressed archives, and task/support Python code is
intentionally readable. Some third-party libraries retain debug information.

HARDENING_VALIDATION.json records the bounded compatibility scope. Linux GPU
graphics and complete native teardown remain unqualified for F. Earlier
experiments and package hashes retain their original identities. Existing
licensing and third-party terms are unchanged.
