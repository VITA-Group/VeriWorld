# Known release boundaries

These findings are retained in the 2026-11-04 F runtime release. They are not
model failures, and updating the runtime does not change historical results.

| Area | Verified boundary | Treatment in this release |
| --- | --- | --- |
| ObjectGrounding book alias | The additional `find_book_living_view` definition uses `sm_decor_5`, which was absent in the tested scene after settled queries. A similarly named object has not been established as equivalent. | Retain the known failure and do not advertise this extra target as qualified. It is outside the ten-target formal panel. No guessed alias replacement is included. |
| Linux graphics | The F binary passes the defined non-graphics native scope; its GPU rendering/video path was not qualified in the local hardening study. | Earlier GPU results remain attached to their earlier executable hashes. Use the F qualification scope without inferring new graphics coverage. |

The published task code and tests remain the audited parser-v4 scientific
baseline. Experiment controllers, private credentials, raw runs and tentative
parameter changes are not imported into the repository. The updated input
guide, release metadata, checksums and qualified F binaries are versioned
separately from the historical task and result provenance.
