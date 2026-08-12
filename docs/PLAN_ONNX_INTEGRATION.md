# ONNX Runtime Migration — Technical Reference

> **Status:** Rewritten 2026-08-13 after a 15-agent deep investigation of the
> voice-typer codebase. Supersedes the earlier "OnnxParakeet Integration Plan"
> which proposed adding a parallel `onnx-parakeet` backend — that architecture
> is **obsolete**. The user-approved decision (2026-08-12, see
> `plan-runtime-pack-split.md`) is to **convert the three existing engines in
> place** (VAD, Parakeet, Qwen) and remove torch project-wide.
>
> This document is the **detailed technical reference** for the ONNX migration
> itself. The companion document `plan-runtime-pack-split.md` covers the
> installer split, runtime-pack worker, CI/CD, cross-platform, and edge cases.
> Read both together.

---

## SCOPE — "remove torch" means the project, NOT your device (READ FIRST)

> **CRITICAL – user-set boundary (2026-08-13):** Whenever this plan (or its
> companion `plan-runtime-pack-split.md`) says "remove torch", "torch removal",
> or "torch removed from X", it means **removing torch as a PROJECT
> dependency and source-code import** — i.e. `pyproject.toml`,
> `requirements-lock.txt`, `voice_typer/**/*.py` imports, build/Nuitka
> invocation flags, and the frozen sidecar/worker binaries. It does **NOT**
> mean uninstalling torch from the user's device.
>
> **The implementing agent MUST NOT, at any time, under any phase:**
> - run `pip uninstall torch` / `pip uninstall torchvision` /
>   `pip uninstall nvidia-*` or any equivalent (including `uv pip uninstall`),
> - delete, prune, or modify anything inside the user's **`.venv`** (or any
>   virtualenv / conda env / user / system site-packages),
> - delete or modify any installed torch (CPU or GPU) package, wheel, cache
>   (`~/.cache/torch`, `~/.cache/huggingface`), or DLL from the device,
> - touch or "clean up" torch-related files outside the project repo tree.
>
> The migration edits **only** files inside this repository (source, tests,
> docs, build scripts, workflow files). Any change to the project that makes
> torch *no longer required* is a project change — it does not entitle the
> agent to uninstall anything from the user's environment.
>
> **Whether the user later uninstalls torch from their device is the USER's
> decision.** This plan treats that as explicitly **OUT OF SCOPE**. The agent
> finishes the project migration, leaves the user's installed torch (CPU +
> GPU) fully intact, and the existing torch code paths already work today — so
> if anything about the migration regresses, the user's pre-existing torch
> environment is untouched and self-contained.
>
> If an agent believes an environment change (e.g. uninstalling torch to
> reclaim disk, or `pip`-failing because torch is still present) is necessary,
> that is a **skip**: record it in `worklog.md` per the CONSTRAINTS.md
> audit-trail format and leave the environment alone.

---

## 0. What changed since the previous version of this plan

The previous version of this plan was written against an older snapshot of the
codebase and proposed adding a **new** `onnx-parakeet` backend alongside the
existing `parakeet` backend. After investigation, that approach is rejected for
these reasons:

1. **The user approved the in-place conversion on 2026-08-12.** Keeping two
   Parakeet backends (`parakeet` + `onnx-parakeet`) creates a UX wart (two
   entries on the Models page), a migration dead-end (users on `"parakeet"`
   would be stranded on the torch backend forever), and a maintenance burden
   (two code paths for the same model family).
2. **The `AsrBackend` Protocol** (`voice_typer/server/asr/registry.py:42-94`)
   is `@runtime_checkable` and duck-typed. The registry maps
   `"parakeet"` → `("voice_typer.server.parakeet_engine", "ParakeetEngine")`
   purely by name + class (`asr_registry.py:63-67`). Swapping the
   implementation in place is structurally trivial and requires **zero**
   registry/config/validator changes — only the engine module itself changes.
3. **Every line number in the old plan is stale.** The codebase has been
   refactored: `config.py` is now a package (`config/__init__.py:799`), and
   `config_validators.py` is now `config_validators/__init__.py:307`. The old
   plan cited `config.py:585` and `config_validators.py:1237` — both wrong.
4. **Several refactor proposals were already done.** `asr_utils.split_audio()`
   already exists at `voice_typer/server/asr_utils.py:390`. Both
   `ParakeetEngine._split_audio` (`parakeet_engine.py:845`) and
   `QwenEngine._split_audio` (`qwen_engine.py:860`) already delegate to it.
   The old plan's "Part 1a — Extract `split_audio()`" is unnecessary.

This rewrite corrects all of the above and adds the technical depth that the
old plan and the companion plan both lack: hidden-state hoisting for Silero
VAD, TDT decoding options for Parakeet, the **Qwen misidentification** (it is
an ASR engine, not an LLM — `onnxruntime-genai` is the wrong tool), and the
GPU/DLL handling reality.

---

## 1. Architecture decision: in-place conversion

### What "in-place conversion" means

The three existing backends keep their names: `"whisper"`, `"qwen"`,
`"parakeet"`. The `"whisper"` backend already uses ctranslate2 (no torch) and
is untouched. The `"parakeet"` and `"qwen"` backends keep their names but
their engine modules swap from `transformers + torch` to `onnxruntime` (or a
library built on it). The `"parakeet"` backend's registered class stays
`ParakeetEngine`; only its internals change.

### What this avoids

- No new entries in `_BACKEND_SPECS` (`asr_registry.py:63`).
- No new `ModelMetadata` row in `MODEL_REGISTRY` (`model_registry.py:13`).
- No new value in the `asr_backend` Literal (`config/__init__.py:799`).
- No new enum value in `config_validators/__init__.py:307`.
- No new branch in `model_manager._ensure_engine()` or `set_active_backend()`.
- No migration code for user configs that reference `"parakeet"`.
- No new entry in `types/config.ts` (renderer TS mirror).
- No new allow-pattern constant in `security/model_integrity.py:553-587`.

### What this still requires

The engine modules themselves change substantially (see Parts A, B, C below).
The `MODEL_REGISTRY` entry for `"parakeet"` needs its `repo_id`, `download_size_mb`,
and `description` updated to reflect the ONNX model source. The
`model_hashes.json` entry for `"parakeet"` needs to be repopulated for the new
file set (via `scripts/populate_model_hashes.py`). The stub
`voice_typer/stubs/qwen_asr.pyi` stays; a new `voice_typer/stubs/onnx_asr.pyi`
is added if Parakeet uses `onnx-asr` (see Part B).

---

## 2. Part A — Silero VAD → ONNX

### 2.1 Current state (verified)

- **File:** `voice_typer/server/vad.py` (~500 LOC).
- **Model path:** `voice_typer/server/silero_vad.jit` (2,272,526 bytes ≈ 2.17 MB,
  Silero VAD v4).
- **Loading:** `vad.py:182` — `_model = torch.jit.load(str(_VAD_MODEL_PATH))`.
- **Hidden state:** The JIT module manages the LSTM hidden state **internally**
  via `_model.reset_states()` (`vad.py:463-481`) and the stateful
  `_model(input, sr)` call. Callers do not see or touch the state.
- **Public API:** `is_available()`, `is_speech(audio, sr)`, `compute_vad_prob(audio, sr)`,
  `reset_states()`, `preload()`, `unload()`.
- **torch imports:** `vad.py:100`, `:145`, `:289`, `:425` (uses `torch.from_numpy`,
  `torch.zeros`, `torch.cat`, `torch.no_grad`).
- **Availability probe:** `vad.py:97-104` — `is_available()` does `import torch`
  and returns `True/False`. Called by `vad_processor.py:256-261` during
  `VadProcessor.__init__`.
- **CPU-only intent:** `vad.py:174-181` explicitly documents that VAD runs on
  CPU. There is no GPU code path for VAD today.

### 2.2 The critical gotcha the previous plan missed

`onnxruntime.InferenceSession` is **stateless**. The Silero VAD v4 ONNX export
takes `(input, state, sr)` as inputs and returns `(output, stateN)` — the
caller must hold the LSTM hidden-state buffer (shape `(2, 1, 128)` float32)
and thread it through every `compute_vad_prob` call, re-zeroing it on
`reset_states()`, `unload()`, and first load. This is the classic Silero ONNX
migration trap. If the state is not threaded correctly, VAD probabilities
become garbage after the first 512-sample window.

### 2.3 Migration plan

#### 2.3.1 Add the ONNX model file

- Download `silero_vad.onnx` (v4) from the official Silero VAD releases
  (Hugging Face `snakers/silero-vad` or the GitHub release).
- Place it at `voice_typer/server/silero_vad.onnx` (next to the current
  `silero_vad.jit`).
- Update packaging:
  - `MANIFEST.in:25-31` — add `include voice_typer/server/silero_vad.onnx`.
  - `scripts/build/voice-typer.spec:112-113,274` (PyInstaller fallback) —
    add `silero_vad.onnx` to datas.
  - The three `scripts/build/build_sidecar_*.sh` scripts — Nuitka
    `--include-data-files=voice_typer/server/silero_vad.onnx=voice_typer/server/silero_vad.onnx`.
- **Do NOT delete `silero_vad.jit` yet** — it stays until Phase 1c verification
  is complete (see §2.5).

#### 2.3.2 Rewrite `vad.py` model loading

Replace the `torch.jit.load` path with an `onnxruntime.InferenceSession`:

```python
# voice_typer/server/vad.py (new)

import numpy as np
import onnxruntime as ort

_VAD_MODEL_PATH = Path(__file__).resolve().parent / "silero_vad.onnx"
_VAD_STATE_SHAPE = (2, 1, 128)  # Silero v4 LSTM hidden state
_VAD_SAMPLE_RATE = 16000

class _SileroVadOnnx:
    def __init__(self) -> None:
        self._session: ort.InferenceSession | None = None
        self._state = np.zeros(_VAD_STATE_SHAPE, dtype=np.float32)
        self._input_name: str | None = None
        self._state_name: str | None = None
        self._sr_name: str | None = None
        self._output_name: str | None = None
        self._state_out_name: str | None = None

    def load(self) -> None:
        # CPU-only — see §2.3.3 for why providers is pinned
        self._session = ort.InferenceSession(
            str(_VAD_MODEL_PATH),
            providers=["CPUExecutionProvider"],
        )
        # Discover I/O names (Silero v4 ONNX uses non-default names)
        inputs = {i.name: i for i in self._session.get_inputs()}
        outputs = {o.name: o for o in self._session.get_outputs()}
        self._input_name = "input" if "input" in inputs else next(iter(inputs))
        self._state_name = "state" if "state" in inputs else next(iter(inputs))
        self._sr_name = "sr" if "sr" in inputs else None
        self._output_name = "output" if "output" in outputs else next(iter(outputs))
        self._state_out_name = "stateN" if "stateN" in outputs else next(iter(outputs))
        self.reset_states()

    def reset_states(self) -> None:
        self._state = np.zeros(_VAD_STATE_SHAPE, dtype=np.float32)

    def forward(self, audio_chunk: np.ndarray, sr: int = _VAD_SAMPLE_RATE) -> float:
        # audio_chunk must be shape (1, N) float32
        ort_inputs = {
            self._input_name: audio_chunk,
            self._state_name: self._state,
        }
        if self._sr_name:
            ort_inputs[self._sr_name] = np.array(sr, dtype=np.int64)
        out = self._session.run(None, ort_inputs)
        prob = float(out[0][0][0])
        self._state = out[1]  # thread the new state forward
        return prob
```

#### 2.3.3 CPUExecutionProvider — pinned, not default

ORT's default provider list is `["CUDAExecutionProvider", "CPUExecutionProvider"]`
when `onnxruntime-gpu` is installed. VAD is CPU-only by design (`vad.py:174-181`).
If a user has `onnxruntime-gpu` installed, ORT will route VAD to GPU — which
adds GPU→CPU upload latency per 512-sample window and breaks the existing
latency budget. The session MUST be created with
`providers=["CPUExecutionProvider"]` only.

#### 2.3.4 `is_available()` semantics

Change `vad.py:97-104` from `import torch` to:

```python
def is_available() -> bool:
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return _VAD_MODEL_PATH.exists()
```

#### 2.3.5 `unload()` and `reset_states()`

`unload()` must call `self._session = None` and `self.reset_states()`. The
existing `release_gpu_memory()` call in `asr_utils.py` becomes a no-op for ORT
(see §5.2).

### 2.4 Test rewrites (mandatory)

The old plan said "extend tests to run the ONNX backend with a fake" but did
not enumerate the rewrites. These tests are mandatory:

| Test file | Current state | Required change |
|---|---|---|
| `tests/test_vad.py` | Mocks `torch.from_numpy`, `torch.zeros`, `torch.cat`, `torch.no_grad` | Rewrite mocks to use a fake `ort.InferenceSession` that returns fixed `(prob, state)` tuples. Verify state threading. |
| `tests/test_vad_dtype_optimization.py` | Tests the `data_ptr()` no-clone invariant (torch-specific) | **Delete.** The invariant is unsatisfiable through ORT's allocator — ORT copies the input buffer. |
| `tests/test_electron_ipc_and_build.py:498` | Source-greps `assert "torch.jit.load" in src` | Change to `assert "InferenceSession" in src` (or remove the assertion if it serves no purpose post-migration). |
| `bench/bench_vad.py` | Uses real torch + real `silero_vad.jit` | Rewrite to use ORT + `silero_vad.onnx`. Keep the `--include-silero` flag for parity. |
| `tests/conftest.py` (~190 lines of `mock_torch` plumbing) | `_FakeOutOfMemoryError`, `_FakeTensor`, `_build_mock_torch()` session fixture, `real_torch` marker | Strip VAD-specific torch mocks. Keep the fixture for Parakeet/Qwen until Phase 1c. |
| `tests/regressions/gpu_memory_release_test.py` | Tests `torch.cuda.empty_cache()` (58 hits) | Either delete (purpose disappears) or repurpose to test `unload()` drops the ORT session. |

### 2.5 Phase gate

The `silero_vad.jit` file, the `MANIFEST.in` entry, and the
`--module-parameter=torch-disable-jit=no` Nuitka flag are retired **only** at
Phase 1c (total torch removal), NOT at Phase 1a. This is because Parakeet and
Qwen still use torch between Phase 1a and Phase 1c, and the Nuitka flag
protects the bundle while torch is still shipped.

### 2.6 Documentation

- `docs/adr/0005-silero-vad.md` is **already wrong** about the current backend
  (it describes an older state). Update it to reflect the ONNX migration and
  the hidden-state threading requirement.
- `docs/adr/0020-desktop-runtime-migration-analysis.md:954` says
  "vad.py, silero_vad.jit — Unchanged" — this becomes false. Update.

---

## 3. Part B — Parakeet → ONNX

### 3.1 Current state (verified)

- **File:** `voice_typer/server/parakeet_engine.py` (~1,400 LOC).
- **Class:** `ParakeetEngine` (`parakeet_engine.py:79`).
- **Current backend:** `transformers` + `torch`. The `transformers` library is
  excluded from the frozen Nuitka build, so Parakeet **cannot run in the
  shipped app today** — only in dev. This is the primary motivation for the
  ONNX migration: it makes Parakeet actually shippable.
- **Model:** NVIDIA Parakeet TDT 0.6B (Token-and-Duration Transducer).
- **HF repo (current):** pinned in `parakeet_engine.py` (verify before
  migration — the FP16 ONNX variant lives at `grikdotnet/parakeet-tdt-0.6b-fp16`).
- **torch imports:** `parakeet_engine.py:316`, `:373` (plus `transformers`).
- **Chunking:** 25 s chunks, 3 s overlap. `asr_utils.split_audio()` already
  used (`parakeet_engine.py:845` delegates).
- **Merge:** `_merge_chunks` + `_compute_overlap_skip` (`parakeet_engine.py`,
  module-level). Constants: `MAX_BOUNDARY_SKIP_WORDS=2`, `OVERLAP_DEDUP_WINDOW=3`.
- **English filter:** `is_likely_english()` and `is_latin_char()` are
  **module-level functions** at `parakeet_engine.py:47-78`, NOT private methods.
  `NON_LATIN_RATIO_LIMIT = 0.30`.
- **GPU→CPU fallback:** `_maybe_retry_cuda` (`parakeet_engine.py:1135`) uses
  torch's in-place `.to("cpu")`. Emits `parakeet_cpu_fallback` event
  (`parakeet_engine.py:1355`), consumed by `tray.py:334,561`.
- **CUDA error detection:** `transcribe_with_fallback` at `parakeet_engine.py:1259`
  checks `{"cuda", "cublas", "cudnn"}` only (3 keywords, no "out of memory").
  Separately, `parakeet_engine.py:955` checks OOM with a more specific qualifier.

### 3.2 The "already downloaded ONNX" claim is FALSE

The companion plan says "you already downloaded the ONNX version." The
investigation found **no** `*.onnx` files anywhere in the repo, **no**
`grikdotnet` reference, **no** `onnx-parakeet` manifest entry in
`model_hashes.json`, and **no** `onnx_engine.py`. The plan must specify the
download path, HF repo, revision, and SHA pinning explicitly.

### 3.3 TDT decoding — pick one approach explicitly

Parakeet TDT (Token-and-Duration Transducer) decoding is non-trivial. There
are two viable approaches; the plan must pick one:

#### Option B-1 (recommended): use `onnx-asr` end-to-end

The `onnx-asr` library (`pip install onnx-asr`) wraps the ONNX Parakeet model
and exposes a `Model.recognize(audio, sample_rate)` API. This is the
lowest-effort path — the decoding loop is the library's problem.

```python
# voice_typer/server/parakeet_engine.py (rewritten)

import onnx_asr  # type: ignore[import-untyped]

class ParakeetEngine:
    def __init__(self, device="cuda", language="en", config=None):
        self.device = device
        self.language = language
        self.config = config
        self._model: onnx_asr.Model | None = None
        self._lock = threading.RLock()
        self._cpu_fallback_notified = False

    def load(self, progress_callback=None) -> bool:
        # onnx_asr.Model is class-based, NOT onnx_asr.load_model(...)
        # The old plan's pseudocode (load_model) is wrong.
        providers = self._select_providers(self.device)
        self._model = onnx_asr.Model(
            "nemo-parakeet-tdt-0.6b-v3",
            quantization="fp16",
            providers=providers,
        )
        return True

    def transcribe(self, audio, audio_stats=None):
        if len(audio) / WHISPER_SAMPLE_RATE < 25:
            text = self._model.recognize(audio, sample_rate=16000)
        else:
            chunks = asr_utils.split_audio(audio, 25, 3)
            texts = [self._model.recognize(c, sample_rate=16000) for c in chunks]
            text = asr_utils.merge_chunks(texts)
        if self.language == "en" and not is_likely_english(text):
            return ""
        if audio_stats and should_reject_low_audio_hallucination(text, audio_stats.rms):
            return ""
        return text
```

**Verify before committing:** the `onnx_asr` API is class-based (`Model(...)`),
not a `load_model(...)` function as the old plan's pseudocode showed. Confirm
the exact constructor signature against the installed `onnx-asr` version
(`pyproject.toml` should pin `onnx-asr>=0.12.0`). Add a
`voice_typer/stubs/onnx_asr.pyi` stub for pyrefly/mypy.

#### Option B-2 (not recommended): custom decoder via raw `onnxruntime`

If `onnx-asr` is unsuitable (e.g., API mismatch, missing features), the
alternative is to load the encoder + decoder ONNX files directly via
`onnxruntime.InferenceSession` and write the TDT decoding loop (greedy or
beam search) in Python. This is ~100-200 lines of non-trivial code and
requires understanding the TDT algorithm (token + duration prediction, blank
handling, alignment). It should only be chosen if Option B-1 is blocked.

### 3.4 GPU→CPU fallback — session recreation, not `.to("cpu")`

Unlike PyTorch, ONNX Runtime cannot move a session between providers in place.
The fallback must recreate the session with `CPUExecutionProvider` only:

```python
def transcribe_with_fallback(self, audio, audio_stats=None):
    try:
        return self.transcribe(audio, audio_stats)
    except Exception as exc:
        if self.device == "cuda" and is_cuda_error(exc):
            self._unload_impl()
            self.device = "cpu"
            self._load_impl(providers=["CPUExecutionProvider"])
            text = self.transcribe(audio, audio_stats)
            if not self._cpu_fallback_notified:
                publish("parakeet_cpu_fallback", {})
                self._cpu_fallback_notified = True
            return text
        raise TranscriptionBackendError(...)
```

This is multi-second latency (session recreation + weight reload). The plan
must acknowledge this cost — it is NOT a free swap like torch's `.to("cpu")`.

### 3.5 Model metadata + integrity

#### 3.5.1 `MODEL_REGISTRY` update (in place)

Update the existing `"parakeet"` entry in `voice_typer/server/model_registry.py`
(NOT a new entry):

```python
"parakeet": ModelMetadata(
    name="parakeet",
    download_size_mb=1300,  # verified: grikdotnet/parakeet-tdt-0.6b-fp16 is 1,275,466,609 bytes
    required_vram_mb=3072,  # estimate — verify with real ORT GPU run
    backend="parakeet",
    multilingual=True,
    supported_languages=None,
    description="Parakeet TDT 0.6B FP16 via ONNX Runtime. Fast, efficient, no PyTorch needed.",
    network_behavior="downloads-on-first-use-consent-gated",  # see §3.5.3
    repo_id="grikdotnet/parakeet-tdt-0.6b-fp16",
    speed_rating="fast",
    accuracy_rating="high",
),
```

**Note on `required_vram_mb=3072`:** this was estimated for PyTorch. ONNX
Runtime's memory footprint differs — typically lower due to no Python overhead
but the CUDA execution provider allocates arena memory. Verify with a real
`nvidia-smi` measurement before pinning.

#### 3.5.2 `model_hashes.json` repopulation

Run `scripts/populate_model_hashes.py` after the new model files are
downloaded. The script auto-populates the `files` dict via the HF Tree API +
LFS pointer parsing. **Do not hand-edit `model_hashes.json`.** The old plan
implied manual SHA pinning — the script is the canonical path.

Also update `_MODEL_SIZE_MB` in `asr_utils.py:44-78` to include
`"parakeet": 1300` (parallel to the registry entry) — otherwise the
disk-space pre-check (`_check_disk_space_for_download`) false-passes.

#### 3.5.3 Consent gating — CONFLICT with the companion plan

The existing `"parakeet"` entry is pinned to
`network_behavior="downloads-on-first-use-no-consent"` by
`tests/test_model_registry.py::test_parakeet_is_no_consent` (G4-H-04 known
bug). The GDPR-driven `huggingface_consent` gate
(`service/model.py:854-912`, CR-11) requires consent for HF downloads.

**Decision:** the ONNX migration is the right moment to fix this bug. Set
`network_behavior="downloads-on-first-use-consent-gated"` and update the test.
The companion plan's "no progress bar, no dialog, no toast" pack download
applies to the **pack itself** (the runtime), NOT to the model weights —
model downloads keep their existing consent gate and progress UI.

#### 3.5.4 Allow patterns for ONNX files

Update `voice_typer/server/security/model_integrity.py:553-587` (NOT
`_model_integrity.py`, which is a 23-line backward-compat shim). Add an
`ALLOW_PATTERNS_PARAKEET_ONNX` constant following the existing naming
convention (the old plan's `ALLOW_PATTERNS_ONNX` violates the convention):

```python
ALLOW_PATTERNS_PARAKEET_ONNX = frozenset({
    "*.onnx",
    "config.json",
    "tokenizer.json",
    "vocab.txt",
    "special_tokens_map.json",
    "generation_config.json",
})
```

Parakeet TDT needs more than the old plan's `*.onnx, config.json, vocab.txt` —
the tokenizer files are required for decoding.

### 3.6 Tests to add

- `tests/test_parakeet_onnx_load.py` — load the ONNX model, verify
  `is_available()` and `load()` succeed.
- `tests/test_parakeet_onnx_transcribe.py` — transcribe a known WAV fixture,
  verify the text matches the PyTorch baseline within an edit-distance
  threshold (parity test).
- `tests/test_parakeet_onnx_gpu_fallback.py` — mock a CUDA OOM, verify the
  session is recreated on CPU and the `parakeet_cpu_fallback` event fires.
- `tests/test_parakeet_onnx_sha.py` — verify the downloaded model files match
  `model_hashes.json`.
- `tests/test_parakeet_onnx_abort.py` — verify `RunOptions` can abort a
  long-running transcription (ORT supports this via `RunOptions`).

### 3.7 Diagnostic export

Update `voice_typer/server/diagnostics_export.py` to report:
- `onnxruntime.__version__`
- `onnxruntime.get_available_providers()`
- `onnxruntime.get_device()` (for GPU)
- The SHA-256 of `silero_vad.onnx` and the Parakeet ONNX files.

Also update `scripts/diagnostics.py:175-199` (the CLI producer) in lockstep —
it has its own torch + ctranslate2 block.

---

## 4. Part C — Qwen → ONNX (scope correction)

### 4.1 The misidentification

The companion plan (`plan-runtime-pack-split.md` Section 3, Phase 1b) says:

> **Qwen (LLM)** — ✅ Convert. Decided (2026-08-12): Qwen converts to
> `onnxruntime-genai` (Microsoft's LLM runtime). Real work: model export,
> chat-template + sampling loop in `qwen_engine.py`, reply-quality parity
> tests. Adds ~15 MB to the pack. This removes the last torch user in the
> project.

This is **based on a misidentification**. The investigation verified:

- **File:** `voice_typer/server/qwen_engine.py` (1,132 LOC).
- **Class:** `QwenEngine` (`qwen_engine.py:79`).
- **Library:** `qwen_asr` (`pyproject.toml:351` pins `qwen-asr>=0.1,<1`).
- **Model:** `Qwen3-ASR-1.7B` (`model_registry.py:357`).
- **API:** `qwen_asr.Qwen3ASRModel.from_pretrained(path)` then
  `model.transcribe((audio, sample_rate), language=...)` — this is an **ASR
  (audio transcription) API**, not an LLM text-generation API.
- **There is NO chat template, NO sampling loop, NO streaming, NO tool calls,
  NO text generation in `qwen_engine.py`.**

### 4.2 Why `onnxruntime-genai` is the wrong tool

`onnxruntime-genai` (Microsoft's ONNX Runtime GenAI) is a library for running
**SLMs/LLMs and multi-modal LLMs**. I inspected the actual wheel
(`onnxruntime-genai==0.15.2` from PyPI): its `models/builders/qwen.py` imports
`Qwen2ForCausalLM`, `Qwen2_5_VLForConditionalGeneration`,
`Qwen3VLForConditionalGeneration` from `transformers` — i.e., Qwen **text**
LLMs and **VL** multimodal LLMs. **There is no Qwen3-ASR / Qwen-Audio builder
anywhere in the wheel.** Using `onnxruntime-genai` for Qwen3-ASR is not
possible without writing a custom builder, which is more work than the
alternatives below.

### 4.3 Revised options for Qwen

Pick one of these — the companion plan's "committed decision" is void.

#### Option C-1 (recommended): keep `qwen_asr` library, replace its torch backend

The `qwen_asr` library itself depends on `torch` + `transformers`. The cleanest
path is to check whether `qwen_asr` has (or will have) an ONNX Runtime backend
option. If yes, switch the backend flag. If no, this option is blocked.

**Action:** open an issue on the `qwen_asr` repo to ask about ONNX Runtime
support. Do NOT proceed with this option until the maintainer confirms.

#### Option C-2: export Qwen3-ASR to ONNX manually

Use `torch.onnx.export()` (still requires torch in the **dev** environment,
not the runtime) to export the Qwen3-ASR encoder + decoder to ONNX, then load
via `onnxruntime.InferenceSession` and write a custom transcription loop.

This is significant work:
- Export the encoder (audio → hidden states).
- Export the decoder (hidden states → tokens).
- Write the CTC/attention decoding loop in Python (~150-300 LOC depending on
  the model architecture).
- Build the tokenizer from the HF tokenizer files.
- Verify parity against the torch baseline.

**Rough effort estimate:** 2-4 weeks of focused work, plus parity testing.

#### Option C-3 (recommended for Phase 1): defer Qwen migration

Given the misidentification, the safest path is to **defer Qwen's ONNX
migration** to a separate phase after VAD + Parakeet are done. This means:

- Phase 1a: VAD → ONNX (silero).
- Phase 1b: Parakeet → ONNX (`onnx-asr`).
- Phase 1c (revised): torch removal is **NOT total** until Qwen is migrated.
  The sweep covers VAD + Parakeet + the 7 supporting modules
  (`asr_utils`, `resource_probe`, `diagnostics_export`, `nvidia_dll_paths`,
  `transcription`, `prewarm/__init__`, `prewarm/cache_probe`), but
  `qwen_engine.py` keeps torch + transformers.
- Phase 1d (new): Qwen → ONNX, then the final torch sweep on `qwen_engine.py`.

This unblocks the installer-size win (VAD + Parakeet are the big torch users)
while honestly acknowledging that Qwen needs its own investigation.

### 4.4 What stays the same regardless of option

- `_dedup_overlap` (`qwen_engine.py:811-857`) — pure-string audio-chunk-merge
  algorithm. Must be preserved in any rewrite. It is DIFFERENT from
  Parakeet's `_merge_chunks` (case-sensitive, no punctuation stripping).
- `_split_audio` (`qwen_engine.py:860`) — already delegates to
  `asr_utils.split_audio()`. No change.
- CUDA error detection — Qwen uses `{"cuda", "cublas", "cudnn"}` at
  `qwen_engine.py:943`. See §5.1 for the shared helper.
- GPU→CPU fallback (`qwen_engine.py:921-989`) — uses torch's `.to("cpu")` +
  `.to(torch.float32)`. ONNX migration requires session recreation (see §3.4).
- Existing tests mock the model — there is no real-inference baseline for
  "parity tests." Any parity work must first establish a baseline.

### 4.5 Platform gaps in `onnxruntime-genai` (if Option C-1/C-2 is chosen)

- Latest `onnxruntime-genai` (0.15.2) supports Python ≥3.10; the project
  requires Python ≥3.10 (`pyproject.toml:30`). Pin to a compatible version.
- No macOS x86_64 wheel exists at any version. macOS Intel users cannot use
  `onnxruntime-genai`. The plan must address this (either drop macOS Intel
  support for Qwen, or fall back to CPU-only `onnxruntime`).
- The `onnxruntime-genai` wheel for Windows is ~11.5 MB compressed, but it
  **requires `onnxruntime` as a dep** (already in deps) and does NOT include
  model weights. Qwen3-ASR-1.7B at FP16 is ~3.4 GB — the "~15 MB" figure in
  the companion plan is the wheel size only, not the total pack impact.

---

## 5. Part D — Shared utilities (what's left)

### 5.1 `is_cuda_error()` — do NOT collapse the classifier

The old plan proposed a shared `CUDA_ERROR_KEYWORDS` frozenset:

```python
CUDA_ERROR_KEYWORDS = frozenset({"cuda", "cublas", "cudnn", "out of memory"})
def is_cuda_error(exc): ...
```

This is **lossy**. The actual classifier in `transcription.py:1377-1386` is a
5-layer check:

1. `isinstance(exc, torch.cuda.OutOfMemoryError)` — class hierarchy (dies with torch).
2. `isinstance(exc, RuntimeError)` and MRO check.
3. Attribute check (`exc.cuda_error`, etc.).
4. Keyword match on `{"cuda", "cublas", "cudnn"}` (3 keywords, no "out of memory").
5. `_probe_cuda_runtime` (`transcription.py:578-586`) checks 7 keywords
   including `"dll"`, `"not found"`, `"cannot be loaded"`, `"load library"` —
   critical for Windows DLL-load failure detection.

And `parakeet_engine.py:955` checks OOM with a more specific qualifier
(`"out of memory"` alone is too broad — it matches CPU RAM exhaustion too).

**Decision:** extract `is_cuda_error()` to `asr_utils.py` but preserve the
5-layer structure. Do NOT collapse to a 4-keyword frozenset. The function
signature stays the same; the implementation keeps the layers and adapts
layer 1 (drop `torch.cuda.OutOfMemoryError`, replace with ORT exception
types) and layer 4 (keep the 3-keyword set; ORT errors include "cuda" in
their messages).

```python
# voice_typer/server/asr_utils.py

def is_cuda_error(exc: Exception) -> bool:
    """Return True if *exc* looks like a GPU runtime failure."""
    # Layer 1: ORT CUDA exceptions (replaces torch.cuda.OutOfMemoryError)
    try:
        import onnxruntime as ort
        if isinstance(exc, ort.RuntimeException):
            msg = str(exc).lower()
            if "cuda" in msg or "gpu" in msg:
                return True
    except ImportError:
        pass
    # Layer 2-3: isinstance + attribute checks (unchanged)
    if isinstance(exc, RuntimeError):
        if getattr(exc, "cuda_error", False):
            return True
    # Layer 4: keyword match (3 keywords — OOM handled separately)
    err_str = str(exc).lower()
    if any(kw in err_str for kw in ("cuda", "cublas", "cudnn")):
        return True
    # Layer 5: DLL-load failures (Windows)
    if any(kw in err_str for kw in ("dll", "not found", "cannot be loaded", "load library")):
        return True
    return False

def is_oom_error(exc: Exception) -> bool:
    """Separate OOM check — kept distinct to avoid matching CPU RAM exhaustion."""
    err_str = str(exc).lower()
    return "out of memory" in err_str or "oom" in err_str
```

This preserves the behavior of `tests/test_transcription_cuda_classifier.py`
(which pins the 5-layer classifier) and `parakeet_engine.py:955`'s separate
OOM check.

### 5.2 `release_gpu_memory()` — no ORT equivalent

`asr_utils.release_gpu_memory()` currently calls `torch.cuda.empty_cache()`.
ONNX Runtime has **no** `empty_cache()` API — the CUDA arena is freed when
the session is destroyed. The function becomes a no-op:

```python
def release_gpu_memory() -> None:
    """No-op for ONNX Runtime. ORT frees the CUDA arena on session destroy."""
    # Kept for API compatibility — callers in unload() still call this.
    # After total torch removal, this can be deleted and callers updated.
    pass
```

### 5.3 `is_likely_english()` / `is_latin_char()` — already module-level

The old plan called these "ParakeetEngine's private methods." They are
**module-level functions** at `parakeet_engine.py:47-78`. Move them to
`asr_utils.py` (alongside `split_audio` which is already there) and update
the import in `parakeet_engine.py`. The ONNX-rewritten Parakeet engine
imports them directly from `asr_utils`.

### 5.4 `merge_chunks()` / `compute_overlap_skip()` — extract

Currently module-level in `parakeet_engine.py`. Move to `asr_utils.py` so
that the rewritten Parakeet engine and any future ONNX variant can share
them. `QwenEngine._dedup_overlap` stays private (different algorithm).

### 5.5 What does NOT need extraction

- `split_audio()` — already in `asr_utils.py:390`.
- `_download_with_retry()` — already in `asr_utils.py`.
- `cleanup_hf_cache_dir()` — already in `asr_utils.py`.
- `_check_disk_space_for_download()` — already in `asr_utils.py`.
- `_require_huggingface_consent()` — already in `asr_utils.py` /
  `service/model.py:854-912`.
- `unload()` boilerplate — 5 lines, not worth extracting (the old plan
  agreed).

---

## 6. Part E — GPU handling and NVIDIA DLLs

### 6.1 The critical inaccuracy in the companion plan

The companion plan says:

> `nvidia_dll_paths.py` — finds NVIDIA DLLs under `torch/lib` — dies with
> CPU-only; GPU variants source DLLs from the onnxruntime-gpu package.

This is **false**. `onnxruntime-gpu` does **not** bundle NVIDIA DLLs. The
wheel expects system-installed CUDA Toolkit + cuDNN. Migrating to
`onnxruntime-gpu` would re-introduce the very DLL-discovery problem
`nvidia_dll_paths.py` was written to solve.

### 6.2 What `nvidia_dll_paths.py` actually does

`voice_typer/server/nvidia_dll_paths.py` (405 LOC) scans a 4-root × 4-subpath
matrix:

- Roots: site-packages (4 variants: venv, user, system, conda).
- Subpaths: `nvidia/cublas/bin`, `nvidia/cudnn/bin`, `nvidia/cuda_nvrtc/bin`,
  `torch/lib`.

The `torch/lib` path is the CUDA-DLL-001 fallback for GPU torch wheels. The
`nvidia/*` paths cover the `nvidia-*-cu12` PyPI wheels (which bundle CUDA
DLLs without torch). The module is NOT torch-specific — the `nvidia/*` paths
survive torch removal.

### 6.3 Post-migration state

After torch removal:
- The `torch/lib` scan path dies (no torch installed).
- The `nvidia/*` scan paths survive and become the primary DLL source.
- `nvidia_dll_paths.py` itself is **KEPT** (with the `torch/lib` branch
  removed). The companion plan's claim that it "dies with CPU-only" is wrong
  — it dies only if `nvidia-*-cu12` wheels are also removed, which they
  should not be (they're the CUDA-DLL source for GPU onnxruntime-gpu).
- ctranslate2's GPU path also depends on these DLLs (it does NOT depend on
  torch — verified via lockfile: `ctranslate2==4.8.1` pulls
  `onnxruntime==1.28.0` CPU). Removing torch preserves ctranslate2's CPU path
  but breaks Windows GPU unless the `nvidia-*-cu12` wheels remain.

### 6.4 `resource_probe.py` — mostly already backend-agnostic

`voice_typer/server/resource_probe.py` (276 LOC) probes RAM, disk, and GPU.
Only the 13-line GPU-memory block (L200-234) is torch-specific, wrapped in
`try/except Exception` with DEBUG fallback. RAM/disk checks use
`psutil`/`ctypes`/`shutil`/`os.statvfs`. After migration:

- Replace the torch GPU-memory probe with `onnxruntime.get_device()` +
  `nvidia-smi` subprocess (or `pynvml` if available).
- Wrap in the same `try/except Exception` pattern.
- Update the probe result schema in `diagnostics_export.py` in lockstep.

### 6.5 `transcription.py` — only one torch touchpoint

`voice_typer/server/transcription.py` has exactly one torch dependency:
`isinstance(exc, torch.cuda.OutOfMemoryError)` at L1338. The GPU availability
check at `_resolve_device:227-263` already uses ctranslate2 + ctypes (no
torch). After migration:

- Replace the `isinstance` check with `is_oom_error(exc)` (see §5.1).
- The rest of `transcription.py` is already torch-free.

### 6.6 macOS GPU

macOS has no CUDA. `onnxruntime` on macOS supports `CoreMLExecutionProvider`
and `MetalExecutionProvider` (via `onnxruntime-genai` for LLMs, or directly
for non-LLM models). The companion plan is Windows-focused and does not
address macOS GPU. The ONNX migration should:

- Pin `providers=["CPUExecutionProvider"]` on macOS for VAD (same as Windows
  + Linux — VAD is CPU-only by design).
- For Parakeet on macOS: test whether `CoreMLExecutionProvider` offers
  meaningful speedup over CPU. If yes, add it as an option. If no, CPU-only
  is acceptable (Parakeet 0.6B FP16 runs in real-time on M1 CPU).

---

## 7. Part F — Tests, stubs, diagnostics, constraints

### 7.1 Stubs

Add `voice_typer/stubs/onnx_asr.pyi` if Option B-1 (`onnx-asr` library) is
chosen for Parakeet. Without this stub, pyrefly/mypy will fail on
`import onnx_asr  # type: ignore[import-untyped]` — the `# type: ignore` is a
temporary measure; the stub is the proper fix.

### 7.2 Ratchet baselines

After the migration, the ratchet baselines need regeneration:

- `coverage-baseline.json` — total is 65.23% today. Removing torch tests may
  drop coverage. Run `scripts/coverage_ratchet_check.py --regenerate --force`
  after the test rewrites are stable.
- `mypy-baseline.json` — 696 errors today. Torch-specific ignores (e.g.,
  `transformers.*` overrides at `pyproject.toml:791`) become stale. Regenerate.
- `pyrefly-baseline.json` — ~276 entries. 14+ entries for
  `parakeet_engine.py`/`qwen_engine.py`/`prewarm/*` go stale when rewritten.
  Regenerate.
- `ruff-baseline.json` — torch-specific noqa comments become stale.

**Note:** ratchets refuse to auto-regenerate on improvement — the
`--regenerate --force` flag is required.

### 7.3 Doc-accuracy tests

These tests pin specific facts in the docs and will fail if the docs are not
updated in lockstep with the code:

- `tests/test_api_doc_accuracy.py`
- `tests/test_architecture_doc_accuracy.py` (pins 36-event bus count — the
  count changes if new events are added)
- `tests/test_doc_command_counts.py`
- `tests/test_security_doc_command_count.py`
- `tests/test_techdebt_todos_freshness.py` (pins TECH-DEBT TODOs in
  `prewarm/__init__.py` that reference torch + transformers)

Update the corresponding docs in the same PR as the code changes.

### 7.4 CONSTRAINTS.md rules that need USER action

The following CONSTRAINTS.md rules are touched by this plan. **Agents may not
edit CONSTRAINTS.md** (CONSTRAINTS.md L12 + AGENTS.md L243). The user must
make these changes after the migration is verified:

| Rule | What it says | Action needed |
|---|---|---|
| C-CI-8 / NU-106 | Protects the Nuitka bundle while torch is shipped; mandates `--module-parameter=torch-disable-jit=no` | Retire after Phase 1c verification (grep frozen bundle for torch → zero hits). |
| C-CI-11 | Enumerates exactly 4 code-signing steps (sidecar+prewarm+native listener; NSIS; MSI; standalone exe) | If a worker exe is added (companion plan), update to include the 5th binary. |
| C-DATA-1 | Allows 3 categories of network calls; pack download from GitHub Releases is not covered | Extend category (3) "model downloads" → "runtime asset downloads" or add category (4). |
| C-I18N-1 | 8 locale files must stay in parity | No change to the rule itself, but new strings must be added to all 8 (en, ar, de, es, fr, hi, ru, zh). |
| C-BRAND-1 | `APP_NAME` stays a constant, never inlined | No change; new strings must use `{appName}` placeholder. |

### 7.5 AGENTS.md rules

`AGENTS.md` (557 lines) embeds CONSTRAINTS.md verbatim. No AGENTS.md rule
blocks this plan, but the plan must respect:

- "Agents may never edit CONSTRAINTS.md" (L243).
- The full dev-loop guidance (plan → execute → verify → commit).

### 7.6 The hidden 4th allowlist

The companion plan says "three allowlists in lockstep" (`_COMMAND_REGISTRY`,
`ALLOWED_COMMANDS`, `PythonRequest`/`PythonPushEvent`). There is actually a
**fourth**: `ALLOWED_EVENT_TYPES` at
`src-tauri/src/sidecar/ws/event_protocol.rs:49` (40 entries). It has **no
parity test** — adding a Python event without adding it here silently drops
the frame. Any new IPC event added by this plan or the companion plan must
be added to all four allowlists, and a parity test for the fourth should be
added (mirroring `tests/test_ipc_command_registry_sync.py`).

---

## 8. Verification gates

### 8.1 Phase 1a (VAD → ONNX) gate

- `tests/test_vad.py` passes with the ORT backend.
- `tests/test_vad_dtype_optimization.py` deleted.
- `tests/test_electron_ipc_and_build.py:498` updated.
- `bench/bench_vad.py --include-silero` runs and reports latency ≤ the
  torch baseline (record the number in `bench/bench-baseline.json`).
- `silero_vad.onnx` is bundled in the sidecar build (verify via
  `scripts/build/build_sidecar_windows.sh` dry-run).
- `docs/adr/0005-silero-vad.md` updated.
- `vad.py` no longer imports torch.

### 8.2 Phase 1b (Parakeet → ONNX) gate

- `tests/test_parakeet_onnx_*.py` (5 new test files) pass.
- Parakeet transcribes a known WAV fixture within an edit-distance threshold
  of the torch baseline (parity).
- `onnx_asr` pinned in `pyproject.toml`.
- `voice_typer/stubs/onnx_asr.pyi` added.
- `model_hashes.json` repopulated via `scripts/populate_model_hashes.py`.
- `MODEL_REGISTRY["parakeet"]` updated (repo_id, download_size_mb,
  network_behavior).
- `tests/test_model_registry.py::test_parakeet_is_no_consent` updated to
  `test_parakeet_is_consent_gated` (fixes G4-H-04).
- `diagnostics_export.py` + `scripts/diagnostics.py` report ORT info.
- `parakeet_engine.py` no longer imports torch or transformers.

### 8.3 Phase 1c (torch sweep) gate — revised

If Option C-3 (defer Qwen) is chosen, the gate is:

- `grep -ri "import torch\|from torch" voice_typer/` returns hits ONLY in
  `qwen_engine.py`.
- `grep -ri "transformers" voice_typer/` returns hits ONLY in
  `qwen_engine.py` and `pyproject.toml` (transformers stays as a dep until
  Qwen is migrated).
- `scripts/diagnostics.py:175-199` updated.
- All 4 ratchet baselines regenerated.
- Doc-accuracy tests updated.
- CONSTRAINTS.md rule C-CI-8/NU-106 retired by the user.

### 8.4 Phase 1d (Qwen → ONNX) gate — new

This gate is defined after the Qwen migration option is chosen (see §4.3).
Until then, Qwen keeps torch + transformers, and the "total torch removal"
claim is honestly scoped to "total except Qwen."

---

## 9. Summary of file changes

| Action | File | Phase |
|---|---|---|
| **NEW** | `voice_typer/server/silero_vad.onnx` | 1a |
| **REWRITE** | `voice_typer/server/vad.py` (ORT backend + hidden state) | 1a |
| **MODIFY** | `voice_typer/server/asr_utils.py` (add `is_cuda_error`, `is_oom_error`, `is_likely_english`, `is_latin_char`, `merge_chunks`, `compute_overlap_skip`; make `release_gpu_memory` a no-op) | 1a/1b |
| **MODIFY** | `voice_typer/server/parakeet_engine.py` (rewrite to ORT; import from `asr_utils`) | 1b |
| **MODIFY** | `voice_typer/server/resource_probe.py` (replace torch GPU probe with ORT) | 1c |
| **MODIFY** | `voice_typer/server/diagnostics_export.py` (report ORT info) | 1c |
| **MODIFY** | `voice_typer/server/transcription.py` (drop `torch.cuda.OutOfMemoryError` isinstance) | 1c |
| **MODIFY** | `voice_typer/server/nvidia_dll_paths.py` (drop `torch/lib` branch, keep `nvidia/*`) | 1c |
| **MODIFY** | `voice_typer/server/prewarm/cache_probe.py` (update package list) | 1c |
| **MODIFY** | `scripts/diagnostics.py:175-199` (CLI producer) | 1c |
| **MODIFY** | `pyproject.toml` (add `onnx-asr`, drop `torch>=2.0,<3.0` — except Qwen if deferred) | 1c |
| **REGENERATE** | `requirements-lock.txt` | 1c |
| **REGENERATE** | `coverage-baseline.json`, `mypy-baseline.json`, `pyrefly-baseline.json`, `ruff-baseline.json` | 1c |
| **MODIFY** | `MANIFEST.in` (add `silero_vad.onnx`) | 1a |
| **MODIFY** | `scripts/build/voice-typer.spec` (PyInstaller fallback datas) | 1a |
| **MODIFY** | `scripts/build/build_sidecar_*.sh` (Nuitka data-file inclusion; retire `--module-parameter=torch-disable-jit=no` at 1c) | 1a/1c |
| **MODIFY** | `voice_typer/server/security/model_integrity.py:553-587` (add `ALLOW_PATTERNS_PARAKEET_ONNX`) | 1b |
| **REGENERATE** | `model_hashes.json` (via `scripts/populate_model_hashes.py`) | 1b |
| **NEW** | `voice_typer/stubs/onnx_asr.pyi` | 1b |
| **REWRITE** | `tests/test_vad.py`, delete `tests/test_vad_dtype_optimization.py` | 1a |
| **NEW** | `tests/test_parakeet_onnx_*.py` (5 files) | 1b |
| **MODIFY** | `tests/conftest.py` (strip VAD torch mocks; keep for Qwen) | 1a/1c |
| **MODIFY** | `docs/adr/0005-silero-vad.md`, `docs/adr/0020-desktop-runtime-migration-analysis.md` | 1a |
| **USER-ONLY** | `CONSTRAINTS.md` (retire C-CI-8/NU-106; update C-CI-11, C-DATA-1) | 1c |

---

## 10. Open decisions

1. **Qwen migration option (§4.3).** Pick C-1, C-2, or C-3. Recommendation:
   C-3 (defer) until the `qwen_asr` maintainer confirms ONNX support.
2. **Parakeet decoding approach (§3.3).** Pick B-1 (`onnx-asr` end-to-end)
   or B-2 (custom decoder). Recommendation: B-1.
3. **`required_vram_mb` for Parakeet ONNX (§3.5.1).** Measure with real
   `nvidia-smi` before pinning.
4. **macOS GPU for Parakeet (§6.6).** Test `CoreMLExecutionProvider`
   speedup. If <20% faster than CPU, ship CPU-only.
5. **`onnxruntime-gpu` packaging (§6.1).** Decide whether the GPU variant
   bundles `nvidia-*-cu12` wheels or expects system CUDA. This is a
   companion-plan decision but blocks Phase 1c.
