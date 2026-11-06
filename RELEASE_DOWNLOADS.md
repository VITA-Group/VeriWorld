# VeriWorld Windows and Linux downloads

Current release: [paper-artifacts-2026-11-03-rc1](https://github.com/yanyanzheng96/VeriWorld-release/releases/tag/paper-artifacts-2026-11-03-rc1).
F Linux GPU graphics remains unqualified; see HARDENING_VALIDATION.json.

Each runtime archive is split into three **raw byte parts**, at most 1,900 MiB
per part. Download all three parts for your platform. Individual parts cannot
be extracted; merge them first. No special archive tool is needed for merging.

`manifest.json` records each part's size and SHA256, together with the complete
archive's size and SHA256. The downloader verifies both levels. It does not
execute or extract the downloaded archive.

## Automatic download and merge

Download `download_veriworld.py` and `manifest.json` from the same release into
one folder. With Python 3.10 or later, run one of:

```powershell
python download_veriworld.py windows --output downloads
```

```bash
python3 download_veriworld.py linux --output downloads
```

The repository and tag are read from the manifest. Interrupted downloads can
be resumed by running the same command again. Verified parts are reused. An
existing file with the wrong checksum is reported and is not silently replaced.
Keep at least 10 GiB of free space for parts plus the reconstructed archive,
and additional space for extraction. Parts are retained after merging.

For a private release, authenticate with GitHub CLI, download the assets with
`gh release download TAG --repo OWNER/REPO --dir release-files`, then use the
offline commands below. The Python downloader does not read stored credentials.

## Merge parts already downloaded

Place the three parts in `release-files`, then run:

```powershell
python download_veriworld.py windows --local-parts release-files --output downloads
```

```bash
python3 download_veriworld.py linux --local-parts release-files --output downloads
```

No network request is made when `--local-parts` is supplied. Do not mix parts
from different release tags.

## Run the tasks

**Windows:** extract `VeriWorld-Windows-hardened-F.zip` into a writable folder.
It includes the Python task code, supporting data and packaged Windows runtime.
Follow its README to create the Python environment, configure your model API,
and run `run_task.py --check` followed by a smoke check.

**Linux:** extract `VeriWorld-Linux-x86_64-hardened-F.tar.gz` and obtain the task
source archive supplied in the same release. Set `VERIWORLD_BUILD` to the
extracted `demo1/Binaries/Linux/demo1-Linux-Shipping`, or supply `--build`.
Use a non-root user and preserve executable permissions. A graphics-capable
NVIDIA GPU and working Vulkan drivers are required for rendered tasks.
Host `ffmpeg` and `ffprobe` must be on PATH for video tasks.

The Linux runtime's software recording path needs **libx264.so.164** (ABI 164).
If it is installed in a nonstandard directory, add that directory to
`LD_LIBRARY_PATH` before starting Python/the runtime. The cloud dependency
cache used in development is not included as a standalone environment.
Install dependencies for your Linux distribution; an older ABI 163 library
does not substitute for ABI 164.

Runtime smoke checks make real model API calls. Users supply their own API
endpoint, exact model identifier and credential. No credentials are bundled.
Representative task/seed/model checks are documented in the source release;
they are not a claim that every possible combination has been tested.

## Download statistics

GitHub exposes counters for each release asset:

```bash
python download_stats.py --repo OWNER/REPO --tag TAG
```

For private releases, add `--gh` and use an authenticated GitHub CLI session.
The counters represent asset downloads, not unique users or successful
installations. **Do not add three part counters and call the sum full-package
downloads.** Repeated downloads and resuming can also affect these counters.

The F main executables and declared release metadata replace their baseline
versions. Other runtime assets retain their original content. Current task
sources accompany the Windows bundle and the separate source archive. Code,
supporting data and Unreal/third-party content retain their respective terms.
This download helper does not change their license scope.
