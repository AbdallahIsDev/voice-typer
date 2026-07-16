# Windows Validation Runbook — Phase 0-W (ADR-0020)

**Status**: VALIDATE ON WINDOWS HOST. This runbook documents the 9-point Phase 0-W validation gate that must pass on a real Windows 10/11 host with MSVC build tools before the Tauri cutover.

**Prerequisites**:
- Windows 10 22H2 or Windows 11 (x86_64 or aarch64)
- Visual Studio 2022 Build Tools (C++ workload) — provides `cl.exe` + `link.exe` + Windows SDK
- Python 3.12.x (for running Nuitka + the dev sidecar)
- Rust toolchain (`rustup init` → `stable` + `x86_64-pc-windows-msvc` target)
- Node.js 20+ (for the React renderer build)
- Git

**Time estimate**: 2-4 hours (first run; subsequent runs ~30 min with cached deps).

---

## Step 0 — Environment setup

```powershell
# Install Rust
winget install Rustlang.Rustup
rustup default stable-x86_64-pc-windows-msvc

# Install Node.js
winget install OpenJS.NodeJS.LTS

# Install Visual Studio Build Tools (C++ workload)
winget install Microsoft.VisualStudio.2022.BuildTools --override "--add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"

# Clone + enter the repo
git clone https://github.com/AbdallahIsDev/voice-typer.git
cd voice-typer

# Python venv + deps (use uv, not pip — qwen-asr resolution issues with pip)
pip install uv
uv venv
.venv\Scripts\activate
uv pip install -e ".[dev,test]"

# Node deps
cd voice_typer\client
npm install
cd ..\..

# Native hotkey binaries
bash scripts\build\compile_native.sh
# Or on PowerShell:
# powershell -ExecutionPolicy Bypass -File scripts\build\compile_native.ps1
```

**Expected output**: All commands exit 0. The `compile_native.sh` script produces `voice_typer/server/native/windows-key-listener.exe`.

**Pass criteria**: `where cargo`, `where node`, `where python` all resolve. `voice_typer/server/native/windows-key-listener.exe` exists.

---

## Step 1 — Nuitka Windows `.exe` builds from `python-build-standalone` + MSVC

**VALIDATE ON WINDOWS HOST**

```powershell
# Install Nuitka + python-build-standalone
uv pip install nuitka zstandard

# Download python-build-standalone cpython-3.12.x for x86_64-pc-windows-msvc
# (See ADR-0020 §4.2 for the exact URL + hash)
python -c "
import urllib.request, json, os
url = 'https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest'
# Download the cpython-3.12.x + x86_64-pc-windows-msvc + install_only archive
# Extract to src-tauri/resources/python-build-standalone/
"

# Build the sidecar with Nuitka
python -m nuitka `
    --standalone `
    --onefile `
    --output-dir=src-tauri/bin `
    --output-filename=python-sidecar-x86_64-pc-windows-msvc.exe `
    --include-package=voice_typer.server `
    --include-package=faster_whisper `
    --include-package=enigo `
    --include-package=pystray `
    --include-data-dir=voice_typer/server/native=native `
    --python-flag=no_site `
    --python-flag=no_warnings `
    -m voice_typer.server.ipc_server

# Verify the binary exists
dir src-tauri\bin\python-sidecar-x86_64-pc-windows-msvc.exe
```

**Expected output**: Nuitka compiles for ~10-15 minutes, producing `src-tauri/bin/python-sidecar-x86_64-pc-windows-msvc.exe` (~80-120 MB).

**Pass criteria**: The `.exe` exists and `src-tauri\bin\python-sidecar-x86_64-pc-windows-msvc.exe --help` prints the IPC server help text without errors.

**Common failures**:
- `error: Microsoft Visual C++ 14.0 is required` → Install VS Build Tools C++ workload.
- `ModuleNotFoundError: faster_whisper` → Add `--include-package=faster_whisper` (already in the command above).
- `error: cannot find 'python3.12.dll'` → Ensure python-build-standalone is extracted to the right path.

---

## Step 2 — `externalBin` `python-sidecar` spawns

**VALIDATE ON WINDOWS HOST**

```powershell
# Build the Tauri host (requires display server for WebView2)
cd src-tauri
cargo tauri build

# Verify the built MSI/NSIS installer exists
dir target\release\bundle\
```

**Expected output**: `cargo tauri build` compiles the Rust host (~5 min), bundles the sidecar + resources, and produces an MSI installer at `target/release/bundle/msi/Voice Typer_1.0.0_x64_en-US.msi`.

**Pass criteria**: The MSI exists. Installing it (double-click) creates a Start Menu entry for "Voice Typer". Launching it shows the React UI.

---

## Step 3 — WS + HMAC connect

**VALIDATE ON WINDOWS HOST**

```powershell
# Launch the installed app, then check the sidecar stdout in the Tauri log
# (Tauri redirects sidecar stdout to its own log at %APPDATA%/voice-typer/logs/)

# Verify the server_started JSON was emitted
type "%APPDATA%\voice-typer\logs\voice-typer.log" | findstr "server_started"

# Expected line:
# [SIDECAR] server_started port=12345
```

**Pass criteria**: The log contains `[SIDECAR] server_started port=N`. The Tauri host's WS client connects to `ws://127.0.0.1:N` and sends the auth frame `{"type":"auth","token":"<64-char-hex>"}`. The sidecar accepts the token and the WS connection stays open.

---

## Step 4 — `faster-whisper` transcribes inside the Nuitka bundle

**VALIDATE ON WINDOWS HOST**

```powershell
# In the running app:
# 1. Open Settings → Models
# 2. Download a small model (e.g., "tiny" or "base")
# 3. Open the Home page
# 4. Press the dictation hotkey (default: Ctrl+Alt+V)
# 5. Speak a test phrase ("hello world")
# 6. Release the hotkey

# Verify the transcription appears in the text field
# Verify the transcription appears in History
```

**Pass criteria**: The transcription text appears in the focused text field within 5 seconds of releasing the hotkey. The History page shows the new entry with the correct model name + device name.

**Common failures**:
- `CUDA error: no kernel image` → The Nuitka bundle didn't include the CUDA runtime. Add `--include-package=torch` + the appropriate CUDA libs.
- `Model not found` → The model download path resolves to the wrong directory. Check `%APPDATA%/voice-typer/models/`.

---

## Step 5 — `enigo` paste

**VALIDATE ON WINDOWS HOST**

```powershell
# In the running app:
# 1. Open Notepad
# 2. Press the dictation hotkey
# 3. Speak a short phrase ("test paste")
# 4. Release the hotkey

# Verify the text appears in Notepad
```

**Pass criteria**: The transcribed text appears in Notepad. For short text (<300 chars), `enigo.text()` injects it directly. For long text, the clipboard + Ctrl+V path is used.

---

## Step 6 — Notification toast

**VALIDATE ON WINDOWS HOST**

```powershell
# In the running app:
# 1. Open Settings → General
# 2. Toggle "Notifications" on
# 3. Trigger a notification (e.g., start dictation, then stop)

# Verify a Windows toast notification appears
```

**Pass criteria**: A Windows toast notification appears in the Action Center with the Voice Typer icon + the notification text.

---

## Step 7 — Cooperative shutdown

**VALIDATE ON WINDOWS HOST**

```powershell
# In the running app:
# 1. Close the main window (click X)
# 2. Verify the sidecar exits within 2 seconds

# Check Task Manager for "python-sidecar" — it should be gone
tasklist | findstr python-sidecar
```

**Pass criteria**: `tasklist | findstr python-sidecar` returns nothing within 2 seconds of closing the main window. The Tauri log shows `[SHUTDOWN] sidecar killed`.

---

## Step 8 — Prewarm `LogonTrigger` Task Scheduler

**VALIDATE ON WINDOWS HOST**

```powershell
# In the running app:
# 1. Open Settings → General
# 2. Enable "Prewarm on login"
# 3. Sign out + sign back in

# Verify the prewarm task ran
schtasks /query /tn "VoiceTyperPrewarm" /v
```

**Pass criteria**: `schtasks /query` shows the "VoiceTyperPrewarm" task with `LogonTrigger` and `Last Run Time` matching the sign-in time. The prewarm log at `%APPDATA%/voice-typer/logs/prewarm.log` shows the prewarm ran.

---

## Step 9 — Native `windows-key-listener` toggles dictation

**VALIDATE ON WINDOWS HOST**

```powershell
# In the running app:
# 1. Open Settings → Hotkey
# 2. Set a custom hotkey (e.g., F8)
# 3. Focus a text field (e.g., Notepad)
# 4. Press F8
# 5. Speak a test phrase
# 6. Press F8 again

# Verify the dictation toggles correctly
```

**Pass criteria**: Pressing F8 starts recording (the bubble appears with "Listening…"). Pressing F8 again stops recording and pastes the transcription. The native `windows-key-listener.exe` process is visible in Task Manager while the app is running.

---

## Summary checklist

| # | Check | Pass criteria |
|---|---|---|
| 1 | Nuitka Windows `.exe` builds | `python-sidecar-x86_64-pc-windows-msvc.exe` exists (~80-120 MB) |
| 2 | `externalBin` spawns | Tauri app launches, MSI installer produced |
| 3 | WS + HMAC connect | `[SIDECAR] server_started port=N` in log |
| 4 | `faster-whisper` transcribes | Transcription appears in text field + History |
| 5 | `enigo` paste | Text appears in Notepad |
| 6 | Notification toast | Windows toast appears |
| 7 | Cooperative shutdown | Sidecar exits within 2s of window close |
| 8 | Prewarm `LogonTrigger` | `schtasks /query` shows the task ran on login |
| 9 | Native hotkey toggle | F8 starts/stops dictation |

**All 9 must pass before the Windows Tauri cutover.** Electron remains the fallback until all 9 pass.
