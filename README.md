# VeriWorld

The release task code is maintained in [VITA-Group/VeriWorld](https://github.com/VITA-Group/VeriWorld).
This migration imports the published release source without changing its task
implementations, tests, fixtures or supporting result CSVs.
See [migration provenance](MIGRATION_PROVENANCE.json) and
[runtime protection scope](RUNTIME_PROTECTION.md).

The current Windows and Linux runtimes are the qualified F hardening builds.
The runtime binary is distributed as a GitHub Release at
[yanyanzheng96/VeriWorld-release](https://github.com/yanyanzheng96/VeriWorld-release/releases/tag/paper-artifacts-2026-11-03-rc1)
(tag `paper-artifacts-2026-11-03-rc1`); this repository hosts the task code and supporting data only.
The task source retains the audited parser-v4 scientific baseline and the
latest approved input-condition documentation. See [release scope](RELEASE_STATUS.md).

The task dataset is mirrored on Hugging Face at
[yanyanzheng96/VeriWorld](https://huggingface.co/datasets/yanyanzheng96/VeriWorld)
(CSV views of the task catalogue, fixtures and runtime recipes, with Croissant metadata).

Executable tasks for spatial reasoning in interactive 3D environments, with
deterministic verification and configurable model API clients.

See [task input conditions](docs/input_conditions.md) for the observations,
metadata and feedback supplied in each task's V/S/C or auxiliary configuration.

## Download the Windows or Linux runtime

The runtime binary is **not** stored in this repository; download it from the
VeriWorld-release GitHub Release:

**[Download release paper-artifacts-2026-11-03-rc1](https://github.com/yanyanzheng96/VeriWorld-release/releases/tag/paper-artifacts-2026-11-03-rc1)**

| Platform | Contents | Archive size | Download parts |
| --- | --- | ---: | ---: |
| Windows | Task code, supporting data, F runtime | See manifest | 3 |
| Linux x86_64 | F runtime; use this repository's Python tasks | See manifest | 3 |

The archives are raw-byte split into parts smaller than 2 GiB. Download
`download_veriworld.py` and `manifest.json` from that release into one folder:

```bash
python download_veriworld.py windows --output downloads
# Linux:
python3 download_veriworld.py linux --output downloads
```

The helper downloads/resumes the parts, merges them and verifies each part and
the complete archive against SHA256. Then extract the resulting ZIP or tar.gz.
See [the complete download, merge and verification guide](RELEASE_DOWNLOADS.md)
for offline merging and download statistics. The archive manifests identify every part and the complete stream. Runtime
assets are retained from the original packages; the main executables and
declared release metadata are updated and verified separately.

For Linux, clone this repository or use `VeriWorld-task-sources-hardened-F.zip`
from the same release. `libx264.so.164`, Vulkan-capable NVIDIA drivers and host
`ffmpeg`/`ffprobe` are required for the relevant rendering/video paths. See the
Linux section below and the download guide.

This release contains the parser-v4 task snapshot. Ongoing experiment results
are not automatically added to its supporting data. See [release scope](RELEASE_STATUS.md).
See [known release boundaries](KNOWN_ISSUES.md), including the Drop fixture
alignment and ObjectGrounding target/condition-label limitations.
No model API key is included; configure your own provider and exact model ID.

## Windows quickstart

1. Extract the entire Windows bundle to a writable directory. Keep the directory
   layout intact. Use an NVIDIA GPU with graphics support and an up-to-date
   driver. The engine has been tested on Windows; a CPU-only machine is not
   sufficient. The runtime includes its engine-side Python and GPU components.
2. Open PowerShell in the extracted directory and create a fresh host environment:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item model_configs.example.json model_configs.json
```

Python 3.13 is the tested Windows host version. The source uses Python 3.10+
syntax; other versions have not all been qualified. You do not need Unreal
Editor, a C++ toolchain, or an external CUDA toolkit for the supplied tasks.

3. Edit `model_configs.json`: set `name` to a label of your choice, `model` and
   `client_options.expected_model` to the exact identifiers your provider uses,
   and `base_url` to your provider endpoint. Keep `api_key` as
   `env:VERIWORLD_API_KEY`. Set your own credential in this PowerShell session:

```powershell
$env:VERIWORLD_API_KEY = Read-Host "Your model API key" -MaskInput
```

`-MaskInput` requires PowerShell 7. On Windows PowerShell 5, use your preferred
secret manager or set the environment variable without saving it in a script.
For the model routes tested in this release preparation, you may instead copy
`model_configs.paper.example.json`; these are examples, not bundled credentials
or a guarantee of provider availability. See `API_CONFIGURATION.txt` for
Anthropic-compatible protocols, request aliases and model identity checks.

4. Check the installation, then run one real model/engine round:

```powershell
.\.venv\Scripts\python.exe run_task.py --check
.\.venv\Scripts\python.exe run_task.py --task PlaceOnBottom --seed 0 --model example-model --smoke
```

Replace `example-model` with the exact **name** in your config. The runtime is
found automatically inside the Windows bundle. With the source-only ZIP, pass
`--build "C:\path\to\demo1-Win64-Shipping.exe"` or set `VERIWORLD_BUILD`.
The launcher starts the engine, waits for its WebSocket, executes the selected
public task CLI, and stops only processes belonging to that invocation. Do not
manually start another runtime on the same port.

Initial startup can take about a minute. The launcher allows engine warmup;
TunnelNav3D also checks geometry/camera readiness before sending its first
image. If that check times out, the attempt remains unscored and no model
request is made for the uninitialized scene.

`--check` makes no API call. `--smoke` makes real, billable model requests and
runs a one-round installation check. Its PASS/FAIL is a model outcome, not an
installation verdict, and it must not be included in paper benchmark scores.
An API exception, model identity mismatch, missing image, or runtime error is
an invalid/unscored attempt. A legal action followed by verifier FAIL is valid.

## All eleven task families

The same launcher accepts each task name below. Keep the model/config settings
above and change `--task` and `--seed`. Each command creates a new output folder.

| Task | Example seed(s) | Quickstart interface |
| --- | --- | --- |
| PlaceOnBottom | 0, 19 | V; `--condition S` or `C` also supported |
| BoxFold | 0, 19 | V; S/C also supported |
| MovingShooter | 0, 19 | V frame grid; S/C also supported; needs ffmpeg |
| LightWar | 0, 7 | V, random spawn/intensity/height; S/C also supported |
| MazeNavFPS | 0, 8 | VP batch, `--grid-size 5` |
| TunnelNav3D | 0, 2 | VP batch |
| Billiards | 0, 2 | Image/angle-speed interface |
| SceneGrounding | 0, 1 | Walk; `--level living_room` or `aiue` |
| ObjectGrounding | 0, 1 | Nine-marker grid; default wooden-spoon target |
| DropToTarget | 0, 1, 2 | V/S/C; Python surface function; needs ffmpeg |
| CurvyBilliards | 0, 2, 9 | V/S/C; launch parameters; needs ffmpeg |

Seeds follow the recorded task definitions. In legacy TunnelNav3D, the curved
geometry uses a fixed construction seed; different CLI seeds do not imply
independent tunnel geometries. See `TASK_SEED_NOTES.txt` before aggregating seeds.

For MovingShooter and the two computational-physics tasks, install a build of
**ffmpeg and ffprobe** and add their directory to PATH before starting. They are
host video utilities; they are not bundled in the dataset. `run_task.py --check`
prints the paths it finds. The launcher rejects these tasks if either is absent.

```powershell
.\.venv\Scripts\python.exe run_task.py --task DropToTarget --seed 0 --condition V --model example-model --smoke
.\.venv\Scripts\python.exe run_task.py --task ObjectGrounding --seed 1 --model example-model --target find_wooden_spoon_pot_view --smoke
.\.venv\Scripts\python.exe run_task.py --task MazeNavFPS --seed 8 --grid-size 5 --model example-model --smoke
```

Remove `--smoke` to use the selected CLI's normal budget, or explicitly pass
`--rounds N`. For maze/tunnel this sets model-call steps. An arbitrary quickstart
invocation is not automatically an exact reproduction of a paper table: use
the recorded task, model version, seed, condition, interface and budget.
`task_entrypoints.json` lists all 51 original entry points. Their `--help` and
adjacent READMEs expose the other variants. Navigation VP/RP variants and the
Photoreal marker interfaces are not silently relabeled as core V/S/C.

`--dry-run` prints the selected underlying CLI and arguments without executing
it. For concurrent runs, supply a different `--port` and `--output` for each.
The named output directory must not already exist. Begin with one instance;
increase concurrency only within available GPU memory. This release preparation
used at most five local instances.

## Outputs and reproduction

`runs/<task_seed_timestamp>/invocation.json` identifies the CLI and arguments.
`api/` retains each model input and raw response, plus returned model identity.
Task logs under `episode/` (Billiards uses its run working directory) retain
rendered images, actions, state and verifier outcomes. Never publish a private
API config or credential. Run directories include task-level `reproduce.json`
and `reproduce.py` where provided; inspect with `--dry-run` before replaying and
override runtime/config/output paths for your machine.

## Task dataset and separate supporting results

The published dataset consists of test-task definitions, seed/initialization
fixtures and runtime recipes. [Hugging Face](https://huggingface.co/datasets/yanyanzheng96/VeriWorld)
provides CSV views of 11 task-family definitions, 90 reference fixtures and 29
installation commands. `croissant.json` describes these three task record sets.
See [DATASET_METADATA.md](DATASET_METADATA.md) for record units, provenance,
binary dependencies and existing license scopes. `release_seed_fixtures.json`
and `task_entrypoints.json` retain the source task configuration and entry points.

The historical score CSVs and `SUPPORTING_DATA.txt` are separate paper-support
files, not the task dataset or a complete run archive. They remain unchanged.
`reproduce_statistics.py` operates on that historical export. To inspect the
published initialization fixtures without GPU or model API, run:

```powershell
.\.venv\Scripts\python.exe validate_release_seeds.py
```

## Linux source distribution

Use the same source ZIP with the separately supplied
`VeriWorld-Linux-x86_64-hardened-F.tar.gz` and pass `--build` with
the extracted `demo1/Binaries/Linux/demo1-Linux-Shipping` executable. Preserve
executable permissions (`chmod +x` if needed). Linux requires an NVIDIA graphics
GPU and the runtime's system libraries; A100 alone cannot render these scenes.
Use `python3 -m venv .venv`, `.venv/bin/python -m pip install -r requirements.txt`,
and the same `run_task.py` arguments. Host `ffmpeg`/`ffprobe` are still required.
Linux runtime qualification exists separately; this combined Windows ZIP does
not contain a Linux executable or claim a new Linux end-to-end run.

## Verification boundaries

`release_connectivity_scope.json` records the earlier 24 actual API/runtime
checks across 11 task families, representative seeds and ten model routes.
90 generator fixtures and 2,141 historical parameter matches are separate
offline checks. These checks do not establish every task/seed/model combination.
The shipping README/launcher checks are reported separately in the delivery
verification report. Kimi and pinned GPT-4o service failures remain unscored;
other historical model IDs can also be unavailable or return a different version.
No automatic model substitution is performed. Runtime/container and source/data
hashes are in the release manifests. The custom runtime's actual engine receipt
is UE 5.7.0; `legacy58` is a historical build label, not a stock UE 5.8 claim.

## CUDA parser update and separate parameter repeats

This revision includes the numeric parser repair (math imports, tuple/scalar
assignments and unambiguous JSON actions) and V3 fixed-time visual feedback.
Original supporting score CSVs remain the prior export; ongoing replacements
and parameter studies are separate and are not silently merged into those CSVs.

All released physics seeds are DropToTarget 0/1/2 and CurvyBilliards 0/2/9.
Use the commands above, changing `--seed`, to check each one. Beyond the default
`--variant standard`, the physics launcher accepts:

* `--variant rotate90`: rotate target/start and terrain coordinates by 90 degrees.
* `--variant mirror-x`: reflect target/start and terrain X coordinates.
* `--variant wide-target`: increase target radius by 25 percent, other settings fixed.

Example (a new parameter-study output, not an original paper score):

```powershell
.\.venv\Scripts\python.exe run_task.py --task CurvyBilliards --seed 0 --variant rotate90 --condition C --model example-model --rounds 5 --output runs/curvy_rotated_repeat1
```

For independent repeats, run the same command with fresh output names
`repeat2` and `repeat3`. Preserve all PASS and FAIL results. The reference
witnesses are used only by native qualification, never supplied to the model.
Review `veriworld/benchmark/computational_physics/paper_tasks/README.md` for
the exact action subset and variant scope. No API credentials are included.


## Licensing and citation

The project license and notices are retained in [LICENSE](LICENSE) and
[NOTICE.md](NOTICE.md). The license is the PolyForm Noncommercial License 1.0.0: use is limited to noncommercial purposes and **commercial use is prohibited** (see NOTICE.md Section 2). [DISTRIBUTION_CHANNEL.txt](DISTRIBUTION_CHANNEL.txt)
records the owner-authorized GitHub release channel. Unreal Engine and
third-party content retain their own terms; the project license does not
grant ownership of or supersede terms for third-party material.
The task metadata describes the existing artifact-specific license scopes;
it does not grant new rights. See DATASET_METADATA.md. Use CITATION.cff to cite VeriWorld.


## Required citations

Academic follow-up work must explicitly acknowledge VeriWorld in the body of
the work as well as provide both citations below. See NOTICE section 2.2.1 for
the existing attribution requirement.

If you use VeriWorld in published work, you must cite **both** of the following:

1. **VeriWorld** itself (NeurIPS 2026; see also [CITATION.cff](CITATION.cff)):

```bibtex
@inproceedings{veriworld2026,
  title={{VeriWorld: A Verifiable Visual SWE-Bench for Spatial Reasoning in 3D Environments}},
  author={Zheng, Yan and Peng, ShengYun and Liang, Xuyao and Cong, Xiaoyan and Chen, Nuo and Liu, Bangya and Wang, Zihan and Cong, Wenyan and Fan, Zhiwen and Wang, Zhangyang},
  booktitle={Advances in Neural Information Processing Systems},
  year={2026}
}
```

2. **VoxelCodeBench** - the companion work that introduces the voxel-code generation framework on which VeriWorld's 3D task environments are built. Citation is required for all uses of VeriWorld, not just work that touches the voxel API directly.

```bibtex
@article{zheng2026voxelcodebench,
  title={VoxelCodeBench: Benchmarking 3D World Modeling Through Code Generation},
  author={Zheng, Yan and Bordes, Florian},
  journal={arXiv preprint arXiv:2604.02580},
  year={2026}
}
```

## Contact

- Build issues / questions: `yan.zheng.mat@gmail.com`
- Licensing / commercial inquiries: `yan.zheng.mat@gmail.com`
