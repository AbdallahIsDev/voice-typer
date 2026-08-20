# Plan: Drop torch, shrink the installer, split core vs ML runtime pack

> **Status:** Rewritten 2026-08-13 after a 15-agent deep investigation of the
> voice-typer codebase. Supersedes the 2026-08-12 version, which was
> Windows-skewed, under-specified the worker-exe architecture, made
> inaccurate claims about prewarm/auto-update/NVIDIA DLLs, and treated Qwen
> as an LLM (it is an ASR engine).
>
> This is the **master plan** for the installer split and torch removal. The
> companion document `PLAN_ONNX_INTEGRATION.md` is the **detailed technical
> reference** for the ONNX migration itself (VAD hidden-state threading,
> Parakeet TDT decoding, Qwen scope correction, CUDA error classifier
> preservation). Read both together — this plan references the companion for
> engine internals and focuses on the packaging, distribution, CI/CD, and
> cross-platform concerns.

---

## SCOPE — "remove torch" means the project, NOT your device (READ FIRST)

> **CRITICAL – user-set boundary (2026-08-13):** Whenever this master plan
> (or its companion `PLAN_ONNX_INTEGRATION.md`) says "drop torch", "remove
> torch", or "torch removal", it means **removing torch as a PROJECT
> dependency and source-code import** — `pyproject.toml`,
> `requirements-lock.txt`, `voice_typer/**/*.py`, Nuitka invocation flags, and
> the frozen sidecar/worker binaries. It does **NOT** mean uninstalling torch
> from the user's device.
>
> **The implementing agent MUST NOT, at any time, under any phase:**
> - run `pip uninstall torch` / `torchvision` / `nvidia-*` or any equivalent
>   (including `uv pip uninstall`),
> - delete, prune, or modify anything inside the user's **`.venv`** (or any
>   virtualenv / conda env / user / system site-packages),
> - delete or modify any installed torch (CPU or GPU) package, wheel, cache
>   (`~/.cache/torch`, `~/.cache/huggingface`), or DLL from the device,
> - touch or "clean up" torch-related files outside the project repo tree.
>
> The migration edits **only** files inside this repository. Dropping torch
> from `pyproject.toml` / the lockfile / the frozen build is a *project*
> change and does **not** remove torch from the user's environment — the two
> are decoupled.
>
> **Whether the user later uninstalls torch from their device is the USER's
> decision.** Treat it as **OUT OF SCOPE**. The agent finishes the project
> migration, leaves the user's installed torch (CPU + GPU) fully intact, and
> the existing torch code paths keep working today — so a migration regression
> never destroys the user's pre-existing environment.
>
> If an agent believes an environment change (e.g. uninstalling torch to
> reclaim disk, or a `pip` failure because torch is still installed) is
> necessary, that is a **skip**: record it in `worklog.md` per the
> AGENTS.md audit-trail format and leave the environment alone.

---

## 0. What changed since the 2026-08-12 version

The 2026-08-12 version was approved by the user as the direction. After
investigation, the direction stands (drop torch, split the installer, ship a
runtime pack) but **many specific claims were wrong or under-specified**.
This rewrite corrects them:

| # | 2026-08-12 claim | Reality | Fix |
|---|---|---|---|
| 1 | "Qwen (LLM) converts to `onnxruntime-genai`. Real work: chat-template + sampling loop." | Qwen is an **ASR engine** (`qwen_asr.Qwen3ASRModel.transcribe(audio, sr)`). `onnxruntime-genai` has no Qwen3-ASR builder. | Defer Qwen to a separate phase. See `PLAN_ONNX_INTEGRATION.md` §4. |
| 2 | "Prewarm moves INTO the pack." | Prewarm is a **separate Nuitka-frozen binary** with 3 OS-level schedulers (Windows LogonTrigger, macOS LaunchAgent, Linux systemd). Moving it obsoletes 1,532 LOC of process tracking + 24 OS-scheduler tests + 3 build scripts. | §6 below: prewarm becomes a startup phase of the worker exe, not a separate binary. The OS schedulers are deleted. |
| 3 | "Torch's ~40,000 files (454 MB)." | Torch 2.13.0 ships **12,118 files / 1,044 MB total**. Only 37 files match prewarm's suffix filter. The 40k number is from a stale comment at `cache_probe.py:221`. | Corrected in §6. |
| 4 | "onnxruntime already sits in your venv unused." | It's a **transitive dep of `faster-whisper`**. After the split, faster-whisper moves to the pack, so the slim core loses it unless declared explicitly. | §5.3: declare `onnxruntime` explicitly in the slim core's deps for VAD. |
| 5 | "GPU variants source DLLs from the onnxruntime-gpu package." | `onnxruntime-gpu` does **not** bundle NVIDIA DLLs — it expects system CUDA. | `PLAN_ONNX_INTEGRATION.md` §6.1. Keep `nvidia_dll_paths.py` with `nvidia/*` paths. |
| 6 | "It already probes the network for update checks — same mechanism." | `docs/auto-update-feature.md` is explicitly **"NOT IMPLEMENTED (design only)"**. No GitHub Releases publishing step exists in CI. | §10: build the auto-update mechanism from scratch. |
| 7 | "The exact same websocket bridge." | Today's sidecar IS the WS server; Tauri is the client. Adding a worker means the slim core talks to TWO processes — a NEW second hop. | §7: define the worker IPC architecture explicitly. |
| 8 | "Three allowlists in lockstep." | There are **four** allowlists. The fourth (`ALLOWED_EVENT_TYPES` at `event_protocol.rs:49`) has no parity test. | §9.4: add the fourth allowlist + parity test. |
| 9 | "Pack auto-downloads on first launch by default... no progress bar, no dialog, no toast." | Conflicts with the GDPR-driven `huggingface_consent` gate (`service/model.py:854-912`, CR-11). Pack download phones home to GitHub Releases, revealing user IP to Microsoft. | §8.4: pack download is consent-gated, same as model downloads. |
| 10 | "Installer drops to ~180 MB; disk drops from ~850 MB to ~430 MB." | Split adds a SECOND Python runtime (~50 MB) inside the worker onefile. Post-split disk is ~530 MB, not ~430 MB. | §5.5: corrected disk footprint. |
| 11 | "C-CI-8/NU-106 retire." | NU-106 is NOT a AGENTS.md rule — it's an inline evidence tag in the workflow YAML, cited in C-CI-8's rationale. Only C-CI-8 is the rule. | §11.2: correct the rule reference. |
| 12 | "Size gate asserts the sidecar stays ≤ ~185 MB." | No such size gate exists in CI today. `tauri-windows-build.yml:509-510` only `Write-Host`s the size. | §11.5: add the size gate. |
| 13 | "`--nofollow-import-to=torch` is added." | The bare top-level flag is already in `build_prewarm_windows.sh:157`. The sidecar scripts use granular `torch._dynamo`/`_inductor` exclusions, NOT the bare flag. | `PLAN_ONNX_INTEGRATION.md` §8.1. |
| 14 | "Your own Parakeet engine currently CANNOT run... the transformers library is excluded from the frozen build." | True. Verified: `pyproject.toml` excludes `transformers` from the Nuitka follow set. | Unchanged — this is the motivation for the ONNX migration. |
| 15 | "You already downloaded the ONNX version." | False. No `*.onnx` files, no `grikdotnet` reference, no manifest entry anywhere in the repo. | `PLAN_ONNX_INTEGRATION.md` §3.2. |
| 16 | 18 edge cases listed. | 0 fully handled by existing code, 5 partially handled, 13 unhandled. No tests exist for any. | §8: re-spec each edge case against the real codebase. |

---

## 1. The problem — current state (verified)

### 1.1 Installer size

The Windows installer is ~253-259 MB (NSIS 259 MB, MSI 257 MB — same content,
two installer wrappers). The Linux `.deb`/`.rpm`/AppImage and macOS
`.dmg`/`.app` are comparable in size. Inside the sidecar, measured from the
real dev environment:

| Component | What it is | Installed | In installer | Verdict |
|---|---|---|---|---|
| torch (CPU) | Deep-learning framework. Used only for Silero VAD + Parakeet + Qwen. | 454.5 MB | 87.1 MB | **REMOVE** |
| ctranslate2 | Lean whisper engine. Does NOT depend on torch. | 59.8 MB | 11.4 MB | **KEEP (in pack)** |
| faster-whisper | Whisper library talking to ctranslate2. | 1.3 MB | 1.0 MB | **KEEP (in pack)** |
| scipy + libs | Audio math: filters, resampling. | 115.5 MB | 31.7 MB | keep (pack) |
| av + libs (PyAV) | Audio/video file reading. | 65.9 MB | 21.1 MB | keep (pack) |
| pyrnnoise | RNNoise background-noise removal. | 14.2 MB | 12.7 MB | keep (pack) |
| numpy + libs | Basic math. | 43.0 MB | 9.7 MB | keep (pack) |
| PIL, huggingface_hub, glue libs | Icons, model downloader, plumbing. | ~30 MB | ~11 MB | keep (core + pack) |
| Compiled app code + Python runtime | Your own code, compiled, plus Python itself. | ~80 MB | ~50 MB | keep (core) |

### 1.2 Two structural problems

1. **Big download scares users.** A 259 MB installer looks bloated before users
   even try it.
2. **Every update = full re-download.** The Python sidecar is built by Nuitka
   as a "onefile": everything (Python, torch, whisper, all libraries) is
   compressed into ONE single file. A one-line bug fix forces users to
   re-download ~200 MB because there is no way to "diff" compressed blobs.

---

## 2. The goal (revised)

1. **Remove torch** from VAD + Parakeet (+ Qwen if/when feasible — see
   `PLAN_ONNX_INTEGRATION.md` §4). Installer drops to ~180 MB; disk drops
   from ~850 MB to ~530 MB (not ~430 MB — the worker onefile includes its
   own Python runtime).
2. **Split the app into two pieces**:
   - **Slim core** (~30-45 MB): the app itself — UI shell, settings, mic
     test, cloud engines, tray, hotkeys. Updates cost ~35 MB forever.
   - **ML runtime pack** (~180-200 MB worker onefile, ~450 MB unpacked):
     all offline-transcription machinery (engines + libraries + VAD +
     Parakeet tokenizer). Downloaded ONCE, silently, in the background,
     with consent (see §8.4).
3. Keep everything local and offline-capable. Cloud providers
   (Groq/OpenAI/Deepgram) keep working with or without the pack.

---

## 3. Phase 1 — ONNX migration: remove torch

**The detailed technical reference for this phase is
`PLAN_ONNX_INTEGRATION.md`.** This section is a summary; read the companion
for engine internals, code sketches, and test lists.

### 3.1 Engine decisions

| Engine | Decision | Why | Companion § |
|---|---|---|---|
| Silero VAD → ONNX | ✅ Convert (Phase 1a) | `silero_vad.onnx` ~2 MB, runs on `onnxruntime`. **Hidden state must be hoisted into Python** — the JIT module manages it internally, ORT does not. | A |
| Parakeet → ONNX | ✅ Convert (Phase 1b) | Use `onnx-asr` library (Option B-1). TDT decoding handled by the library. Makes Parakeet shippable for the first time. | B |
| Qwen → ONNX | ⏸ Defer (Phase 1d) | **Qwen is an ASR engine, not an LLM.** `onnxruntime-genai` is the wrong tool. Defer until `qwen_asr` maintainer confirms ONNX support, or export manually (Option C-2). | C |
| Whisper / faster-whisper / ctranslate2 | ❌ Keep as-is | ctranslate2 does NOT depend on torch (verified via lockfile). | — |
| numpy / scipy / av / pyrnnoise / PIL | keep | Unrelated to torch. | — |

### 3.2 Torch removal scope (honest)

The 2026-08-12 version claimed "TOTAL torch removal." Given the Qwen
deferral, the honest scope is:

- **Phase 1c:** torch removed from VAD, Parakeet, and the 7 supporting
  modules (`asr_utils`, `resource_probe`, `diagnostics_export`,
  `nvidia_dll_paths`, `transcription`, `prewarm/__init__`,
  `prewarm/cache_probe`).
- **Phase 1d:** torch removed from `qwen_engine.py` + `transformers` dep
  dropped from `pyproject.toml`.

**The "TOTAL removal" gate** (`grep -ri "import torch" voice_typer` → zero
hits) is only achievable after Phase 1d. Between Phase 1c and 1d, the gate
is "zero hits except `qwen_engine.py`."

### 3.3 The 30+ torch import sites the old plan missed

The 2026-08-12 version listed 10 files. The investigation found **30+
additional sites** that must be swept:

- `scripts/diagnostics.py:177` — actual `import torch` (the CLI diagnostics
  producer, separate from the in-app `diagnostics_export.py`).
- `tests/conftest.py` — ~190 lines of `mock_torch` plumbing
  (`_FakeOutOfMemoryError`, `_FakeTensor`, `_build_mock_torch()`,
  `real_torch` marker, session-scope fixture).
- 8 literal `import torch` statements in tests
  (`tests/test_vad_dtype_optimization.py` ×7,
  `tests/test_transcription_fallback.py:127`).
- `scripts/build/voice-typer.spec:112-113,274` — PyInstaller fallback
  references `silero_vad.jit`, `"transformers"`, `"transformers.models"`,
  `"accelerate"`.
- `MANIFEST.in:20-26` — `include voice_typer/server/silero_vad.jit`.
- `pyproject.toml:146` — `"transformers>=5.14.1,<6.0"` (pulls torch
  transitively).
- `pyproject.toml:791` — `"transformers.*"` mypy override.
- `pyproject.toml:632` — `"ignore::DeprecationWarning:torch.jit._serialization"`
  pytest filter.
- `requirements-lock.txt` — 18 Linux `nvidia-*` packages + `triton`/`sympy`/
  `mpmath`/`networkx` + `transformers`/`tokenizers`/`safetensors` (transitive).
- 6 build shell scripts with `--nofollow-import-to=torch.*` /
  `--module-parameter=torch-disable-jit=no` flags (sidecar + prewarm, ×3
  platforms).
- `.github/workflows/tauri-windows-build.yml:422-475, 517-535` — full
  NU-106 flag block.
- 13 doc files with 30+ torch references (including
  `docs/adr/0020-desktop-runtime-migration-analysis.md:954` which says
  "vad.py, silero_vad.jit — Unchanged" — becomes false).
- `tests/tauri/test_config_script_drift.py:437-512` —
  `TestNuitkaSidecarBuildsDoNotExcludeTorchDistributed` HARD-ENFORCES the
  `--module-parameter=torch-disable-jit=no` flag and FORBIDS 5
  `--nofollow-import-to=torch.*` flags. Retiring the flag REQUIRES
  deleting/updating this test class.

### 3.4 Prewarm impact — corrected

The 2026-08-12 version said "prewarm gets FASTER" and cited "torch's ~40,000
files (454 MB)." Both claims are wrong:

- Torch 2.13.0 ships **12,118 files / 1,044 MB total**. Only **37 files /
  945 MB** match prewarm's suffix filter (`{.pyc,.so,.pyd,.dll,.dylib,.json,.txt}`
  — `.py` is deliberately excluded). The 40k number is from a stale comment
  at `cache_probe.py:221`.
- `_warm_imports` lives at `cache_probe.py:281-365` (85 lines, not 21 as the
  old plan's "lines 282-302" reference implied).
- The package list is **DYNAMIC**, not static. For the default `whisper`
  backend, torch + transformers are NOT warmed (STARTUP-3 optimization at
  `cache_probe.py:298-319`). The old plan's framing implied all users pay
  the torch tax; the default backend already skips it.

After migration, the warm list becomes `onnxruntime + ctranslate2 +
numpy/scipy` (~200 MB total, far fewer files). Prewarm is expected to be
faster, but **there is no prewarm benchmark today** (`bench/bench_startup.py`
measures tray-import time, not prewarm). The plan should commit to a target:

- `bench/bench-baseline.json` has `bench_startup.cold_import.first_run_ms =
  800.0 ms`. The plan should commit to ≤ 600 ms post-migration (25% improvement).

---

## 4. Phase 2 — the split: slim core + runtime pack

### 4.1 What "splitting" means

Today the installer is one big box. We split into two:

- **Box A — slim core.** Everything except offline-transcription machinery:
  window, settings, tray, hotkeys, mic test, cloud engines, config.
- **Box B — runtime pack (worker exe).** A frozen Nuitka onefile containing:
  onnxruntime, ctranslate2 + faster-whisper, numpy/scipy, av, pyrnnoise,
  Silero VAD ONNX, Parakeet tokenizer, the prewarm logic (absorbed as a
  startup phase — see §6).

### 4.2 Why a "worker exe" and not loose files

A compiled Python program knows where its libraries are only because they
were baked in at build time. Packing Box B as one frozen worker program
means: it always finds its own libraries, nothing can be accidentally lost,
and it can be verified as one file via SHA-256.

### 4.3 The worker is NOT "the same kind of thing as the current sidecar"

The 2026-08-12 version said "the worker is the same kind of thing as the
current sidecar, just smaller in scope." This is architecturally wrong and
must be corrected:

- **Today:** Tauri spawns ONE sidecar. The sidecar IS the websocket server;
  Tauri is the client (`src-tauri/src/sidecar/spawn/release_mode.rs:49,71`).
- **After split:** Tauri spawns the slim-core sidecar (same as today) AND
  the worker exe (new). The slim-core sidecar talks to the worker over a
  SECOND websocket connection. This is a **new 1-host↔2-processes pattern**,
  not "the same bridge."

### 4.4 Worker exe build — concrete specification

The 2026-08-12 version gave no Nuitka flags, no entry point, no build script
for the worker. This rewrite specifies them:

- **Entry point:** `voice_typer/worker/__main__.py` (new). A thin launcher
  that sets up the websocket server, loads the engines on demand, and
  exposes the transcription API.
- **Build script:** `scripts/build/build_worker_windows.sh` (new), plus
  `build_worker_linux.sh` and `build_worker_macos.sh`. Modeled on the
  existing `build_prewarm_*.sh` scripts (which are the closest existing
  analog — a separate Nuitka onefile).
- **Nuitka flags:**
  ```bash
  nuitka --standalone --onefile \
    --include-data-files=voice_typer/server/silero_vad.onnx=voice_typer/server/silero_vad.onnx \
    --include-package=onnxruntime \
    --include-package=ctranslate2 \
    --include-package=faster_whisper \
    --include-package=numpy \
    --include-package=scipy \
    --include-package=av \
    --include-package=pyrnnoise \
    --nofollow-import-to=torch \
    --nofollow-import-to=transformers \
    --onefile-tempdir-spec=%LOCALAPPDATA%/voice-typer/worker-tmp \
    --output-filename=voice-typer-worker-$TARGET_TRIPLE.exe \
    voice_typer/worker/__main__.py
  ```
- **Tauri `externalBin`:** add the worker to ALL FIVE platform tauri conf
  files (`tauri.windows-x86_64.conf.json`, `tauri.windows-aarch64.conf.json`,
  `tauri.linux-x86_64.conf.json`, `tauri.linux-aarch64.conf.json`,
  `tauri.macos.conf.json`). Also add to `plugins.shell.scope` in
  `tauri.conf.json:60-62,127-138`.
- **macOS bundle id:** `com.voicetyper.worker` (parallel to the host's
  `com.voicetyper`).
- **PyInstaller fallback:** add a `voice-typer-worker.spec` (parallel to
  `voice-typer.spec`) for environments where Nuitka fails.

### 4.5 How updates work after the split

- The pack gets its **own version number** (e.g., "runtime-pack v3"),
  separate from the app's version. This version only changes when the
  engines/libraries inside it change — which is rare.
- **App update** → download the new slim core only (~35 MB). The pack on
  the user's disk keeps working.
- **Pack update** → the app checks the pack version on launch, and if a
  newer pack exists, downloads it in the background (with consent — see §8.4).

### 4.6 Integrity — verify-before-use

The 2026-08-12 version said the pack "reuses existing `tauri-binaries.json`
machinery." This is misleading — the codebase has **three separate
integrity systems**, and the pack would be a fourth:

1. `model_hashes.json` + `verify_model_integrity()` — for HF model files.
2. `tauri-binaries.json` + `verify_tauri_binary_or_skip()` — for the Tauri
   host binary (only 3 entries: `voice-typer-tauri` / `.exe` / `.app`).
3. `voice_typer/server/native/binaries.json` + `update_native_manifests.py`
   — for the native hotkey listener.

**Decision:** create a fourth manifest `pack-manifest.json` at
`%LOCALAPPDATA%\voice-typer\runtime-pack\<version>\pack-manifest.json`
(schema: `{version, sha256, files: [{name, sha256, size}], min_proto_version}`).
Do NOT extend `tauri-binaries.json` — its schema is scoped to a single host
binary spawned by the launcher, and extending it creates coupling between
unrelated systems.

The `autostart_launcher.py` verify-before-use logic is reused as a pattern
(not as code) — the worker launcher gets its own `verify_pack_or_skip()`
function modeled on `verify_tauri_binary_or_skip()`.

### 4.7 Where the pack lives (cross-platform)

The 2026-08-12 version hardcoded `%LOCALAPPDATA%\voice-typer\runtime-pack\`.
This is Windows-only. The actual paths (per `src-tauri/src/platform/paths.rs:163-356`):

| Platform | Path |
|---|---|
| Windows | `%LOCALAPPDATA%\voice-typer\runtime-pack\<version>\` |
| Linux | `$XDG_DATA_HOME/voice-typer/runtime-pack/<version>/` (default `~/.local/share/voice-typer/runtime-pack/<version>/`) |
| macOS | `~/Library/Application Support/voice-typer/runtime-pack/<version>/` |

The worker exe path is resolved per-platform via a new
`src-tauri/src/platform/worker_path.rs` (modeled on `paths.rs`).

### 4.8 The download experience (revised)

- First launch → pack download starts in the background **after consent**
  (see §8.4). Behaves like infrastructure (think WebView2).
- **No progress bar in the main UI**, but a small "Preparing offline engine…"
  line appears in the mic test / transcription areas if the user tries to use
  offline transcription before the pack is ready.
- Safety net: if the pack is missing and the user tries offline
  transcription → the download starts (or resumes) silently and the UI shows
  the "Preparing…" line.
- Metered connection: a setting "Download offline engine later", default off
  (auto-download). On Windows, detect metered via `NLM` API; on Linux/macOS,
  no reliable detection — the setting is manual.

### 4.9 What works without the pack

| User action | Pack present | Pack missing |
|---|---|---|
| Mic test page | full levels + VAD sensitivity | RMS meter works; VAD "smartness" degrades silently (VAD is in the pack) |
| Cloud transcription (Groq/OpenAI/Deepgram) | works | **works — cloud never needs the pack** (verified: `cloud_engines.py` + `llm_polish.py` have zero torch/ctranslate2/onnxruntime imports) |
| Local whisper / Parakeet | instant | silent download starts, "Preparing…" line, then works |
| Check for updates | downloads slim core only | same |

**Correction:** the 2026-08-12 version said "mic test... VAD smartness
degrades silently." The actual mic test (`voice_typer/server/service/microphone_test.py`)
uses RMS only — no VAD. The degradation is: VAD-dependent features (smart
silence trimming) silently skip, but the level meter works. The plan should
not over-promise VAD features in mic test.

---

## 5. Disk footprint (corrected)

### 5.1 Today (single installer)

- Installer: ~253 MB (NSIS) / ~257 MB (MSI).
- Installed: ~850 MB (torch 454 + ctranslate2 60 + scipy 116 + av 66 +
  pyrnnoise 14 + numpy 43 + glue 30 + app 80).

### 5.2 After Phase 1 (torch removed, single installer)

- Installer: ~180 MB (torch's 87 MB gone).
- Installed: ~430 MB (torch's 454 MB gone).

### 5.3 After Phase 2 (split, both installed)

- Slim core installer: ~35 MB.
- Pack (worker onefile, downloaded): ~180-200 MB compressed.
- Slim core installed: ~80 MB (app + Python runtime + glue libs).
- Pack installed (unpacked): ~450 MB (onnxruntime + ctranslate2 + scipy + av
  + pyrnnoise + numpy + engines + **second Python runtime ~50 MB**).
- **Total installed: ~530 MB** (not ~430 MB as the old plan claimed).

### 5.4 Why the second Python runtime

The worker is a Nuitka onefile — it bundles its own Python runtime (~50 MB)
because Nuitka compiles Python to C and links against libpython. This is
unavoidable for a standalone frozen binary. The slim core also has its own
Python runtime. Hence two runtimes.

### 5.5 Net win

- Installer download: 253 MB → 35 MB (slim core) + 180 MB (pack, once) =
  215 MB first-time, 35 MB thereafter.
- Updates: 200 MB → 35 MB (slim core) per release. Pack updates are rare.
- Disk: 850 MB → 530 MB (torch gone, second runtime added).

The disk win is smaller than the old plan claimed, but the **update win**
is the real prize: 35 MB per release vs 200 MB per release.

---

## 6. Prewarm — re-architected (not just "moved")

### 6.1 What prewarm is today

Prewarm is a **separate Nuitka-frozen binary** (`prewarm-<target-triple>[.exe]`)
launched by THREE OS-level schedulers:

- Windows: Task Scheduler with LogonTrigger.
- macOS: LaunchAgent (`RunAtLoad`).
- Linux: systemd user timer (`OnBootSec=10s`).

It is bundled as a Tauri **resource** (not `externalBin`), and the Rust
sidecar only passes its path to Python via the `VOICE_TYPER_PREWARM_EXE` env
var (`src-tauri/src/sidecar/spawn/prewarm.rs`).

The prewarm binary warms the OS file cache by paging the heavy libraries'
files into RAM before the user actually transcribes. Today it pages
torch's 37 warmable files (945 MB) + transformers (when the backend is
Parakeet/Qwen).

### 6.2 What "moves INTO the pack" actually means

The 2026-08-12 version's "prewarm moves INTO the pack" hides a substantial
re-architecture. Three options:

#### Option P-1 (recommended): prewarm becomes a worker startup phase

The worker exe runs a prewarm phase on startup (before accepting the first
transcription request). This eliminates:

- `prewarm-<triple>[.exe]` binary (3 build scripts deleted).
- `src-tauri/src/sidecar/spawn/prewarm.rs` (54 LOC).
- `prewarm_resolver.py` (242 LOC).
- `task_scheduler.py`, `prewarm_scheduler_posix.py`.
- PID-file + sentinel + completion-event machinery (1,532 LOC across
  `process_tracker.py` + `completion_events.py` + `paths.py`).
- 24 OS-scheduler tests (`mig15/test_prewarm_logontrigger_windows.py`,
  `mig16/test_prewarm_launchagent_macos.py`,
  `mig17/test_prewarm_systemd_linux.py`).
- `test_uninstall_prewarm_cleanup.py` (387 lines).
- `test_prewarm_spawn_resolver.py` (227 lines).

The prewarm logic itself (`cache_probe.py:_warm_imports`) moves into the
worker's startup sequence. The `_warm_imports` package list becomes
worker-internal (onnxruntime + ctranslate2 + numpy/scipy).

**Trade-off:** the worker takes longer to start (it warms before accepting
requests). But the user never sees this — the worker starts in the
background after the pack download completes, and by the time the user
clicks "transcribe," the worker is warm.

#### Option P-2: keep prewarm as a separate binary

Contradicts the "one frozen worker program file" claim in §4. Not
recommended.

#### Option P-3: `prewarm_now` RPC over the worker's `--ws`

The worker exposes a `prewarm_now` RPC that the slim core can call. The
worker warms itself on-demand. This is a variant of P-1 with lazier warming.

### 6.3 Decision: P-1

Prewarm becomes a worker startup phase. The OS schedulers are deleted. The
existing prewarm tests are deleted (or rewritten as worker-startup tests).
The `bench/bench_startup.py` target is updated to measure worker-startup
time (including prewarm), not tray-import time.

---

#### §6.3 addendum 2026-08-14: Cache Status surface restored (user-facing)

P-1's retirement collateral removed the Settings → About **"Cache Status"
card** along with the standalone-prewarm process machinery
(`get_prewarm_status` / `run_prewarm` / `open_prewarm_log`). The user
re-opened that decision: the card is a user-facing feature, so the IPC
surface was **restored verbatim from commit 5a319872** (per user
instruction: restore the deleted surface, do not reimplement, do not
revert any unrelated session work). What was restored:

- `voice_typer/server/prewarm/status.py` — `get_prewarm_status`,
  `_probe_cache_status` + TTL probe cache, file read/write helpers;
  adapted to read/write the **worker status file** `prewarm-status.json`
  under the config dir (written by the worker warm phase — see
  `voice_typer/worker/_ws_server.py`) instead of the deleted
  sentinel/PID machinery. `prewarm_running` field dropped (no more
  process-tracker).
- `_handle_get_prewarm_status` + `_handle_open_prewarm_log` +
  `_handle_run_prewarm` in `status_handlers.py`; registry +
  rate-limiter + TS/Rust allowlist entries;
  `GetPrewarmStatusRequest` / `OpenPrewarmLogRequest` /
  `RunPrewarmRequest` types; `PrewarmAndUpdates.tsx` Cache Status
  card (badge, rows, **Run Prewarm Now**, Refresh, View log).
- `open_prewarm_log` now opens `worker.log` (the retired `prewarm.log`
  no longer exists).

`run_prewarm` was restored the same day (§6.3 addendum 2nd half) as a
**RE-IMPLEMENTATION**, not a verbatim copy: the pre-P-1 handler spawned
a detached `pythonw -m voice_typer.server.prewarm --force` subprocess,
and that module is deleted by design (P-1). The restored handler
instead runs the warm phase in-process via
`prewarm.status.run_prewarm_now()` — `warm_imports_for_worker` (the
same file-paging pass the worker runs at startup) on a daemon thread,
plus a status-file refresh — so "Run Prewarm Now" re-warms the OS
standby cache on demand with zero deleted machinery. Start/stop of the
AUTOMATIC warm phase remains the `fast_startup` toggle.

Counts after the full restoration: Python registry **71** (69 renderer
+ 2 host-only), TS **69**, Rust **67** (`EXPECTED_COMMANDS` in
`tests/tauri/mig19/test_phase4_validation.py` = 67; see
ADR-0020 §16 addendum 2026-08-14).

---

## 7. Worker IPC architecture (new — was under-specified)

### 7.1 The new 1-host↔2-processes pattern

Today: Tauri (client) → sidecar (WS server). The `SidecarState` struct
(`src-tauri/src/sidecar/spawn.rs:200-203`) is hardwired for ONE child, ONE
`ws_tx`, ONE heartbeat task, ONE pending map, ONE `respawn_in_progress`
flag.

After split: Tauri (client) → slim-core sidecar (WS server) AND slim-core
sidecar (client) → worker (WS server). The worker is a second WS server
that the slim-core sidecar connects to as a client.

### 7.2 What the worker needs

- **WS server:** the worker listens on a port (negotiated via the same
  `--ws` mechanism the sidecar uses). The slim-core sidecar connects as a
  client.
- **Heartbeat:** the worker needs its own heartbeat (parallel to
  `src-tauri/src/sidecar/ws/heartbeat.rs`). The slim-core sidecar sends
  pings; the worker responds.
- **Respawn scheduler:** if the worker crashes, the slim-core sidecar
  restarts it (parallel to `respawn_scheduler.rs`). Must NOT trip the
  sidecar's circuit breaker.
- **Auth:** the worker inherits the bearer-token / `hmac.compare_digest`
  pattern from `voice_typer/server/ipc/auth.py:61-70`. A shared token is
  passed via env var (same as today's sidecar auth).
- **Shutdown:** the worker shuts down when the slim-core sidecar shuts down
  (graceful via WS close, or forceful via SIGTERM/taskkill).
- **Single-instance:** the worker takes a lock file to prevent parallel
  spawns (parallel to `VoiceTyperSingleInstance`).

### 7.3 Worker lifecycle (the plan's biggest hole)

The 2026-08-12 version never specified: when does the worker start? When
does it stop? Does it stay running between transcriptions?

**Decision: long-lived worker, started after pack download.**

- The worker starts once (after the pack is downloaded + verified) and
  stays running for the app's lifetime.
- It holds ~450 MB of RAM (unpacked pack + loaded models).
- If RAM pressure is high, the slim-core sidecar can unload the worker
  (send a `worker_unload` RPC) and restart it on next transcription.
- The worker's prewarm phase runs once at startup (Option P-1).

**Trade-off:** 450 MB RAM is significant. The plan should add a setting
"Keep offline engine running" (default on) vs "Start on demand" (default
off for low-RAM machines).

### 7.4 New IPC events (the plan introduces ~13)

The 2026-08-12 version's 18 edge cases implicitly introduce new IPC events
that must be added to all FOUR allowlists (see §9.4):

1. `pack_download_started` (push)
2. `pack_download_progress` (push, silent — no UI)
3. `pack_download_completed` (push)
4. `pack_download_failed` (push)
5. `pack_verified` (push)
6. `pack_missing` (push)
7. `pack_corrupt` (push)
8. `pack_ready` (push — worker started + prewarmed)
9. `worker_started` (push)
10. `worker_crashed` (push)
11. `worker_unloaded` (push)
12. `transcribe_offline` (request — slim core → worker)
13. `transcribe_offline_result` (push — worker → slim core)

Each must be added to:
- `_COMMAND_REGISTRY` (`voice_typer/server/ipc/registry.py:172`).
- `ALLOWED_COMMANDS` TS (`voice_typer/client/src/main/allowed-commands.ts:70`).
- `allowed_commands()` Rust (`src-tauri/src/commands/sidecar_cmds/allowlist.rs:139`).
- `ALLOWED_EVENT_TYPES` Rust (`src-tauri/src/sidecar/ws/event_protocol.rs:49`).
- `PythonRequest` / `PythonPushEvent` TS unions.
- `hooks/usePython.ts` `KNOWN_EVENT_TYPES` list.
- `event_bus.py` canonical catalogue docstring.

Plus the parity tests:
- `tests/test_ipc_command_registry_sync.py`.
- `tests/test_command_registry_parity.py`.
- `tests/test_relaunch_event_name_parity.py`.
- `tests/test_notification_event_name.py`.
- A **new** `tests/test_event_types_parity.py` (the fourth allowlist has no
  parity test today — this is a gap to fill).

---

## 8. Edge cases — re-verified against the codebase

The 2026-08-12 version listed 18 edge cases. The investigation found: 0
fully handled by existing code, 5 partially handled, 13 unhandled. This
section re-specifies each against the real codebase.

### 8.1 Partial download resume

- **Codebase state:** `service/model.py` has resume logic for HF model
  downloads (via `snapshot_download`'s `resume_download=True`). No resume
  for the pack (no pack downloader exists yet).
- **Plan:** the pack downloader reuses the resume pattern from
  `service/model.py:_download_whisper_family`. The partial file is saved as
  `pack-<version>.partial`; on next launch, it continues from the byte
  offset. The partial file is never trusted — only a fully downloaded,
  checksum-verified pack is ever used.
- **Test:** `tests/test_pack_download_resume.py` (new).

### 8.2 Pack arrives corrupted

- **Codebase state:** `verify_tauri_binary_or_skip()` exists for the Tauri
  host binary. No equivalent for the pack.
- **Plan:** `verify_pack_or_skip()` (new, modeled on
  `verify_tauri_binary_or_skip`). Mismatch → discard + re-download (up to 3
  attempts, with exponential backoff).
- **Test:** `tests/test_pack_corruption_recovery.py` (new).

### 8.3 Atomic swap (crash during replacement)

- **Codebase state:** `security/file_io.py:266` documents that `os.replace`
  raises `PermissionError` on Windows when the destination is open. No
  `MoveFileEx(MOVEFILE_DELAY_UNTIL_REBOOT)` anywhere.
- **Plan:** on Windows, the worker exe must be stopped BEFORE the swap. The
  swap is: download to `pack-<new-version>/` → verify → stop worker →
  `rename pack-<old> pack-<old>.trash` → `rename pack-<new> pack-<current>`
  → start worker → delete `pack-<old>.trash`. On POSIX, the rename-over is
  atomic and the worker can keep running (the old inode stays alive until
  the process exits).
- **Test:** `tests/test_pack_atomic_swap.py` (new, Windows + POSIX).

### 8.4 Consent gate (CONFLICT — corrected)

- **Codebase state:** `service/model.py:854-912` — `_require_huggingface_consent()`
  blocks all HF model downloads without consent (GDPR-driven, CR-11).
  `tests/test_service_download_consent.py` enforces it.
- **2026-08-12 plan said:** "no progress bar, no dialog, no toast" for the
  pack. This **violates** the consent pattern — the pack download phones
  home to GitHub Releases, revealing user IP to Microsoft.
- **Corrected plan:** the pack download is consent-gated, same as model
  downloads. The consent is requested once (on first launch or first
  offline-transcription attempt) via the existing consent UI. After
  consent, the pack downloads silently (no progress bar in the main UI, but
  a "Preparing…" line in the relevant areas).
- **AGENTS.md:** C-DATA-1 (rule on allowed network calls) must be
  updated by the USER to extend category (3) "model downloads" → "runtime
  asset downloads" or add category (4). Agents cannot edit AGENTS.md.

### 8.5 Metered connection

- **Codebase state:** no metered detection exists.
- **Plan:** Windows — `NLM` API via `ctypes`/`comtypes`. Linux/macOS — no
  reliable detection; the setting is manual ("Download offline engine
  later"). Default: auto-download on Windows, manual on Linux/macOS (until
  a reliable detection API is found).
- **Test:** `tests/test_pack_metered_detection.py` (new, Windows only).

### 8.6 Corporate networks / proxies

- **Codebase state:** the existing update check (NOT IMPLEMENTED — see §10)
  was supposed to handle proxies. The pack downloader should use the same
  `requests`/`httpx` stack with system proxy env vars (`HTTP_PROXY`,
  `HTTPS_PROXY`).
- **Plan:** the pack downloader inherits `assert_url_allowed()` from
  `tests/test_http_safety_ssrf.py` (SSRF protection) and respects system
  proxy env vars.
- **Test:** `tests/test_pack_proxy.py` (new).

### 8.7 GitHub rate limit

- **Codebase state:** no GitHub Releases download logic exists today.
- **Plan:** automatic retries with exponential backoff (1s, 2s, 4s, 8s, max
  3 attempts). On 403 (rate limit), respect the `X-RateLimit-Reset` header.
- **Test:** `tests/test_pack_github_rate_limit.py` (new).

### 8.8 Disk space check (before download)

- **Codebase state:** `asr_utils._check_disk_space_for_download()` exists
  for model downloads. Not reused for the pack.
- **Plan:** reuse `_check_disk_space_for_download()` with the pack size
  (180 MB compressed + 450 MB unpacked = 630 MB required). If insufficient,
  show one tray notification + defer.
- **Test:** `tests/test_pack_disk_space_check.py` (new).

### 8.9 Disk fills during download

- **Codebase state:** no handling for disk-full mid-download.
- **Plan:** the download stops gracefully on `OSError` (disk full), the
  partial file is deleted, one notification is shown, retried later.
- **Test:** `tests/test_pack_disk_full_during_download.py` (new).

### 8.10 Pack deleted by cleaner/AV

- **Codebase state:** `autostart_launcher.py` does a cheap existence check
  for the Tauri binary on launch. No equivalent for the pack.
- **Plan:** at every launch, the slim-core sidecar checks
  `pack-<version>/worker.exe` existence. Missing → silent re-download. A
  full checksum check runs in the background so startup is never slowed.
- **Test:** `tests/test_pack_missing_on_launch.py` (new).

### 8.11 App-data folder write blocked

- **Codebase state:** `src-tauri/src/platform/paths.rs` resolves the data
  dir per-platform. No fallback for write-blocked dirs.
- **Plan:** fall back to the user's roaming folder (Windows) / `~/.voice-typer`
  (POSIX). If that's blocked too, run in "core-only mode" with one clear
  message. Rare, but handled.
- **Test:** `tests/test_pack_fallback_dir.py` (new).

### 8.12 Update during download

- **Codebase state:** no update mechanism exists (see §10).
- **Plan:** partial downloads are saved per pack-version. If the needed
  version is still the same, the download continues. If the version
  changed, the old partial is discarded.
- **Test:** `tests/test_pack_version_change_during_download.py` (new).

### 8.13 Dual instance race

- **Codebase state:** `VoiceTyperSingleInstance` enforces a single app
  instance via mutex (Windows) / lockfile (POSIX — best-effort).
- **Plan:** the pack downloader takes its own lock file
  (`pack-<version>.lock`) to serialize downloads across instances.
- **Test:** `tests/test_pack_dual_instance.py` (new).

### 8.14 Race: transcribe-at-finish

- **Codebase state:** no pack-ready state exists.
- **Plan:** "ready" is a single definition: downloaded + verified + worker
  started + prewarmed. All features check that state. Clicking early queues
  the request and it auto-continues when ready.
- **Test:** `tests/test_pack_transcribe_at_finish.py` (new).

### 8.15 Transcribe 2s after first launch

- **Codebase state:** no handling.
- **Plan:** the "Preparing offline engine…" line appears, the request is
  queued, and it auto-continues when the pack is ready.
- **Test:** `tests/test_pack_early_transcribe.py` (new).

### 8.16 Checksum slows startup

- **Codebase state:** `verify_tauri_binary_or_skip()` runs on startup for
  the Tauri binary.
- **Plan:** the pack checksum check runs in the background after startup,
  never blocking the window from opening. A cheap existence check runs
  synchronously.
- **Test:** `tests/test_pack_checksum_background.py` (new).

### 8.17 Pack download competes with model download

- **Codebase state:** each `download_model` call spawns its own thread. No
  shared queue.
- **Plan:** a shared download queue (`download_queue.py`, new). The pack is
  always lowest-priority and pauses while a user-initiated download runs.
  Both are resumable.
- **Test:** `tests/test_pack_download_queue.py` (new).

### 8.18 SmartScreen / MOTW / Gatekeeper

- **Codebase state:** `scripts/windows/sign-authenticode.ps1` signs the
  installer. `scripts/macos/install.sh:135-136` removes the quarantine
  xattr.
- **Plan:**
  - **Windows:** the worker exe is signed with the same Authenticode
    certificate (added to the signing foreach in
    `tauri-windows-build.yml:620-624`). The MOTW is removed after
    verification.
  - **macOS:** the worker is signed with Developer ID + notarized via
    `notarytool` + stapled (added to `tauri-macos-build.yml:661-667`).
    Gatekeeper handles the quarantine.
  - **Linux:** unsigned by design (per `tauri-linux-build.yml:393-397`).
- **Test:** `tests/test_pack_signing.py` (new, Windows + macOS).

---

## 9. Already exists — reuse, don't rebuild (verified)

The 2026-08-12 version listed 12 existing pieces. The investigation verified
them and corrected the claims:

| Existing piece | Where | What we reuse it for | Verified? |
|---|---|---|---|
| Download-progress events + resume | `useModelDownload.ts`, `event_bus.py`, `download_progress` push | Pattern (not code) for the pack downloader. `useModelDownload.ts` does NOT support silent mode — needs a separate `usePackDownload` hook. | ✅ pattern, ❌ direct reuse |
| Model download service | `service/model.py` | Pattern for the pack downloader. Only `DownloadOutcome`, `push_progress`, `notify`, `poll_download_progress` are reusable; transport, manifest format, polling strategy, and consent must be new. | ✅ pattern, ❌ direct reuse |
| Integrity manifest | `tauri-binaries.json`, `autostart_launcher.py`, `update_tauri_manifests.py` | Pattern for `pack-manifest.json` (new file). Do NOT extend `tauri-binaries.json` — different scope. | ✅ pattern, ❌ direct reuse |
| Websocket IPC bridge | `--ws` flag, `websockets` package, IPC registry | Pattern for the worker's WS server. The slim-core sidecar becomes a WS CLIENT of the worker — a new second hop. | ✅ pattern, ❌ direct reuse |
| Prewarm tooling | `voice_typer/server/prewarm/` | Logic moves INTO the worker as a startup phase. The separate binary + OS schedulers are DELETED. | ✅ logic, ❌ binary |
| Single-instance mutex | `VoiceTyperSingleInstance` | Pattern for the worker's own lock file. | ✅ pattern |
| Auto-update mechanism | `docs/auto-update-feature.md` | **NOT IMPLEMENTED (design only).** Must be built from scratch. See §10. | ❌ does not exist |
| Code-signing pipeline | `tauri-windows-build.yml` (4 steps) | Extend to sign the worker exe (5th binary). | ✅ exists, needs extension |
| Cloud engines | `cloud_engines.py`, `llm_polish.py` | Core works without the pack (verified: zero torch/ctranslate2/onnxruntime imports). | ✅ verified |
| Mic test service | `service/microphone_test.py` | Degraded mode when pack is absent (RMS only, no VAD). | ✅ verified |
| NSIS installer config | `tauri.windows-x86_64.conf.json` | "Include offline engine pack" checkbox requires a CUSTOM .nsi template — Tauri v2's `bundle.windows.nsis` config has no checkbox option. | ⚠️ needs custom .nsi |

### 9.1 The "three allowlists in lockstep" claim is understated

There are actually **four allowlists** + several auxiliary sync points:

1. `_COMMAND_REGISTRY` — `voice_typer/server/ipc/registry.py:172` (65 entries).
2. `ALLOWED_COMMANDS` (TS) — `voice_typer/client/src/main/allowed-commands.ts:70`.
3. `allowed_commands()` (Rust) — `src-tauri/src/commands/sidecar_cmds/allowlist.rs:139`.
4. **`ALLOWED_EVENT_TYPES`** (Rust) — `src-tauri/src/sidecar/ws/event_protocol.rs:49`
   (40 entries, **no parity test** — adding a Python event without adding it
   here silently drops the frame).

Plus:
- `PythonRequest` / `PythonPushEvent` TS discriminated unions.
- `hooks/usePython.ts` `KNOWN_EVENT_TYPES` list.
- `event_bus.py` canonical catalogue docstring.

And the parity tests:
- `tests/test_ipc_command_registry_sync.py`.
- `tests/test_command_registry_parity.py`.
- `tests/test_relaunch_event_name_parity.py`.
- `tests/test_notification_event_name.py`.
- **NEW:** `tests/test_event_types_parity.py` (for the fourth allowlist).

### 9.2 The 8 locale files

Verified at `voice_typer/client/src/renderer/src/i18n/translations/{ar,de,en,es,fr,hi,ru,zh}.json`
(all 1,918 lines). Parity enforced by `tests/test_i18n_keys_parity.py` +
`tests/test_i18n_completeness.py` + renderer `locale-key-parity.test.ts`.
Helper scripts: `scripts/add_i18n_keys.py`, `backfill_i18n_keys.py`,
`apply_translations.py`, `_i18n_common.py`,
`client/scripts/translate-i18n{,-all}.js`.

### 9.3 New user-visible strings (must be added to all 8 locales)

- `"Preparing offline engine…"` (transcription area)
- `"Download offline engine later"` (settings checkbox label)
- `"Pack missing"` (tray notification)
- `"Pack corrupt"` (tray notification)
- `"Disk space low"` (tray notification)
- `"Pack download complete"` (tray notification)
- `"Include offline engine pack"` (NSIS installer text — NOT covered by
  renderer i18n parity test; needs separate installer-i18n story +
  `scripts/check_branding.py` `BUILD_CONFIG_FILES` allowlist entry)
- `"Keep offline engine running"` (settings checkbox, per §7.3)

All strings must use `{appName}` placeholder (C-BRAND-1, `APP_NAME = "Voice
Typer"` at `branding.py:31`).

---

## 10. Auto-update mechanism (must be built — does not exist today)

The 2026-08-12 version claimed "it already probes the network for update
checks — same mechanism." This is **false**:

- `docs/auto-update-feature.md` is explicitly **"NOT IMPLEMENTED (design only)"**.
- No GitHub Releases publishing step exists in CI — only
  `actions/upload-artifact`.
- No "network is back" retry trigger exists.

### 10.1 What must be built

- **GitHub Releases publishing:** add a `softprops/action-gh-release` step
  to the release workflow. Upload the slim-core installer + the pack
  onefile + `pack-manifest.json` as release assets.
- **Pack-version check:** on launch, the slim core fetches the latest
  `pack-manifest.json` from the GitHub Releases URL (with consent — §8.4).
  If the version is newer than the local pack, download in the background.
- **Network-is-back trigger:** use Tauri's `window.addEventListener('online')`
  in the renderer (or `tauri-plugin-network` on the Rust side) to retry
  deferred downloads.
- **Proxy support:** respect `HTTP_PROXY`/`HTTPS_PROXY` env vars.
- **SSRF protection:** inherit `assert_url_allowed()` from
  `tests/test_http_safety_ssrf.py`.
- **Max-bytes limit:** inherit `_secure_read_text(max_bytes=)` from
  `tests/test_secure_file_io_max_bytes.py`.

### 10.2 C-DATA-1 constraint

C-DATA-1 (rule on allowed network calls) currently allows 3 categories:
(1) update checks, (2) cloud transcription, (3) model downloads. The pack
download from GitHub Releases is NOT covered. The USER must extend category
(3) → "runtime asset downloads" or add category (4). Agents cannot edit
AGENTS.md.

---

## 11. CI/CD (the 2026-08-12 version's biggest gap)

The user explicitly noted: "some of them don't handle the ci action, cicd
part." This section specifies the CI changes.

### 11.1 The constraint rule references (corrected)

The 2026-08-12 version cited "C-CI-8/NU-106." This is imprecise:

- **C-CI-8** is a AGENTS.md rule that mandates `--module-parameter=torch-disable-jit=no`
  to protect the Nuitka bundle while torch is shipped.
- **NU-106** is NOT a standalone AGENTS.md rule — it's an inline
  evidence tag in `tauri-windows-build.yml:422-448` and
  `build_sidecar_windows.sh:155-166`, cited in C-CI-8's rationale.

Only C-CI-8 is the rule. The user retires C-CI-8 after Phase 1c
verification (agents cannot edit AGENTS.md).

### 11.2 `tests/tauri/test_config_script_drift.py` HARD-FAILS

`TestNuitkaSidecarBuildsDoNotExcludeTorchDistributed` (lines 437-512)
HARD-ENFORCES the `--module-parameter=torch-disable-jit=no` flag and
FORBIDS 5 `--nofollow-import-to=torch.*` flags. Retiring the flag REQUIRES
deleting/updating this test class. The plan must list this test as a
mandatory change in Phase 1c.

### 11.3 "Grep frozen bundle for torch" — implementation

No existing CI step does this. Nuitka onefile is a single ~100 MB PE binary.
The grep requires `strings` (Linux/macOS) or a Python script that extracts
the payload. Concrete implementation:

```bash
# scripts/build/check_bundle_torch_free.sh (new)
strings voice-typer-sidecar-$TARGET_TRIPLE.exe | grep -i "torch\." && exit 1
strings voice-typer-sidecar-$TARGET_TRIPLE.exe | grep -i "silero_vad.jit" && exit 1
echo "Bundle is torch-free."
```

Add as a CI step in `tauri-windows-build.yml` (and macOS/Linux equivalents)
after the sidecar build.

### 11.4 Size gate — does not exist today

`tauri-windows-build.yml:509-510` only `Write-Host`s the size. Add a real
gate:

```yaml
- name: Assert sidecar size ≤ 185 MB
  run: |
    $size = (Get-Item voice-typer-sidecar-x86_64-pc-windows-msvc.exe).Length / 1MB
    Write-Host "Sidecar size: $size MB"
    if ($size -gt 185) {
      Write-Error "Sidecar exceeds 185 MB limit (got $size MB)"
      exit 1
    }
```

Also add a pack size gate (≤ 200 MB compressed) and a slim-core size gate
(≤ 45 MB).

### 11.5 Worker exe signing

C-CI-11 enumerates exactly 4 code-signing steps (sidecar+prewarm+native
listener; NSIS; MSI; standalone exe). The worker exe is a **5th binary**.
The user must update C-CI-11 to include it. In CI:

- **Windows:** add the worker to the foreach array at
  `tauri-windows-build.yml:620-624`.
- **macOS:** add to `tauri-macos-build.yml:661-667` (currently hardcoded
  list — needs a new entry).
- **Linux:** unsigned by design (`tauri-linux-build.yml:393-397`).

### 11.6 Cross-platform CI

The 2026-08-12 version was Windows-only. The plan must address:

- **macOS:** `tauri-macos-build.yml` — notarization + stapling for the
  worker. New `entitlements.plist` entry if the worker needs mic access.
- **Linux:** `tauri-linux-build.yml` — `.deb`/`.rpm`/AppImage packaging for
  the slim core. The pack is a separate download (not in the `.deb`).

### 11.7 Ratchet baselines

After the migration, regenerate all 4 ratchet baselines:

- `coverage-baseline.json` (65.23% today — removing torch tests may drop
  coverage; run `scripts/coverage_ratchet_check.py --regenerate --force`).
- `mypy-baseline.json` (696 errors — torch-specific ignores go stale).
- `pyrefly-baseline.json` (~276 entries — 14+ for parakeet/qwen/prewarm go
  stale).
- `ruff-baseline.json` (torch-specific noqa comments go stale).

**Note:** ratchets refuse to auto-regenerate on improvement — the
`--regenerate --force` flag is required.

### 11.8 `.husky/pre-push` no longer runs pytest

The pre-push hook was leaned down: pytest (even the fast subset) was
removed — the full suite is greened at the end of every task
(AGENTS.md working convention), so push-time verification keeps only
the cached client typecheck + the mypy ratchet. The torch-import
concern below is now moot for pre-push; it applies to local
`pytest` runs and CI instead.

### 11.9 New artifact naming (C-CI-13)

C-CI-13 forbids renaming existing artifacts but allows adding new ones.
New artifact names:

- `voice-typer-slim-core-<version>-<triple>.exe` (Windows slim core).
- `voice-typer-runtime-pack-<pack-version>-<triple>.zip` (pack, platform-agnostic zip).
- `pack-manifest.json` (manifest, uploaded as a release asset).

Update `tauri-build.yml` download steps in lockstep.

---

## 12. Execution order (revised)

> **Status (2026-08-15):** Phases 1a–1d, 2a–2b implemented. Updated:
> (a) Phase 1d Qwen → ONNX is DONE — torch engine removed entirely
> (``qwen_onnx_model.py``); (b) the Rust host's worker *spawn* is
> IMPLEMENTED (`spawn_worker_release` / `spawn_worker_dev_mode` in
> `src-tauri/src/sidecar/spawn/worker.rs`, `parse_worker_started`
> handshake, 6 unit tests — committed 2026-08-15); the remaining gap is
> main.rs WIRING: `WorkerState` is not yet `app.manage()`d and nothing
> calls `initialize_worker` (the pack-verified spawn trigger), and the
> slim-core → worker ``transcribe_offline`` forwarding hop is still a
> stub ack — the runtime split is NOT yet end-to-end; (c) Phase 2c/2d:
> Phase 2d launch-time existence checks IMPLEMENTED 2026-08-15
> (``startup_tasks.check_offline_pack_on_launch`` wired into
> ``StartupSequence`` — cheap existence check, background checksum,
> ``offline_pack_missing`` event, consent-gated silent re-download;
> ``get_status`` now carries ``offline_pack`` state; degradation markers
> in ``transcribe_offline`` + mic-test auto-transcribe). Phase 2c CI
> gates are PRE-WIRED (worker build + signing + torch-free + size gates +
> artifact uploads — all conditional on ``build_worker_*.sh``). The
> slim-core BUILD (sidecar without ML libs) + custom .nsi pack checkbox
> + full-offline artifact remain BLOCKED: the slim-core server still
> imports the ML stack in 10 files (vad.py, transcription.py,
> parakeet_engine.py, qwen_onnx_model.py, …), so slimming the sidecar
> before the runtime handoff is verified would break the running app —
> complete the main.rs worker-spawn trigger + transcribe_offline
> forwarding first (see "Remaining work" below). C-CI-11 (5th binary)
> + C-CI-13 (worker binary name) updated 2026-08-15 by the user's
> direction; (d) the C-DATA-1 rule-text extension — DONE
> 2026-08-15: the USER added category (4) "offline-pack download from
> GitHub Releases" to C-DATA-1 in AGENTS.md, so the pack download is
> explicitly permitted whether or not it is consent-gated (see
> `docs/auto-update-feature.md`).

1. **Phase 1a — Silero VAD → ONNX.** See `PLAN_ONNX_INTEGRATION.md` §2.
   - Rewrite `vad.py` (ORT backend + hidden state threading).
   - Add `silero_vad.onnx` + packaging (`MANIFEST.in`, `voice-typer.spec`,
     `build_sidecar_*.sh`).
   - Rewrite `tests/test_vad.py`, delete `tests/test_vad_dtype_optimization.py`.
   - Update `docs/adr/0005-silero-vad.md`.
   - **Gate:** VAD tests pass with ORT; `bench/bench_vad.py` latency ≤ torch baseline.
2. **Phase 1b — Parakeet → ONNX.** See `PLAN_ONNX_INTEGRATION.md` §3.
   - Rewrite `parakeet_engine.py` (use `onnx-asr` Option B-1).
   - Add `voice_typer/stubs/onnx_asr.pyi`.
   - Repopulate `model_hashes.json` via `scripts/populate_model_hashes.py`.
   - Update `MODEL_REGISTRY["parakeet"]` (repo_id, download_size_mb,
     network_behavior → consent-gated, fixes G4-H-04).
   - Add `ALLOW_PATTERNS_PARAKEET_ONNX` to `security/model_integrity.py`.
   - Add 5 new `tests/test_parakeet_onnx_*.py`.
   - **Gate:** Parakeet parity test passes (edit distance ≤ threshold).
3. **Phase 1c — torch sweep (except Qwen).** See `PLAN_ONNX_INTEGRATION.md` §8.3.
   - Sweep the 30+ torch import sites (§3.3).
   - Update `scripts/diagnostics.py:175-199`.
   - Update `pyproject.toml` (drop `torch>=2.0,<3.0`; keep `transformers`
     for Qwen).
   - Regenerate `requirements-lock.txt`.
   - Delete/update `tests/tauri/test_config_script_drift.py:437-512`.
   - Regenerate all 4 ratchet baselines.
   - Update doc-accuracy tests + tech-debt TODO freshness test.
   - Add `scripts/build/check_bundle_torch_free.sh` + CI step.
   - Add size gate to `tauri-windows-build.yml` (≤ 185 MB).
   - **Gate:** `grep -ri "import torch" voice_typer/` → zero hits except
     `qwen_engine.py`. Full workflow run, confirmed with user (C-CI-2).
   - **USER action:** retire C-CI-8 in AGENTS.md.
4. **Phase 2a — worker exe skeleton.** See §4.4, §7.
   - New `voice_typer/worker/__main__.py` entry point.
   - New `scripts/build/build_worker_*.sh` (3 platforms).
   - New `src-tauri/src/platform/worker_path.rs`.
   - Add worker to all 5 platform tauri conf files (`externalBin`).
   - Pack manifest (`pack-manifest.json`) + `verify_pack_or_skip()`.
5. **Phase 2b — silent background downloader.** See §8, §10.
   - New `voice_typer/server/service/pack.py` (pack downloader, modeled on
     `service/model.py` but separate).
   - New `usePackDownload.ts` hook (silent mode).
   - New IPC events (13 — see §7.4) in all 4 allowlists + parity tests.
   - New `tests/test_event_types_parity.py` (4th allowlist).
   - All 18 edge-case tests (§8).
   - Auto-update mechanism (§10): GitHub Releases publishing, pack-version
     check, network-is-back trigger.
   - All 8 locale files updated with new strings (§9.3).
   - **USER action (DONE 2026-08-15):** C-DATA-1 in AGENTS.md extended
     with category (4) — offline-pack download from GitHub Releases.
6. **Phase 2c — slim core build.** See §4, §11.
   - Sidecar build without ML libraries.
   - Custom .nsi template for "Include offline engine pack" checkbox.
   - Full-offline artifact (slim core + pack bundled).
   - Worker exe signing added to CI (Windows + macOS).
   - New artifact names (C-CI-13).
   - **USER action:** update C-CI-11 in AGENTS.md (5th binary).
7. **Phase 2d — launch-time existence check + degradation matrix.** See §8.10.
   - Cheap existence check on launch.
   - Background checksum check.
   - Degradation matrix live in mic test + cloud paths.
8. **Phase 1d — Qwen → ONNX (deferred).** See `PLAN_ONNX_INTEGRATION.md` §4.
   - Decide Option C-1/C-2/C-3.
   - If C-3 (defer indefinitely), document the decision and accept that
     torch stays for Qwen.
   - If C-1/C-2, execute the migration + final torch sweep on
     `qwen_engine.py` + drop `transformers` dep.

---

## 13. Out of scope (not part of this plan)

- Converting whisper to ONNX (decided: no — ctranslate2 already torch-free).
- Removing av / pyrnnoise / PIL (open decision 3 in the 2026-08-12 version —
  optional later pass).
- **GPU runtime pack** (open follow-up: CPU pack is the default; a GPU pack
  variant is a separate project. Note: `onnxruntime-gpu` does NOT bundle
  NVIDIA DLLs — see `PLAN_ONNX_INTEGRATION.md` §6.1. A GPU pack must bundle
  `nvidia-*-cu12` wheels or expect system CUDA.).
- Cloud-only / streaming transcription alternatives.
- Removing `sys.path` hacks used by `nvidia_dll_paths.py` (the `nvidia/*`
  paths survive torch removal — see `PLAN_ONNX_INTEGRATION.md` §6.2).

---

## 14. Open decisions (revised)

1. **Qwen migration option.** Pick C-1, C-2, or C-3. Recommendation: C-3
   (defer) until the `qwen_asr` maintainer confirms ONNX support. See
   `PLAN_ONNX_INTEGRATION.md` §4.3.
2. **Prewarm architecture.** Pick P-1, P-2, or P-3. Recommendation: P-1
   (worker startup phase). See §6.2.
3. **Worker lifecycle.** Long-lived vs. on-demand. Recommendation: long-lived
   with a "Keep offline engine running" setting. See §7.3.
4. **Metered-connection default.** Auto-download on Windows (NLM detection),
   manual on Linux/macOS. See §8.5.
5. **Full-offline installer as permanent second artifact.** Yes — always
   publish both. The slim core is the default; the full-offline installer
   exists for offline-install scenarios.
6. **The optional parallel cut list** (av / pyrnnoise / PIL). Same as the
   2026-08-12 version — optional later pass, each cut takes a feature.
7. **macOS GPU for Parakeet.** Test `CoreMLExecutionProvider` speedup. If
   <20% faster than CPU, ship CPU-only. See `PLAN_ONNX_INTEGRATION.md` §6.6.
8. **Pack source.** GitHub Releases vs. a CDN. Recommendation: GitHub
   Releases (no new infrastructure, free for open source).

---

## 15. Summary of file changes (this plan, excluding Phase 1)

| Action | File | Phase |
|---|---|---|
| **NEW** | `voice_typer/worker/__main__.py` | 2a |
| **NEW** | `voice_typer/server/service/pack.py` | 2b |
| **NEW** | `voice_typer/client/src/renderer/src/hooks/usePackDownload.ts` | 2b |
| **NEW** | `src-tauri/src/platform/worker_path.rs` | 2a |
| **NEW** | `scripts/build/build_worker_windows.sh` | 2a |
| **NEW** | `scripts/build/build_worker_linux.sh` | 2a |
| **NEW** | `scripts/build/build_worker_macos.sh` | 2a |
| **NEW** | `scripts/build/voice-typer-worker.spec` (PyInstaller fallback) | 2a |
| **NEW** | `scripts/build/check_bundle_torch_free.sh` | 1c |
| **NEW** | `tests/test_event_types_parity.py` (4th allowlist) | 2b |
| **NEW** | 18 edge-case tests (`tests/test_pack_*.py`) | 2b |
| **MODIFY** | `src-tauri/tauri.{windows-x86_64,windows-aarch64,linux-x86_64,linux-aarch64,macos}.conf.json` (add worker `externalBin`) | 2a |
| **MODIFY** | `src-tauri/tauri.conf.json` (`plugins.shell.scope`) | 2a |
| **MODIFY** | `src-tauri/src/sidecar/spawn.rs` (generalize `SidecarState` for 2 children) | 2a |
| **MODIFY** | `src-tauri/src/sidecar/ws/event_protocol.rs:49` (`ALLOWED_EVENT_TYPES`) | 2b |
| **MODIFY** | `voice_typer/server/ipc/registry.py:172` (`_COMMAND_REGISTRY`) | 2b |
| **MODIFY** | `voice_typer/client/src/main/allowed-commands.ts:70` | 2b |
| **MODIFY** | `src-tauri/src/commands/sidecar_cmds/allowlist.rs:139` | 2b |
| **MODIFY** | All 8 locale files (`i18n/translations/*.json`) | 2b |
| **MODIFY** | `.github/workflows/tauri-windows-build.yml` (worker signing, size gate, torch-free check) | 1c/2c |
| **MODIFY** | `.github/workflows/tauri-macos-build.yml` (worker notarization) | 2c |
| **MODIFY** | `.github/workflows/tauri-linux-build.yml` (worker packaging) | 2c |
| **MODIFY** | `voice_typer/client/src/renderer/src/types/ipc/{requests,push_events}.ts` | 2b |
| **MODIFY** | `voice_typer/client/src/renderer/src/hooks/usePython.ts` (`KNOWN_EVENT_TYPES`) | 2b |
| **MODIFY** | `voice_typer/server/event_bus.py` (catalogue docstring) | 2b |
| **DELETE** | `voice_typer/server/prewarm/` (binary + schedulers, if P-1 chosen) | 2a |
| **DELETE** | `src-tauri/src/sidecar/spawn/prewarm.rs` | 2a |
| **DELETE** | `tests/tauri/mig15/test_prewarm_logontrigger_windows.py` | 2a |
| **DELETE** | `tests/tauri/mig16/test_prewarm_launchagent_macos.py` | 2a |
| **DELETE** | `tests/tauri/mig17/test_prewarm_systemd_linux.py` | 2a |
| **DELETE** | `tests/test_prewarm_spawn_resolver.py` | 2a |
| **DELETE** | `tests/test_uninstall_prewarm_cleanup.py` | 2a |
| **USER-ONLY** | `AGENTS.md` (retire C-CI-8; update C-CI-11, C-DATA-1) | 1c/2c |
