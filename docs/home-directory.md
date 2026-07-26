# Voice Typer Data Directory

## Where your data lives

Voice Typer stores all of its user data in a single **data directory**.
The location is platform-specific and is resolved by
`voice_typer.server.config._config_dir()`:

| Platform | Default data directory |
|----------|------------------------|
| Windows (new installs) | `%APPDATA%\voice-typer` → `C:\Users\<you>\AppData\Roaming\voice-typer` |
| Windows (existing users) | `%USERPROFILE%\.voice-typer` is still honored if it already exists. The app checks the legacy path **first** (see `config._config_dir()`) and keeps using it, so upgrades are seamless — no data is moved. |
| macOS | `~/Library/Application Support/voice-typer` |
| Linux | `$XDG_DATA_HOME/voice-typer` (falls back to `~/.local/share/voice-typer`) |

You can override the location with the `VOICE_TYPER_CONFIG_DIR` environment
variable (validated against path traversal in `config._config_dir()`).

In the rest of this document, `<DATA_DIR>` refers to that resolved directory.

## Log File Paths (per-platform)

S5-CR-70: the log file path was previously inconsistent across docs —
`README.md` mentioned only the Windows path (`%APPDATA%/voice-typer/voice-typer.log`),
`CONTRIBUTING.md` mentioned only the Unix path (`$HOME/.voice-typer/voice-typer.log`),
and `bug_report.md` mentioned only `~/.voice-typer/voice-typer.log`. This
section is the canonical source of truth — `README.md` and `CONTRIBUTING.md`
both link here.

There are **two** log files (one per process): the Python backend log
and the Tauri Rust host log. They live at *different* paths — the
Python log is at `<DATA_DIR>/voice-typer.log` (directly under the data
dir), while the Rust host log is at `<DATA_DIR>/logs/voice-typer.log`
(in a `logs/` subdir). Verified via:

- Python: `voice_typer/server/log.py:898` — `log_file = config_dir / "voice-typer.log"`
- Rust: `src-tauri/src/platform/logging.rs:22,47,61` — `config_dir.join("logs")` + writer prefix `"voice-typer"`

### Python backend log

Written by `RotatingFileHandler` in `voice_typer/server/log.py`. Rotates
at 5 MiB with 5 backup files kept
(`RotatingFileHandler(maxBytes=5_242_880, backupCount=5)` — ADR-0020 §11).

| Platform | Python log file path |
|----------|----------------------|
| Windows (new installs) | `%APPDATA%\voice-typer\voice-typer.log` → `C:\Users\<you>\AppData\Roaming\voice-typer\voice-typer.log` |
| Windows (existing users) | `%USERPROFILE%\.voice-typer\voice-typer.log` (legacy data directory is honored if it already exists — see above) |
| macOS | `~/Library/Application Support/voice-typer/voice-typer.log` |
| Linux | `$XDG_DATA_HOME/voice-typer/voice-typer.log` (falls back to `~/.local/share/voice-typer/voice-typer.log`) |

Override the location by setting `VOICE_TYPER_CONFIG_DIR` (the log lives
directly under the resolved `<DATA_DIR>` — **not** in a `logs/` subdir).
An earlier draft of this doc claimed the Python log was at
`<DATA_DIR>/logs/voice-typer.log`; that was a bug — the Python
`RotatingFileHandler` writes at `<DATA_DIR>/voice-typer.log` directly
(see `log.py:898`). The `logs/` subdir is reserved for the Rust host
log (below).

### Tauri Rust host log

When running under the Tauri runtime (ADR-0020), the Rust host writes
its own log at `<DATA_DIR>/logs/voice-typer.log` (rotating, 5 MB × 5
backups — see `src-tauri/src/platform/logging.rs:22`). The file is
named `voice-typer.log` (NOT `voice-typer-rust.log` — an earlier draft
of this doc mis-named it; the diagnostics bundle renames it to
`rust-voice-typer.log` only inside the exported zip so the two files
don't collide, but on disk it's `voice-typer.log` in both processes).

| Platform | Rust host log file path |
|----------|-------------------------|
| Windows (new installs) | `%APPDATA%\voice-typer\logs\voice-typer.log` → `C:\Users\<you>\AppData\Roaming\voice-typer\logs\voice-typer.log` |
| Windows (existing users) | `%USERPROFILE%\.voice-typer\logs\voice-typer.log` (legacy data directory is honored if it already exists — see above) |
| macOS | `~/Library/Application Support/voice-typer/logs/voice-typer.log` |
| Linux | `$XDG_DATA_HOME/voice-typer/logs/voice-typer.log` (falls back to `~/.local/share/voice-typer/logs/voice-typer.log`) |

Electron crash logs (when running under the Electron host) land at
`<userData>/electron-crashes.log`.

This design is:

- **Self-contained** — wipe the folder to factory-reset the app
- **Backup-friendly** — one folder to copy
- **Transparent** — you can explore it and understand what is stored

## Folder Structure

```
<DATA_DIR>/
├── README.md                    # This file — describes every file/folder
├── config.json                  # User settings (hotkey, model, mic, etc.)
├── history.db                   # SQLite transcription history
├── huggingface/                 # HF_HOME cache (models + tokenizers)
│   ├── hub/                     #   Actual model blobs (snapshot_download)
│   │   ├── models--Systran--faster-whisper-small.en/
│   │   ├── models--Systran--faster-whisper-medium.en/
│   │   └── ...
│   ├── version                  #   HF cache version stamp
│   └── tokenizers/              #   Tokenizer cache (rarely used)
├── models/ ──junction/symlink──→ huggingface/hub/   ← browsable shortcut
├── venv/                        # Python virtual environment (python -m venv)
│   ├── Scripts/python.exe       #   Python executable (Windows)
│   ├── bin/python               #   Python executable (POSIX)
│   ├── Lib/site-packages/       #   All Python deps (Windows)
│   └── lib/python3.XY/site-packages/  # All Python deps (POSIX)
├── voice-typer.log              # Python backend rotating log (5 MiB × 5 backups)
├── logs/
│   └── voice-typer.log          # Tauri Rust host rotating log (5 MB × 5 backups) — ADR-0020 §11
├── voice-typer-vocabulary.json  # User vocabulary overrides (merged with bundled defaults)
├── voice-typer-corrections.json # User text-corrections overrides (optional; merged with bundled)
└── crash_recovery/
    └── voice-typer-recovery.json
```

> **Windows note:** on a fresh install the directory is
> `%APPDATA%\voice-typer`. If you upgraded from a version that used
> `%USERPROFILE%\.voice-typer`, that folder remains the live data
> directory — do not delete it expecting the app to recreate your data
> under `%APPDATA%`; it will keep using the legacy folder.

## File Descriptions

### `config.json`
Serialised `Config` dataclass. Written by `Config.save()`. Schema version tracked via `schema_version` field. Fields include: `hotkey`, `microphone`, `model_size`, `device`, `language`, `streaming_transcription`, `paste_on_stop`, etc.

### `history.db`
SQLite database (via `voice_typer.server.history_db.HistoryDB`). Contains transcription records with timestamps, model info, audio duration.

### `huggingface/`
HuggingFace cache directory. Set via `os.environ["HF_HOME"]` in `app.py:_setup_logging()`. The `hub/` subdirectory uses HuggingFace's standard `snapshot_download` layout:
- `models--org--name/` directories containing snapshots, blobs, refs
- Managed entirely by `huggingface_hub` — the app does **not** write here directly

### `venv/`
Python virtual environment created by the installer or first-run setup. Contains:
- Python interpreter
- All pip dependencies (faster-whisper, ctranslate2, torch, sounddevice, pynput, pystray, Pillow, etc.)
- CLI entry point (`voice-typer`)

### `voice-typer-vocabulary.json` and `voice-typer-corrections.json`
User-defined vocabulary and correction files. Read by `VocabularyManager` and `configure_corrections()` respectively to build replacement maps for `clean_transcribed_text()`. Both are optional — the app ships with bundled defaults (`voice_typer/server/corrections.json`) that are merged with the user file.

## Model Management

### How models are stored
- `HF_HOME` = `<DATA_DIR>/huggingface/`
- Models download via HuggingFace `snapshot_download` → `huggingface/hub/models--org--name/`
- The app never writes model files directly — HF libraries manage the cache

### Why NOT `<DATA_DIR>/models/`
- `faster-whisper` expects the HF cache layout. Custom download logic would be fragile, hard to maintain, and break with upstream changes.
- **Compromise**: the `models/` folder is a **directory junction** (Windows) or **symlink** (macOS/Linux) pointing to `huggingface/hub/`. Users who browse `<DATA_DIR>/models/` see the actual model files without indirection. Created/fixed on first app run.

### Download flow
1. User selects a model in Settings UI (e.g. "medium.en")
2. Frontend sends `set_config` IPC call
3. Backend's `_try_load_model()` calls `TranscriptionEngine.load()`
4. `TranscriptionEngine` calls `snapshot_download()` → file lands in `huggingface/hub/`
5. On success: tray state → `IDLE`. On failure: tray state → `ERROR`

### Junction / symlink removal
The old code created a junction from `<DATA_DIR>/huggingface/` → `~/.cache/huggingface/` to reuse pre-existing downloads. This was a migration hack and **must not be done in the shipped product**. The `HF_HOME` env var is sufficient.

**Migration for existing users**: copy `~/.cache/huggingface/hub/` → `<DATA_DIR>/huggingface/hub/` on first run after upgrade, then remove the junction.

## GPU / CUDA

### What lives in `<DATA_DIR>`
- Python CUDA packages: installed by pip into `venv/Lib/site-packages/`
  - `ctranslate2` (with CUDA extensions)
  - `nvidia-*` wheels (CUDA runtime DLLs, cuBLAS, cuDNN)
  - `torch` (if PyTorch-based models are used)

### What does NOT live in `<DATA_DIR>`
- **NVIDIA system driver** (`C:\Program Files\NVIDIA GPU Computing Toolkit\`) — this is a system-level component installed by the user or driver update. The Python process merely **loads** these DLLs at runtime. Cannot be bundled.
- **CUDA toolkit** (`C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.x`) — only needed for compilation, not runtime. Not required for users.

### How GPU detection works
- `TranscriptionEngine._probe_cuda()` checks `ctranslate2.get_cuda_device_count()`
- Result is stored in `self.device_info` and displayed in tray tooltip / Settings UI
- If CUDA is unavailable, falls back to CPU (`device="cpu"`)

## First-Run Setup

When a user launches the app for the first time (no `<DATA_DIR>` exists yet):

### Python backend (`app.py` + `config.py`)
1. `_migrate_from_legacy()` — copies from `%APPDATA%/voice-typer/` if present (one-time)
2. `_config_dir().mkdir(parents=True, exist_ok=True)` — creates `<DATA_DIR>`
3. `Config.load()` detects missing config.json → creates with defaults
4. `os.environ["HF_HOME"]` set to `<DATA_DIR>/huggingface/`
5. Logging handler creates `<DATA_DIR>/voice-typer.log` (Python backend log; the Tauri Rust host's `voice-typer.log` lives under `<DATA_DIR>/logs/` — see §Log File Paths above)
6. Tray icon renders (assets from `voice_typer/server/assets/`)
7. `create_launcher_shortcut()` creates desktop shortcut + `<DATA_DIR>/icon.ico`
8. `models/` junction/symlink → `huggingface/hub/` is created if missing

### Electron frontend (first window)
1. IPC connection established (10 retries, 1s apart)
2. `get_config` call populates Settings UI
3. `get_microphones` populates mic selector
4. `get_history` + `get_today_stats` populate History page
5. StatusBar shows connection state and recording state

### NSIS installer (`electron-builder`) — TBD
Currently the installer does NOT create `<DATA_DIR>`. The Python backend creates it on first launch. Options:

- **Option A** (current, simple): Python backend creates everything on first run. No installer changes needed.
- **Option B** (recommended for v1 release): NSIS post-install script runs `python -m voice_typer.server.setup` which creates the folder structure + venv + base config. Slower install but faster first launch.
- **Option C**: Electron main process runs setup before spawning Python.

See "Implementation Checklist" below for what an AI agent should build for Option B/C.

## Error States & Recovery

| Problem | Behaviour |
|---------|-----------|
| `<DATA_DIR>` missing | Created on first `_config_dir().mkdir()` |
| `config.json` missing or corrupt | `Config.load()` creates defaults, logs error |
| `history.db` missing or corrupt | `HistoryDB` creates schema automatically |
| `model/` junction broken | Recreated on startup (`_ensure_model_junction()`) |
| `huggingface/hub/` missing | Models are re-downloaded on next load attempt |
| Disk full / permission denied | Backend catches `OSError`, sets tray state → `ERROR` |
| `venv/` missing | App cannot start (Python won't even run). Installer must ensure venv is valid. |

---

## Implementation Checklist

For an AI agent tasked with implementing the folder structure recommendations:

### 1. Remove junction hack
- [ ] In `voice_typer/server/config.py` or `voice_typer/server/app.py`: remove any code that creates a junction/symlink from `<DATA_DIR>/huggingface/` to `~/.cache/huggingface/`
- [ ] Keep `os.environ.setdefault("HF_HOME", str(config_dir / "huggingface"))` in `app.py:_setup_logging()`
- [ ] On first run after upgrade: detect existing junction, copy `hub/` contents, remove junction

### 2. Create `models/` → `huggingface/hub/` junction/symlink
- [ ] Add `_ensure_model_junction()` helper in `config.py` or a new `setup.py`
- [ ] Call it from `VoiceTyperApp.__init__()` or during `_setup_logging()`
- [ ] Windows: `os.symlink(hub_path, models_path, target_is_directory=True)` with appropriate fallback to junction
- [ ] macOS/Linux: `os.symlink(hub_path, models_path, target_is_directory=True)`
- [ ] Handle existing broken symlinks (remove and recreate)

### 3. Vocabulary / corrections files (DONE)
- [x] `VocabularyManager` reads `config_dir / "voice-typer-vocabulary.json"` (merged with bundled defaults)
- [x] `configure_corrections()` reads `config_dir / "voice-typer-corrections.json"` (merged with bundled defaults)
- [x] Both files are optional — the app works without them using bundled defaults

### 4. Write `<DATA_DIR>/README.md` on first run
- [ ] In `_setup_logging()` or `VoiceTyperApp.__init__()`, check if `config_dir / "README.md"` exists
- [ ] If not, write a copy of this document (or a condensed user-facing version)
- [ ] The README should list every folder/file with a short description and tell the user not to delete model files manually

### 5. NSIS installer setup (electron-builder)
- [ ] Add an NSIS script in `voice_typer/client/build/installer.nsh` that:
  - Creates `$PROFILE\.voice-typer\` and subfolders
  - Runs `python -m venv $PROFILE\.voice-typer\venv`
  - Runs pip install from bundled requirements
  - Writes initial `config.json`
  - Creates desktop shortcut (or let the Python backend handle this)
- [ ] Reference the NSIS script in `electron-builder.yml` under `nsis.include`
- [ ] Bundle Python embeddable + pip requirements inside the installer

### 6. Model download UX
- [ ] When user clicks "Download" for a model in Settings UI:
  - Show progress bar (HuggingFace `snapshot_download` supports `callback` for progress)
  - Emit `model_download_progress` push event with bytes downloaded / total
  - Show completion notification
  - On failure: show error with retry button

### 7. CUDA detection & display
- [ ] In Settings UI, show detected device info (e.g. "NVIDIA GeForce RTX 3060 (CUDA 12.2)")
- [ ] If CUDA not available, show "No GPU detected — using CPU (slower)"
- [ ] Let user override device in Settings (CUDA → CPU fallback)

### 8. CUDA runtime probe (`_probe_cuda_runtime`)
- [ ] Already implemented in `transcription.py` — runs a 10ms silent transcription after model load on CUDA
- [ ] Forces early cuBLAS/cuDNN DLL resolution so failures surface at startup, not mid-recording
- [ ] On probe failure: tears down GPU model, reloads on CPU via `_reload_under_lock()`
- [ ] Logs `[CUDA-PROBE]` entries for every step
- [ ] Probe uses `vad_filter=False` + `word_timestamps=False` for minimal kernel touch

### 9. Debug logging for CUDA DLL loading (`_configure_nvidia_dll_paths`)
- [ ] Already implemented — `[CUDA-DLL]` tags on every path check
- [ ] Logs each root path searched, whether directories exist, how many DLLs found
- [ ] Logs each `os.add_dll_directory()` call result (success/failure)
- [ ] Logs final `PATH` entries containing `nvidia` for post-mortem debugging
- [ ] Logs the complete list of new paths added (or confirms none were needed)
