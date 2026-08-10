# Windows Validation Runbook — Phase 0-W (ADR-0020)

**Status**: VALIDATE ON WINDOWS HOST ONLY. This runbook documents the
9-point Phase 0-W validation gate that **must pass on a real Windows 10
22H2 or Windows 11 host** before the Tauri cutover on Windows. The Linux
sandbox this runbook was authored in CANNOT run `cargo tauri build`,
Nuitka Windows `.exe` builds, or any of the validation steps — every
step labeled `**VALIDATE ON WINDOWS HOST**` must be executed by a human
on a real Windows host, and the observed result recorded in the results
table at §9.

**Authoritative spec**:
- `docs/adr/0020-desktop-runtime-migration-analysis.md` — §4 (Nuitka
  build), §5 (prewarm), §6.2 (paste focus-restore), §6.4 (native
  hotkey), §13.1 (Windows Authenticode signing), §Phase 0-W gate.
- `docs/migration/signing-guide.md` — Authenticode signtool commands.
- `docs/migration/tauri-sidecar-bridge.md` — WS + bearer-token protocol.

**Related scripts**:
- `scripts/build/compile_native.ps1` — builds `windows-key-listener.exe`.
- `scripts/build/voice-typer.spec` — PyInstaller fallback spec (ADR §4.5).
- `scripts/build/smoke_test_windows.ps1` — native-listener smoke test.
- `.github/workflows/tauri-windows-build.yml` — CI workflow ENABLED
  (active x86_64 matrix leg runs via `workflow_dispatch` /
  `workflow_call` for Phase 0-W validation; push/PR triggers stay
  commented out until §6 passes on a host).

**Time estimate**: 2–4 hours first run; subsequent runs ~30 min with
cached deps.

---

## Quick Reference (copy-paste in order)

These are the exact commands a Windows user should run, in order, to
execute the entire Phase 0-W gate. Each numbered step has a full
explanation in the cited section.

```powershell
# ─── §0 Prerequisites (one-time setup) ──────────────────────────────────
1.  winget install --id Rustlang.Rustup -e
2.  winget install --id OpenJS.NodeJS.LTS -e
3.  winget install --id Microsoft.VisualStudio.2022.BuildTools `
        --override "--add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
4.  rustup default stable-x86_64-pc-windows-msvc
5.  git clone https://github.com/AbdallahIsDev/voice-typer.git ; cd voice-typer
6.  pip install uv ; uv venv ; .venv\Scripts\activate
7.  uv pip install -e ".[dev,test]" nuitka==2.5.4 zstandard ordered-set
8.  cd voice_typer\client ; npm install ; cd ..\..

# ─── §0.7 Download python-build-standalone (Nuitka target interpreter) ──
9.  # See §0.7 — download cpython-3.12.8+20241219-x86_64-pc-windows-msvc
    # and extract to C:\tools\pybs\python

# ─── §3 Build native windows-key-listener.exe ───────────────────────────
10. powershell -ExecutionPolicy Bypass -File scripts\build\compile_native.ps1

# ─── §1 Build sidecar .exe with Nuitka ──────────────────────────────────
11. # See §1 — the exact `python -m nuitka ...` command (≈ 15 min build).

# ─── §2 Build prewarm .exe with Nuitka ──────────────────────────────────
12. # See §2 — the exact `python -m nuitka ...` command.

# ─── §4 Build Tauri app (MSI + NSIS) ────────────────────────────────────
13. cd voice_typer\client ; npm run build:renderer ; cd ..\..
14. cd src-tauri ; cargo tauri build --target x86_64-pc-windows-msvc ; cd ..

# ─── §5 Install + smoke test ────────────────────────────────────────────
15. # Install target\x86_64-pc-windows-msvc\release\bundle\nsis\*-setup.exe
16. # Launch "Voice Typer" from the Start Menu.

# ─── §6 Execute the 9-point gate (record each result in §9) ─────────────
17. §6.1 Sidecar spawn via externalBin        — log shows server_started
18. §6.2 WS + bearer-token handshake           — auth accepted in log
19. §6.3 faster-whisper transcribe            — text appears in UI + History
20. §6.4 enigo paste (short + long)           — text in Notepad
21. §6.5 Toast notification                   — Windows toast appears
22. §6.6 Cooperative shutdown                 — sidecar exits ≤ 2 s
23. §6.7 Prewarm LogonTrigger                 — schtasks shows Last Run
24. §6.8 Native windows-key-listener          — F8 toggles dictation
25. §6.9 Single-instance                      — second launch focuses first

# ─── §10 New Rust commands (Sub-agent A additions) ──────────────────────
26. §10.1 Export History                      — file written
27. §10.2 Export Vocabulary                   — file written
28. §10.3 Bubble window                       — bubble appears + hides

# ─── §7 Code signing (optional — skip if no cert) ───────────────────────
29. # See §7 — signtool sign + verify sidecar + prewarm + MSI + NSIS.

# ─── §9 Capture results ─────────────────────────────────────────────────
30. # Fill the §9 results table; attach %APPDATA%\voice-typer\logs\*.
```

---

## §0 Prerequisites

### §0.1 Operating system

**VALIDATE ON WINDOWS HOST** The host must run **Windows 10 22H2 (build
19045) or Windows 11 22H2+ (build 22621+)**, x86_64 architecture. A
Windows-on-ARM host (aarch64) is a separate target triple
(`aarch64-pc-windows-msvc`) and is **out of scope for the v1 Phase 0-W
gate** — see ADR-0020 §4.1.

Verify the OS:

```powershell
**VALIDATE ON WINDOWS HOST** [System.Environment]::OSVersion.Version
# Expected: 10.0.19045+ (Win10 22H2) or 10.0.22621+ (Win11 22H2+).
```

### §0.2 Python 3.12 (64-bit, dev environment)

**VALIDATE ON WINDOWS HOST** Install **Python 3.12.x 64-bit** for the
dev environment (used to run `uv`/`Nuitka` and the dev sidecar). The
frozen binary uses a separate interpreter (§0.7).

```powershell
**VALIDATE ON WINDOWS HOST** winget install --id Python.Python.3.12 -e
**VALIDATE ON WINDOWS HOST** python --version
# Expected: Python 3.12.x
**VALIDATE ON WINDOWS HOST** python -c "import struct; print(struct.calcsize('P') * 8)"
# Expected: 64  (must be 64-bit, not 32-bit)
```

### §0.3 MSVC Build Tools 2022 (C++ workload)

**VALIDATE ON WINDOWS HOST** Visual Studio 2022 Build Tools with the
"Desktop development with C++" workload — provides `cl.exe`, `link.exe`,
the Windows SDK, and the MSVC runtime. Required by both Nuitka (which
invokes `cl.exe` to compile C) and the native
`windows-key-listener.exe` build (§3).

```powershell
**VALIDATE ON WINDOWS HOST** winget install --id Microsoft.VisualStudio.2022.BuildTools `
    --override "--add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
```

Verify `cl.exe` is on PATH (open a fresh "Developer PowerShell for VS
2022" window OR run `vcvars64.bat`):

```powershell
**VALIDATE ON WINDOWS HOST** where cl.exe
# Expected: C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Tools\MSVC\<ver>\bin\Hostx64\x64\cl.exe
**VALIDATE ON WINDOWS HOST** cl 2>&1 | Select-Object -First 1
# Expected: Microsoft (R) C/C++ Optimizing Compiler Version ... for x64
```

If `cl.exe` is missing from PATH, launch the build from a "Developer
PowerShell for VS 2022" prompt (Start Menu → "Developer PowerShell for
VS 2022"), which auto-loads the MSVC env. See §11 Known Issues for the
common PATH failure.

### §0.4 Rust stable-x86_64-pc-windows-msvc

**VALIDATE ON WINDOWS HOST** Install Rust via `rustup` and pin the
default toolchain to `stable-x86_64-pc-windows-msvc` (NOT the
`gnu` triple — Tauri on Windows requires the MSVC ABI to match the
WebView2 + native listener ABI).

```powershell
**VALIDATE ON WINDOWS HOST** winget install --id Rustlang.Rustup -e
**VALIDATE ON WINDOWS HOST** rustup default stable-x86_64-pc-windows-msvc
**VALIDATE ON WINDOWS HOST** rustc --version --verbose
# Expected: rustc 1.xx.x (<commit>) ; host: x86_64-pc-windows-msvc
**VALIDATE ON WINDOWS HOST** cargo --version
# Expected: cargo 1.xx.x
```

### §0.5 Node.js 20 LTS

**VALIDATE ON WINDOWS HOST** Node.js 20 LTS for the React renderer
build. The Tauri `beforeBuildCommand` invokes `npm run build:renderer`.

```powershell
**VALIDATE ON WINDOWS HOST** winget install --id OpenJS.NodeJS.LTS -e
**VALIDATE ON WINDOWS HOST** node --version
# Expected: v20.x.x
**VALIDATE ON WINDOWS HOST** npm --version
# Expected: 10.x.x
```

### §0.6 WebView2 Runtime

**VALIDATE ON WINDOWS HOST** WebView2 Runtime (system-installed on
Windows 11 and on Windows 10 22H2 via Edge updates). Tauri v2 uses
WebView2 as the system WebView on Windows.

```powershell
**VALIDATE ON WINDOWS HOST** Get-ItemProperty `
    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" `
    -ErrorAction SilentlyContinue | Select-Object pv
# Expected: a version string like 120.0.2210.91 or higher.
# If empty: install from https://go.microsoft.com/fwlink/p/?LinkId=2124703
```

### §0.7 python-build-standalone cpython-3.12.x (Nuitka target interpreter)

**VALIDATE ON WINDOWS HOST** Nuitka freezes Python against a specific
base interpreter. ADR-0020 §4.2 mandates **python-build-standalone
cpython-3.12.x** (NOT 3.13+ — `faster-whisper`/`ctranslate2` wheels are
built against 3.12) for the `x86_64-pc-windows-msvc` target.

Pinned values (match `.github/workflows/tauri-windows-build.yml`):

- **Release date**: `20241219`
- **CPython version**: `3.12.8`
- **Target triple**: `x86_64-pc-windows-msvc`
- **Archive**: `cpython-3.12.8+20241219-x86_64-pc-windows-msvc-install_only.tar.gz`
- **Download URL**:
  `https://github.com/astral-sh/python-build-standalone/releases/download/20241219/cpython-3.12.8+20241219-x86_64-pc-windows-msvc-install_only.tar.gz`
- **SHA-256**: fetched at download time from
  `https://github.com/astral-sh/python-build-standalone/releases/download/20241219/SHA256SUMS.txt`
  (the official checksum file — verify the archive hash against this
  file before extraction; do NOT trust a hardcoded hash in this
  runbook, since the release may be re-rolled).

Download + verify + extract (PowerShell):

```powershell
**VALIDATE ON WINDOWS HOST** $date   = "20241219"
**VALIDATE ON WINDOWS HOST** $pyver  = "3.12.8"
**VALIDATE ON WINDOWS HOST** $triple = "x86_64-pc-windows-msvc"
**VALIDATE ON WINDOWS HOST** $archive = "cpython-$pyver+$date-$triple-install_only.tar.gz"
**VALIDATE ON WINDOWS HOST** $url = "https://github.com/astral-sh/python-build-standalone/releases/download/$date/$archive"
**VALIDATE ON WINDOWS HOST** Invoke-WebRequest -Uri $url -OutFile $archive
**VALIDATE ON WINDOWS HOST** Invoke-WebRequest -Uri `
    "https://github.com/astral-sh/python-build-standalone/releases/download/$date/SHA256SUMS.txt" `
    -OutFile SHA256SUMS.txt
**VALIDATE ON WINDOWS HOST** $expected = (Select-String -Path SHA256SUMS.txt -Pattern $archive).Line.Split(' ')[0]
**VALIDATE ON WINDOWS HOST** $actual   = (Get-FileHash -Algorithm SHA256 -Path $archive).Hash.ToLower()
**VALIDATE ON WINDOWS HOST** if ($expected -ne $actual) {
        Write-Error "SHA-256 mismatch: expected=$expected actual=$actual"; exit 1
    }
**VALIDATE ON WINDOWS HOST** New-Item -ItemType Directory -Force -Path C:\tools\pybs | Out-Null
**VALIDATE ON WINDOWS HOST** tar -xzf $archive -C C:\tools\pybs
**VALIDATE ON WINDOWS HOST** C:\tools\pybs\python\python.exe --version
# Expected: Python 3.12.8
```

Then install the project deps into the PYBS interpreter (this is what
Nuitka will actually freeze):

```powershell
**VALIDATE ON WINDOWS HOST** C:\tools\pybs\python\python.exe -m pip install --upgrade pip
**VALIDATE ON WINDOWS HOST** C:\tools\pybs\python\python.exe -m pip install nuitka==2.5.4 zstandard ordered-set
**VALIDATE ON WINDOWS HOST** C:\tools\pybs\python\python.exe -m pip install -e ".[dev,test]"
**VALIDATE ON WINDOWS HOST** C:\tools\pybs\python\python.exe -c "import faster_whisper, ctranslate2; print('ctranslate2', ctranslate2.__version__)"
# Expected: ctranslate2 <version>  (proves faster-whisper + ctranslate2 wheels installed)
```

### §0.8 Nuitka + supporting deps

**VALIDATE ON WINDOWS HOST** Nuitka 2.5.4 (pinned to match the CI
workflow) + `zstandard` (Nuitka's onefile compression backend) +
`ordered-set` (Nuitka dependency).

Already installed in §0.7 — verify:

```powershell
**VALIDATE ON WINDOWS HOST** C:\tools\pybs\python\python.exe -m nuitka --version
# Expected: 2.5.4
```

### §0.9 Final prerequisite verification

```powershell
**VALIDATE ON WINDOWS HOST** where cargo ; where node ; where python ; where cl
**VALIDATE ON WINDOWS HOST** C:\tools\pybs\python\python.exe --version
**VALIDATE ON WINDOWS HOST** cargo tauri --version
# Expected: tauri-cli 1.5.x or higher (install via `cargo install tauri-cli --version "^1.5"`)
```

If `cargo tauri` is missing:

```powershell
**VALIDATE ON WINDOWS HOST** cargo install tauri-cli --version "^1.5" --locked
```

---

## §1 Build sidecar `.exe` with Nuitka (ADR-0020 §4.2)

**VALIDATE ON WINDOWS HOST** This step compiles the Python backend into
a single-file `python-sidecar-x86_64-pc-windows-msvc.exe` (~80–120 MB)
via Nuitka, using the python-build-standalone interpreter as the base.
The build takes ~10–15 minutes; a clean rebuild ~25 minutes.

The flags below are the **authoritative** Nuitka command from ADR-0020
§4.2 — do NOT alter them without updating the ADR. In particular:

- `--standalone --onefile` — single-file output required by Tauri's
  `externalBin` mechanism (which expects one binary per target triple,
  not a folder).
- `--windows-disable-console` — the sidecar runs headless; no console
  window pops up on launch.
- `--include-package=faster_whisper` + `--include-package=ctranslate2`
  — required for CTranslate2 + Whisper ASR.
- `--include-package=voice_typer` — the project package.
- `--include-package=websockets` — required; the sidecar is a WS
  *server* (ADR-0020 §14) and the stdlib has no WS implementation.
- `--enable-plugin=numpy` — pulls numpy's hidden imports.
- `--include-data-dir=$SITE\ctranslate2\lib=...` — copies the CTranslate2
  native runtime DLLs (`ctranslate2.dll` + `libiomp5md.dll` + MKL
  redistributables) into the bundle. **Without `libiomp5md.dll` the
  frozen exe builds fine but crashes instantly on
  `import ctranslate2`** — see §11 Known Issues.
- `--include-dll=$SITE\ctranslate2\lib\ctranslate2.dll` — explicit
  include of the main CTranslate2 DLL (Nuitka does NOT expand `*.dll`
  globs).
- `--onefile-tempdir-spec=%LOCALAPPDATA%\voice-typer\onefile-tmp` —
  pins a deterministic extract dir to prevent tempdir bloat (ADR-0020
  §4.2 + §11 Known Issues).
- `--output-filename=python-sidecar-x86_64-pc-windows-msvc.exe` — the
  filename MUST end with the target triple + `.exe` for Tauri's
  `externalBin` to select it at runtime.
- Entry point: `voice_typer/server/ipc_server.py` (the same entrypoint
  used by the Electron path + the dev sidecar — only the freeze tool
  changes).

```powershell
**VALIDATE ON WINDOWS HOST** $PYBS = "C:\tools\pybs\python"
**VALIDATE ON WINDOWS HOST** $SITE = & $PYBS\python.exe -c "import site; print(site.getsitepackages()[0])"
**VALIDATE ON WINDOWS HOST** Write-Host "PYBS=$PYBS  SITE=$SITE"

# Sanity-check the ctranslate2/lib directory exists and lists the
# expected DLL set (libiomp5md.dll MUST be present):
**VALIDATE ON WINDOWS HOST** Get-ChildItem "$SITE\ctranslate2\lib\*.dll" | Select-Object Name
# Expected: ctranslate2.dll, libiomp5md.dll, possibly mkl_*.dll / libgomp*.dll

# Build the sidecar (≈ 10–15 min):
**VALIDATE ON WINDOWS HOST** & $PYBS\python.exe -m nuitka `
    --standalone --onefile `
    --assume-yes-for-downloads `
    --enable-plugin=numpy `
    --include-package=faster_whisper `
    --include-package=ctranslate2 `
    --include-package=voice_typer `
    --include-package=websockets `
    --include-data-dir="$SITE\ctranslate2\lib=$SITE\ctranslate2\lib" `
    --include-dll="$SITE\ctranslate2\lib\ctranslate2.dll" `
    --windows-disable-console `
    --onefile-tempdir-spec="%LOCALAPPDATA%\voice-typer\onefile-tmp" `
    --output-filename=python-sidecar-x86_64-pc-windows-msvc.exe `
    --output-dir=src-tauri\bin `
    voice_typer\server\ipc_server.py

# Verify the binary exists + its size:
**VALIDATE ON WINDOWS HOST** Test-Path .\src-tauri\bin\python-sidecar-x86_64-pc-windows-msvc.exe
# Expected: True
**VALIDATE ON WINDOWS HOST** $sz = (Get-Item .\src-tauri\bin\python-sidecar-x86_64-pc-windows-msvc.exe).Length / 1MB
**VALIDATE ON WINDOWS HOST** Write-Host "Sidecar size: $([math]::Round($sz,1)) MB"
# Expected: ~80–120 MB
```

### §1.1 Sidecar standalone smoke test (Phase 0 sub-check)

**VALIDATE ON WINDOWS HOST** Per ADR-0020 §4.5 ("Verify step (Phase 0
gate per platform)"): run the sidecar binary standalone with a one-shot
command that loads `faster_whisper`, transcribes a 3-second WAV, prints
the text, exits 0. This proves CTranslate2 + DLLs + model load all work
inside Nuitka — a Windows success does NOT imply macOS success (or vice
versa).

The bundled sidecar expects `--ws` from Tauri; for the standalone
smoke test we override the entrypoint. Easiest path: create a tiny
`scripts/build/smoke_sidecar.py` that imports `faster_whisper` +
transcribes a WAV, then run it via the frozen interpreter. If you don't
have a WAV handy, you can synthesize one with `soundfile`:

```powershell
**VALIDATE ON WINDOWS HOST** # Synthesize a 3-second 16 kHz sine "tone" WAV (NOT real speech — just
# proves the import chain + Whisper model load + transcribe call shape).
**VALIDATE ON WINDOWS HOST** $smoke = @"
import numpy as np, soundfile as sf, tempfile, os
from faster_whisper import WhisperModel
sr = 16000
t = np.linspace(0, 3, sr*3, endpoint=False)
tone = (0.3 * np.sin(2*np.pi*440*t)).astype(np.float32)
wav = os.path.join(tempfile.gettempdir(), 'vt_smoke.wav')
sf.write(wav, tone, sr)
m = WhisperModel('tiny', device='cpu', compute_type='int8')
segs, _ = m.transcribe(wav)
print('SEGMENTS:', [s.text for s in segs])
print('SMOKE-OK')
"@
**VALIDATE ON WINDOWS HOST** $smoke | Out-File -Encoding utf8 scripts\build\smoke_sidecar.py
**VALIDATE ON WINDOWS HOST** & $PYBS\python.exe -m nuitka --standalone --onefile `
    --include-package=faster_whisper --include-package=ctranslate2 `
    --include-package=numpy --include-package=soundfile `
    --include-data-dir="$SITE\ctranslate2\lib=$SITE\ctranslate2\lib" `
    --output-filename=smoke_sidecar.exe --output-dir=scripts\build `
    scripts\build\smoke_sidecar.py
**VALIDATE ON WINDOWS HOST** .\scripts\build\smoke_sidecar.exe
# Expected: prints "SEGMENTS: [...]" then "SMOKE-OK", exit 0.
# A crash here means CTranslate2 DLLs (libiomp5md.dll / MKL) are
# missing from the bundle — re-check the --include-data-dir flag.
```

**Pass criteria for §1**:
- `src-tauri\bin\python-sidecar-x86_64-pc-windows-msvc.exe` exists.
- Size is 80–120 MB.
- `smoke_sidecar.exe` prints `SMOKE-OK` and exits 0.

**Fail scenarios + remediation**:
- `error: Microsoft Visual C++ 14.0 is required` → §0.3 not satisfied;
  reinstall VS Build Tools C++ workload, open a fresh Developer
  PowerShell.
- `ModuleNotFoundError: faster_whisper` → `--include-package` flag
  missing or typo'd.
- `ImportError: libiomp5md.dll not found` (at smoke-test runtime) →
  the `--include-data-dir=$SITE\ctranslate2\lib=...` flag did not
  capture `libiomp5md.dll`; enumerate the lib dir and add an explicit
  `--include-dll` for each missing DLL.
- `error: cannot find 'python3.12.dll'` → the PYBS path is wrong; rerun
  §0.7.
- `--onefile` build crashes mid-link with `LNK1104: cannot open
  'python3.lib'` → the PYBS `libs/python3.12.lib` is missing; reinstall
  PYBS with the `+install_only` archive (not `+shared`).

---

## §2 Build prewarm `.exe` with Nuitka (ADR-0020 §5)

**VALIDATE ON WINDOWS HOST** Prewarm is frozen the same Nuitka way into
`prewarm-x86_64-pc-windows-msvc.exe` (kept as a separate binary per
ADR-0020 Rule 1 — prewarm is NOT merged into the sidecar). One prewarm
binary per target triple.

The prewarm binary is a `bundle.resource` (NOT `externalBin`) because
it's launched by Windows Task Scheduler — not by Tauri. Tauri extracts
it to `resourceDir/prewarm-x86_64-pc-windows-msvc.exe`; the sidecar
resolves it via `resolve_prewarm_exe()` (see ADR-0020 §5).

```powershell
**VALIDATE ON WINDOWS HOST** $PYBS = "C:\tools\pybs\python"
**VALIDATE ON WINDOWS HOST** $SITE = & $PYBS\python.exe -c "import site; print(site.getsitepackages()[0])"
**VALIDATE ON WINDOWS HOST** & $PYBS\python.exe -m nuitka `
    --standalone --onefile `
    --assume-yes-for-downloads `
    --enable-plugin=numpy `
    --include-package=faster_whisper `
    --include-package=ctranslate2 `
    --include-package=voice_typer `
    --include-package=websockets `
    --include-data-dir="$SITE\ctranslate2\lib=$SITE\ctranslate2\lib" `
    --include-dll="$SITE\ctranslate2\lib\ctranslate2.dll" `
    --windows-disable-console `
    --onefile-tempdir-spec="%LOCALAPPDATA%\voice-typer\prewarm-onefile-tmp" `
    --output-filename=prewarm-x86_64-pc-windows-msvc.exe `
    --output-dir=src-tauri\resources `
    voice_typer\server\prewarm\__main__.py

**VALIDATE ON WINDOWS HOST** Test-Path .\src-tauri\resources\prewarm-x86_64-pc-windows-msvc.exe
# Expected: True
```

**Pass criteria for §2**:
- `src-tauri\resources\prewarm-x86_64-pc-windows-msvc.exe` exists.
- Size is 60–100 MB (smaller than the sidecar because it doesn't
  include the WS server stack, though it does include CTranslate2 for
  cache warming).

---

## §3 Build native `windows-key-listener.exe` (ADR-0020 §6.4)

**VALIDATE ON WINDOWS HOST** The native key-listener binary is built by
`scripts/build/compile_native.ps1` (a PowerShell port of the bash
`compile_native.sh`). It uses `cl.exe` (MSVC) if available, else falls
back to MinGW gcc. Output:
`voice_typer/server/native/windows-key-listener.exe`.

ADR-0020 §6.4 mandates: **keep the native hotkey binaries, spawned by
the Python sidecar** (NOT by Tauri). The Tauri global-shortcut plugin
cannot replace them without regressing key suppression, modifier-only
hotkeys, and Fn/Globe-key support.

```powershell
**VALIDATE ON WINDOWS HOST** powershell -ExecutionPolicy Bypass -File scripts\build\compile_native.ps1
# Expected output:
#   [compile_native] Project root: C:\...\voice-typer
#   [compile_native] Native dir:   C:\...\voice-typer\voice_typer\server\native
#   [compile_native] Compiling with MSVC: cl.exe /O2 ...
#   [compile_native] OK: ...\windows-key-listener.exe
#   [compile_native] Done.

**VALIDATE ON WINDOWS HOST** Test-Path .\voice_typer\server\native\windows-key-listener.exe
# Expected: True
```

### §3.1 Native-listener standalone smoke test

**VALIDATE ON WINDOWS HOST** Run the `smoke_test_windows.ps1` helper to
prove the WH_KEYBOARD_LL hook installs + fires on synthetic input:

```powershell
**VALIDATE ON WINDOWS HOST** .\scripts\build\smoke_test_windows.ps1 `
    .\voice_typer\server\native\windows-key-listener.exe '<caps_lock>'
# Expected: "Binary emitted READY" then "Smoke test PASSED"
# (hook callback fired: KEY_DOWN:CapsLock received)
```

If the smoke test fails with "READY received but hook did not fire" the
CI desktop session may not be forwarding synthetic input — set
`VOICE_TYPER_ALLOW_SKIP_HOOK_FIRE=1` to downgrade to a warning
(READY alone still proves the hook installed):

```powershell
**VALIDATE ON WINDOWS HOST** $env:VOICE_TYPER_ALLOW_SKIP_HOOK_FIRE = "1"
**VALIDATE ON WINDOWS HOST** .\scripts\build\smoke_test_windows.ps1 `
    .\voice_typer\server\native\windows-key-listener.exe '<caps_lock>'
```

### §3.2 Copy native binary to Tauri resources

**VALIDATE ON WINDOWS HOST** Tauri's `bundle.resources` includes
`resources/native/windows-key-listener.exe`. Copy the built binary
there so `cargo tauri build` bundles it:

```powershell
**VALIDATE ON WINDOWS HOST** New-Item -ItemType Directory -Force -Path src-tauri\resources\native | Out-Null
**VALIDATE ON WINDOWS HOST** Copy-Item voice_typer\server\native\windows-key-listener.exe `
    src-tauri\resources\native\windows-key-listener.exe -Force
**VALIDATE ON WINDOWS HOST** Test-Path .\src-tauri\resources\native\windows-key-listener.exe
# Expected: True
```

**Pass criteria for §3**:
- `voice_typer\server\native\windows-key-listener.exe` exists.
- `src-tauri\resources\native\windows-key-listener.exe` exists (copy).
- `smoke_test_windows.ps1` reports "PASSED" (or "PASSED with caveat"
  if `VOICE_TYPER_ALLOW_SKIP_HOOK_FIRE=1`).

---

## §4 Build Tauri app (`cargo tauri build`)

**VALIDATE ON WINDOWS HOST** Build the React renderer + the Rust host +
bundle the sidecar + prewarm + native listener + produce the MSI + NSIS
installers.

```powershell
**VALIDATE ON WINDOWS HOST** cd voice_typer\client
**VALIDATE ON WINDOWS HOST** npm install
**VALIDATE ON WINDOWS HOST** npm run build:renderer
**VALIDATE ON WINDOWS HOST** cd ..\..

**VALIDATE ON WINDOWS HOST** cd src-tauri
**VALIDATE ON WINDOWS HOST** cargo tauri build --target x86_64-pc-windows-msvc
**VALIDATE ON WINDOWS HOST** cd ..
```

The `--target x86_64-pc-windows-msvc` flag is mandatory — it forces
the MSVC ABI target (matching the sidecar + native listener) and
produces bundles under
`src-tauri/target/x86_64-pc-windows-msvc/release/bundle/`.

### §4.1 Verify the installers exist

```powershell
**VALIDATE ON WINDOWS HOST** Get-ChildItem "src-tauri\target\x86_64-pc-windows-msvc\release\bundle\nsis\*-setup.exe" -ErrorAction SilentlyContinue | Select-Object FullName
**VALIDATE ON WINDOWS HOST** Get-ChildItem "src-tauri\target\x86_64-pc-windows-msvc\release\bundle\msi\*.msi" -ErrorAction SilentlyContinue | Select-Object FullName
```

Expected (filenames may differ slightly by Tauri version):

- `src-tauri\target\x86_64-pc-windows-msvc\release\bundle\nsis\Voice Typer_1.0.0_x64-setup.exe`
- `src-tauri\target\x86_64-pc-windows-msvc\release\bundle\msi\Voice Typer_1.0.0_x64_en-US.msi`

**Pass criteria for §4**:
- `cargo tauri build` exits 0 with no errors.
- NSIS setup .exe + MSI .msi both exist.
- No "sidecar binary not found" warnings in the build output (would
  indicate the `externalBin` resolution failed — re-check §1).

---

## §5 Install + smoke test

**VALIDATE ON WINDOWS HOST** Install the NSIS setup .exe (preferred for
testing — faster than MSI and supports in-place upgrades):

```powershell
**VALIDATE ON WINDOWS HOST** Start-Process "src-tauri\target\x86_64-pc-windows-msvc\release\bundle\nsis\Voice Typer_1.0.0_x64-setup.exe" -Wait
```

Or double-click the .exe in File Explorer. The installer creates:

- Start Menu entry: "Voice Typer"
- Install dir: `%LOCALAPPDATA%\Programs\Voice Typer\` (per-user, no
  admin required — `voice-typer.manifest` sets
  `requestedExecutionLevel=asInvoker`).
- Config dir: `%APPDATA%\voice-typer\` (config.json, models/, logs/,
  history.db).

### §5.1 Launch + first-run smoke

**VALIDATE ON WINDOWS HOST** Launch from the Start Menu. Expected:

- The Voice Typer main window opens (WebView2) within ~5 s.
- The tray icon appears in the system tray.
- No Windows Defender SmartScreen warning (if unsigned — see §7 to
  suppress; if signed, no warning expected).
- The Python sidecar process appears in Task Manager:
  `python-sidecar-x86_64-pc-windows-msvc.exe`.

```powershell
**VALIDATE ON WINDOWS HOST** tasklist | findstr /I "python-sidecar voice-typer windows-key-listener"
# Expected: 3 processes (voice-typer main + python-sidecar + windows-key-listener)
```

### §5.2 Verify the sidecar log path

```powershell
**VALIDATE ON WINDOWS HOST** Test-Path "$env:APPDATA\voice-typer\logs"
# Expected: True
**VALIDATE ON WINDOWS HOST** Get-ChildItem "$env:APPDATA\voice-typer\logs"
# Expected: voice-typer.log, sidecar.log (rotating, 5 MB × 5 per ADR-0020 §11)
```

---

## §6 9-point validation gate (Phase 0-W)

Each gate item below has:
- **What it tests** (cited to ADR-0020).
- **Exact command** to run.
- **Expected output** (literal text where possible).
- **Pass criteria** (unambiguous boolean).

Record each result in the §9 results table. **All 9 must pass** before
Windows Tauri cutover (ADR-0020 Phase 5-W). Electron remains the
shippable fallback until all 9 pass.

### §6.1 Sidecar spawn via `externalBin`

**VALIDATE ON WINDOWS HOST**

**Tests**: ADR-0020 §1 — Tauri's `externalBin` mechanism selects the
sidecar binary by target triple at runtime and spawns it with the
`TAURI_SIDECAR=1`, `VOICE_TYPER_IPC_TOKEN=<64-hex>`,
`VOICE_TYPER_NATIVE_DIR=<resourceDir>\native`,
`VOICE_TYPER_PREWARM_EXE=<resourceDir>\prewarm-<triple>.exe` env vars
set. The sidecar binds `127.0.0.1:0`, the OS assigns an ephemeral
port, and the sidecar writes ONE structured line to stdout:
`{"event":"server_started","port":<n>}` (see `voice_typer/server/sidecar_ws.py::_emit_server_started`).

**Command** (within 30 s of launching the app):

```powershell
**VALIDATE ON WINDOWS HOST** Get-Content "$env:APPDATA\voice-typer\logs\voice-typer.log" -Tail 100 |
    Select-String "server_started"
```

**Expected**: a line containing `[SIDECAR] server_started port=<N>`
where `<N>` is an ephemeral port (e.g., 51234).

**Pass criteria**:
- The log contains `server_started port=N` within 30 s of app launch.
- `tasklist | findstr python-sidecar` shows the sidecar process.
- No "sidecar did not emit server_started within Nms" error in the log.

**Fail scenarios**:
- `failed to resolve sidecar binary` → §1 not complete, or the binary
  filename does not end with `-x86_64-pc-windows-msvc.exe`.
- `sidecar did not emit server_started within 30000ms` → sidecar
  crashed on import (CTranslate2 DLL missing, see §11) or stdout was
  block-buffered (should not happen — `sidecar_ws._force_line_buffered_stdout`
  forces line buffering; verify the `--ws` arg was passed).
- `sidecar terminated before server_started (code=Some(N))` → check
  `sidecar.log` for the Python traceback.

### §6.2 WS + bearer-token handshake

**VALIDATE ON WINDOWS HOST**

**Tests**: ADR-0020 §3 — the Rust host opens a WS client to
`ws://127.0.0.1:<N>` and sends the auth frame
`{"type":"auth","token":"<64-hex>"}`. The sidecar validates the token
with `hmac.compare_digest` against the `VOICE_TYPER_IPC_TOKEN` env var
and accepts/rejects the connection. Wrong token → sidecar closes the
socket with code 1008; correct token → sidecar emits a `ready` event
over the WS.

(Note: despite the ADR's "HMAC" wording, the implementation uses a
256-bit bearer token via `secrets.token_bytes(32)` — see `main.rs`
header comment. The wire format is identical; only the comparison
differs.)

**Command**:

```powershell
**VALIDATE ON WINDOWS HOST** Get-Content "$env:APPDATA\voice-typer\logs\voice-typer.log" -Tail 200 |
    Select-String "auth accepted|auth|reject|ready"
**VALIDATE ON WINDOWS HOST** Get-Content "$env:APPDATA\voice-typer\logs\sidecar.log" -Tail 200 |
    Select-String "SIDECAR-WS"
```

**Expected** (in `sidecar.log`):

```
[SIDECAR-WS] listening on 127.0.0.1:<N>
[SIDECAR-WS] client connected from ('127.0.0.1', <M>)
[SIDECAR-WS] auth accepted
[SIDECAR-WS] first authenticated connection — emitting `ready` event
```

**Pass criteria**:
- `auth accepted` appears in `sidecar.log` within 5 s of `server_started`.
- The WS connection stays open (no `connection ended` immediately
  after `auth accepted`).
- The main window's React UI hydrates (e.g., the Settings page loads).

**Fail scenarios**:
- `auth token mismatch — rejecting` → the token in the Rust-side
  `VOICE_TYPER_IPC_TOKEN` env var doesn't match what was passed to the
  sidecar (should not happen — both are set from the same
  `generate_token()` call in `main.rs`).
- `VOICE_TYPER_IPC_TOKEN not set` → the env var wasn't propagated to
  the sidecar process. Check `tauri-plugin-shell` config in
  `tauri.conf.json` (`plugins.shell.scope`).
- `auth frame timeout` → the Rust host didn't send the auth frame
  within 5 s of connecting (see `_AUTH_TIMEOUT_SECONDS` in
  `sidecar_ws.py`). Inspect the Rust-side WS connect logic.

### §6.3 `faster-whisper` transcribe inside the Nuitka bundle

**VALIDATE ON WINDOWS HOST**

**Tests**: ADR-0020 Phase 0-W gate item 4 — proves CTranslate2 + DLLs +
model load + transcribe all work inside the frozen sidecar exe.

**Command** (in the running app):

1. Open **Settings → Models**.
2. Download a small model: select "tiny" (39 MB) or "base" (74 MB) and
   click Download. Wait for the download to complete (watch the
   progress bar; the model is saved to `%APPDATA%\voice-typer\models\`).
3. Open the **Home** page.
4. Press the dictation hotkey (default: `Ctrl+Alt+V`).
5. Speak a test phrase, e.g., "hello world".
6. Release the hotkey.

**Expected**:
- The transcription text appears in the focused text field within ~5 s
  of releasing the hotkey.
- The transcription appears in the **History** page with the correct
  model name + device name.

**Verify in logs**:

```powershell
**VALIDATE ON WINDOWS HOST** Get-Content "$env:APPDATA\voice-typer\logs\sidecar.log" -Tail 200 |
    Select-String "transcrib|whisper|model_load|ctranslate2"
```

**Pass criteria**:
- The transcription text appears in the focused text field within 5 s.
- The History page shows the new entry with the correct model name.
- No `CUDA error` / `model not found` / `ctranslate2` errors in
  `sidecar.log`.

**Fail scenarios**:
- `CUDA error: no kernel image` → the Nuitka bundle didn't include the
  CUDA runtime. For CPU-only inference (the v1 default), ensure
  `compute_type='int8'` in the ASR config; CUDA wheels are large and
  not bundled by default.
- `Model not found` → the model download path resolves to the wrong
  directory. Check `%APPDATA%\voice-typer\models\` exists and contains
  the model files. Check `HF_HOME` env var is redirected to that path
  per `asr_setup.py`.
- `ImportError: libiomp5md.dll` → see §11; the `--include-data-dir`
  flag in §1 did not capture `libiomp5md.dll`.

### §6.4 `enigo` paste (short + long text)

**VALIDATE ON WINDOWS HOST**

**Tests**: ADR-0020 §6.2 + §6.3 — short text (< ~300 chars) is injected
via `enigo.text()` (IME-safe `SendInput` with `KEYEVENTF_UNICODE`);
long text (≥ ~300 chars) is copied via
`tauri-plugin-clipboard-manager` then `Ctrl+V` is sent via `enigo`.

The Rust `paste_text` command (`main.rs:660`) handles both paths; the
threshold is `PASTE_SHORT_THRESHOLD` (defined near the top of
`main.rs`).

**Command (short text)**:

1. Open **Notepad** (Win+R → `notepad` → Enter).
2. Click in the Notepad window (so it has focus).
3. Press the dictation hotkey.
4. Speak a short phrase, e.g., "test paste short".
5. Release the hotkey.

**Expected**: the transcribed text appears in Notepad.

**Command (long text)**:

1. Keep Notepad focused.
2. Press the dictation hotkey.
3. Speak a long phrase (≥ 60 seconds of speech, or paste a paragraph
   into the mic's "text-to-speech" test input — any way to generate
   >300 chars of transcription).
4. Release the hotkey.

**Expected**: the long transcription appears in Notepad via clipboard +
`Ctrl+V`. The previous clipboard contents may be replaced (per
`clipboard_snapshot.py` borrow/restore — ADR-0012).

**Verify in logs**:

```powershell
**VALIDATE ON WINDOWS HOST** Get-Content "$env:APPDATA\voice-typer\logs\voice-typer.log" -Tail 200 |
    Select-String "PASTE"
```

Expected:
- Short: `[PASTE] injected N chars via enigo`
- Long: `[PASTE] injected N chars via clipboard + Ctrl/Cmd+V`

**Pass criteria**:
- Short text appears in Notepad (no clipboard side-effect).
- Long text appears in Notepad.
- `[PASTE]` log line with the correct path (`enigo` vs `clipboard +
  Ctrl/Cmd+V`) appears for each.

**Fail scenarios**:
- Text appears in the Voice Typer window instead of Notepad → focus
  was stolen by the Voice Typer bubble/main window. ADR-0020 §6.3
  describes the `AttachThreadInput` + `SetForegroundWindow` focus-
  restore dance; this is implemented in the Python side's
  `clipboard.py`, NOT in the Rust `paste_text` (which delegates to
  enigo directly). The UI must call `paste_text` AFTER the user's
  target window is focused. If focus-restore fails on Windows 11,
  see §11.
- `enigo init failed` → the `enigo` crate failed to initialize on
  Windows (rare; usually a missing `user32.dll` import). Check the
  Rust build logs.
- `clipboard write failed` → `tauri-plugin-clipboard-manager` not
  initialized; check `main.rs` `.plugin(tauri_plugin_clipboard_manager::init())`.

### §6.5 Toast notification

**VALIDATE ON WINDOWS HOST**

**Tests**: ADR-0020 §6.1 — `tauri-plugin-notification` shows a Windows
toast (WinRT `ToastNotification` on Windows 10+). `enigo` does NOT do
toasts — this is the notification plugin's job.

**Command** (in the running app):

1. Open **Settings → General**.
2. Toggle "Notifications" ON (if off).
3. Trigger a notification event — easiest: start dictation, then stop
   without speaking (some notifications fire on "empty transcription");
   OR trigger an error notification (e.g., disconnect the mic
   mid-dictation).

**Expected**: a Windows toast notification appears in the Action
Center with the Voice Typer icon + the notification text.

**Verify**:

```powershell
**VALIDATE ON WINDOWS HOST** # Open Action Center (Win+N or click the
# notification icon in the taskbar). A Voice Typer toast should be
# listed.
```

**Pass criteria**:
- A Windows toast notification appears within 5 s of the trigger.
- The toast has the Voice Typer icon + text.
- No `notification:allow-notify` capability error in `voice-typer.log`
  (would indicate the capability wasn't granted — re-check
  `src-tauri/capabilities/main-runtime.json`).

**Fail scenarios**:
- No toast appears + no error log → "Focus Assist" / "Do Not Disturb"
  is suppressing notifications. Disable Focus Assist (Settings →
  System → Notifications) and retry.
- `PermissionDenied` error → the notification plugin wasn't granted
  permission. On Windows, the notification plugin doesn't need
  explicit user permission (unlike macOS 11+), but the capability
  must list `notification:allow-notify`.

### §6.6 Cooperative shutdown

**VALIDATE ON WINDOWS HOST**

**Tests**: ADR-0020 §10 — when the user closes the main window, the
Rust host sends `{"type":"shutdown"}` over the WS; the sidecar
releases the mic, acks `{"type":"result","data":{"ack":true}}`, and
exits 0. Hard timeout: if the sidecar hasn't exited within 2.0 s of
the ack (see `SHUTDOWN_ACK_TIMEOUT_MS` in `main.rs`), Rust force-kills
the process tree via `kill_children`.

**Command**:

1. With the app running, close the main window (click X, or `Alt+F4`).
2. Within 2 s, check Task Manager:

```powershell
**VALIDATE ON WINDOWS HOST** tasklist | findstr /I "python-sidecar voice-typer windows-key-listener"
```

**Expected**: all three processes are gone within 2 s of window close.

**Verify in logs**:

```powershell
**VALIDATE ON WINDOWS HOST** Get-Content "$env:APPDATA\voice-typer\logs\voice-typer.log" -Tail 100 |
    Select-String "SHUTDOWN|shutdown"
**VALIDATE ON WINDOWS HOST** Get-Content "$env:APPDATA\voice-typer\logs\sidecar.log" -Tail 100 |
    Select-String "SIDECAR-WS"
```

Expected (in `voice-typer.log`):
```
[SHUTDOWN] sidecar exited gracefully (code=Some(0), signal=None)
```

(on graceful shutdown — see `src-tauri/src/commands/sidecar_cmds.rs` line ~164)

or, if Rust had to force-kill after the 2 s ack timeout:
```
[SHUTDOWN] sidecar kill completed (graceful=false)
```

(on force-kill path — see `src-tauri/src/commands/sidecar_cmds.rs` line ~206)

The `graceful=true` variant appears when the sidecar exited on its own
within the ack timeout (the `exited gracefully (code=..., signal=...)`
log line is emitted first); `graceful=false` appears when Rust had to
fall back to `kill_children`.

Expected (in `sidecar.log`):
```
[SIDECAR-WS] shutdown received — releasing mic and exiting
```

**Pass criteria**:
- All three processes (`voice-typer`, `python-sidecar`,
  `windows-key-listener`) exit within 2 s of window close.
- One of `[SHUTDOWN] sidecar exited gracefully (code=..., signal=...)`
  (graceful) OR `[SHUTDOWN] sidecar kill completed (graceful=...)`
  (after-force-kill) appears in `voice-typer.log`.
- `[SIDECAR-WS] shutdown received` appears in `sidecar.log`.

**Fail scenarios**:
- Sidecar lingers >2 s → the cooperative shutdown path didn't fire
  (the `on_window_event` handler in `main.rs:787` only fires for the
  `main` window — closing the `bubble` window doesn't trigger
  shutdown). Verify you closed the main window, not just the bubble.
- Sidecar exits but `windows-key-listener.exe` lingers → the sidecar
  didn't kill its native-listener child on shutdown. The
  `kill_children` backstop should handle this; if not, the
  `native_hotkeys/` package exit handler needs fixing.

### §6.7 Prewarm `LogonTrigger`

**VALIDATE ON WINDOWS HOST**

**Tests**: ADR-0020 §5 — prewarm is registered as a Windows Task
Scheduler task with `LogonTrigger` (via `task_scheduler.py`). On
login, the task runs `prewarm-x86_64-pc-windows-msvc.exe` which warms
the OS file cache (~7 GB of torch + transformers + model weights).

The prewarm exe path is resolved by `resolve_prewarm_exe()` (in
`prewarm_resolver.py`), which checks `VOICE_TYPER_PREWARM_EXE` env var
(set by Rust at startup) → Tauri resource dir → install dir → dev
fallback (plain python module).

**Command**:

1. In the running app, open **Settings → General**.
2. Enable "Prewarm on login" (toggles `task_scheduler.register_prewarm_task()`).
3. Sign out (Win+L → "Sign out") + sign back in.

**Verify** the task was registered + ran:

```powershell
**VALIDATE ON WINDOWS HOST** schtasks /query /tn "com.voicetyper.prewarm" /v /fo LIST
```

Expected fields:
- `TaskName: \com.voicetyper.prewarm`
- `Logon Trigger: At logon` (under "Trigger Information")
- `Last Run Time: <recent>` (matches your sign-in time)
- `Last Result: 0` (success)

**Verify** the prewarm log:

```powershell
**VALIDATE ON WINDOWS HOST** Get-Content "$env:APPDATA\voice-typer\logs\prewarm.log" -Tail 50
```

Expected: lines showing prewarm ran (file-cache warming of model
files).

**Pass criteria**:
- `schtasks /query` shows the `com.voicetyper.prewarm` task with
  `LogonTrigger` and `Last Run Time` matching the sign-in.
- `Last Result: 0` (success).
- `prewarm.log` shows the prewarm ran.

**Fail scenarios**:
- `Task does not exist` → the sidecar's `task_scheduler.py` didn't
  register the task. Check `sidecar.log` for the registration error.
  Common: `schtasks /create` requires admin for some trigger types —
  see §11.
- `Last Result: 0x1` (failure) → the prewarm exe crashed. Re-run it
  manually to see the error:
  ```powershell
  & "$env:LOCALAPPDATA\Programs\Voice Typer\resources\prewarm-x86_64-pc-windows-msvc.exe" --force
  ```
- `Last Result: 0x2` → file not found. The `resolve_prewarm_exe()`
  didn't find the exe at the expected path. Check
  `VOICE_TYPER_PREWARM_EXE` env var was set by Rust.

### §6.8 Native `windows-key-listener.exe` toggles dictation

**VALIDATE ON WINDOWS HOST**

**Tests**: ADR-0020 §6.4 — the native key-listener binary (built in §3)
spawns as a subprocess of the sidecar, installs a `WH_KEYBOARD_LL` hook,
and emits a `KEY_DOWN:<hotkey>` event when the configured hotkey is
pressed. The sidecar matches the event against the registered hotkey
and toggles dictation.

**Command**:

1. Open **Settings → Hotkey**.
2. Set a custom hotkey (e.g., `F8`) — easier to test than the default
   `Ctrl+Alt+V`.
3. Focus a text field (e.g., Notepad).
4. Press `F8`.
5. Speak a test phrase.
6. Press `F8` again.

**Expected**:
- Pressing `F8` starts recording (the bubble window appears with
  "Listening…").
- Pressing `F8` again stops recording and pastes the transcription
  into Notepad.

**Verify** the native listener is running:

```powershell
**VALIDATE ON WINDOWS HOST** tasklist | findstr /I "windows-key-listener"
# Expected: windows-key-listener.exe in the process list while the app
# is running.
```

**Verify** in logs:

```powershell
**VALIDATE ON WINDOWS HOST** Get-Content "$env:APPDATA\voice-typer\logs\sidecar.log" -Tail 200 |
    Select-String "hotkey|native|KEY_DOWN|toggle"
```

**Pass criteria**:
- `windows-key-listener.exe` is in Task Manager while the app runs.
- Pressing the configured hotkey toggles dictation (bubble appears +
  recording starts; second press stops + pastes).
- No `native binary not found` errors in `sidecar.log`.

**Fail scenarios**:
- `native binary not found` → `VOICE_TYPER_NATIVE_DIR` env var wasn't
  set, or `src-tauri\resources\native\windows-key-listener.exe` was
  not bundled (re-check §3.2).
- Hotkey doesn't fire when an elevated (Administrator) window has
  focus → ADR-0020 §6.3 UIPI limitation; this is an OS limitation, not
  a bug. Switch focus to a non-elevated window and retry.
- Hotkey fires but key suppression doesn't work (the hotkey character
  leaks to the foreground app) → the `WH_KEYBOARD_LL` callback isn't
  returning non-zero; check `windows-key-listener.c`.

### §6.9 Single-instance

**VALIDATE ON WINDOWS HOST**

**Tests**: ADR-0020 §12 — `tauri-plugin-single-instance` ensures only
one Voice Typer process runs. A second launch focuses the existing
instance (shows + sets focus on the main window) and emits a
`second-instance` event; NO second sidecar is spawned.

The single-instance check must run at the **absolute entry point of
`main.rs`** — before any sidecar initialization — so a second launch
doesn't spawn a zombie sidecar.

**Command**:

1. With Voice Typer running, launch it a second time (Start Menu →
   "Voice Typer", or double-click the desktop shortcut).

**Expected**:
- No second Voice Typer window appears.
- The existing main window comes to the foreground (focused).
- No second `python-sidecar` process spawns.

**Verify**:

```powershell
**VALIDATE ON WINDOWS HOST** tasklist | findstr /I "voice-typer python-sidecar"
# Expected: ONE voice-typer process + ONE python-sidecar process
# (no duplicates from the second launch).
```

**Pass criteria**:
- Second launch focuses the existing window within ~1 s.
- Task Manager shows ONE `voice-typer` + ONE `python-sidecar` process.
- No "sidecar spawn failed: port already in use" errors in
  `voice-typer.log`.

**Fail scenarios**:
- Two `python-sidecar` processes after the second launch → the
  single-instance check ran AFTER the sidecar spawn. This is a
  CRITICAL bug — see ADR-0020 §12 "Ordering is critical". The
  `tauri_plugin_single_instance::init(...)` call must be the FIRST
  plugin registered in `main.rs:740`.
- Second window appears → the single-instance plugin isn't installed
  or the `second-instance` event handler in `main.rs:744` isn't
  focusing the main window.

---

## §7 Code signing (Authenticode signtool)

**VALIDATE ON WINDOWS HOST** Per ADR-0020 §13.1, sign the Nuitka-built
sidecar + prewarm exes immediately after build (BEFORE bundling —
unsigned sidecars trigger SmartScreen / AV), then sign the final MSI +
NSIS installer after `cargo tauri build`.

**Required**:
- A code-signing certificate (PFX file) — either self-signed for
  testing or a real Authenticode cert from a CA (DigiCert, Sectigo,
  GlobalSign).
- `signtool.exe` (bundled with Windows SDK; installed with VS Build
  Tools §0.3).

> **Unsigned local builds still need `signtool.exe` on PATH.**
> ``tauri.conf.json`` sets ``bundle.windows.signCommand``
> (``scripts/tauri-sign.cmd``), and Tauri's NSIS bundler locates
> ``signtool.exe`` on PATH *before* invoking the wrapper — even when no
> cert is configured — so on a host without the Windows SDK the bundle
> step fails with ``failed to bundle project: SignTool not found``. On
> such a host, either install the SDK signing tools, or temporarily
> remove ``signCommand`` from ``tauri.conf.json`` for the unsigned
> local build (CI's windows-2022 image has signtool preinstalled, so
> the workflow is unaffected).

### §7.1 Set up the cert (skip if you already have one)

**VALIDATE ON WINDOWS HOST** If you don't have a real cert, create a
self-signed one for testing only (NOT for production — users will see
an "Unknown Publisher" warning):

```powershell
**VALIDATE ON WINDOWS HOST** $cert = New-SelfSignedCertificate `
    -Type CodeSigningCert `
    -Subject "CN=Voice Typer Test Code Signing" `
    -KeyUsage DigitalSignature `
    -FriendlyName "Voice Typer Test Code Signing" `
    -CertStoreLocation "Cert:\CurrentUser\My" `
    -HashAlgorithm SHA256 `
    -NotAfter (Get-Date).AddYears(1)
**VALIDATE ON WINDOWS HOST** $pwd = ConvertTo-SecureString -String "test123" -Force -AsPlainText
**VALIDATE ON WINDOWS HOST** Export-PfxCertificate -Cert $cert `
    -FilePath "$env:USERPROFILE\vt-test-codesign.pfx" -Password $pwd
**VALIDATE ON WINDOWS HOST** # Trust the cert locally (so signtool verify passes):
**VALIDATE ON WINDOWS HOST** $store = New-Object System.Security.Cryptography.X509Certificates.X509Store(
    "Root", "CurrentUser")
**VALIDATE ON WINDOWS HOST** $store.Open("ReadWrite")
**VALIDATE ON WINDOWS HOST** $store.Add($cert)
**VALIDATE ON WINDOWS HOST** $store.Close()
**VALIDATE ON WINDOWS HOST** $store = New-Object System.Security.Cryptography.X509Certificates.X509Store(
    "TrustedPublisher", "CurrentUser")
**VALIDATE ON WINDOWS HOST** $store.Open("ReadWrite")
**VALIDATE ON WINDOWS HOST** $store.Add($cert)
**VALIDATE ON WINDOWS HOST** $store.Close()
```

For production: obtain a real Authenticode cert from a CA. Store the
PFX base64-encoded in the `WIN_CSC_LINK` GitHub secret + its password
in `WIN_CSC_KEY_PASSWORD` (matching `electron-builder.yml`).

### §7.2 Sign the sidecar + prewarm exes

**VALIDATE ON WINDOWS HOST**

```powershell
**VALIDATE ON WINDOWS HOST** $signtool = Get-ChildItem `
    "C:\Program Files (x86)\Windows Kits\10\bin\*\x64\signtool.exe" |
    Select-Object -First 1 -ExpandProperty FullName
**VALIDATE ON WINDOWS HOST** Write-Host "Using signtool: $signtool"

**VALIDATE ON WINDOWS HOST** $pfx = "$env:USERPROFILE\vt-test-codesign.pfx"
**VALIDATE ON WINDOWS HOST** $pfxPwd = "test123"   # replace with real password

# Sign the sidecar exe:
**VALIDATE ON WINDOWS HOST** & $signtool sign /f $pfx /p $pfxPwd `
    /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 `
    src-tauri\bin\python-sidecar-x86_64-pc-windows-msvc.exe
**VALIDATE ON WINDOWS HOST** & $signtool verify /pa /v `
    src-tauri\bin\python-sidecar-x86_64-pc-windows-msvc.exe
# Expected: "Successfully verified"

# Sign the prewarm exe:
**VALIDATE ON WINDOWS HOST** & $signtool sign /f $pfx /p $pfxPwd `
    /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 `
    src-tauri\resources\prewarm-x86_64-pc-windows-msvc.exe
**VALIDATE ON WINDOWS HOST** & $signtool verify /pa /v `
    src-tauri\resources\prewarm-x86_64-pc-windows-msvc.exe
# Expected: "Successfully verified"
```

### §7.3 Sign the MSI + NSIS installer

**VALIDATE ON WINDOWS HOST** After `cargo tauri build` (§4):

```powershell
**VALIDATE ON WINDOWS HOST** $msi  = (Get-ChildItem "src-tauri\target\x86_64-pc-windows-msvc\release\bundle\msi\*.msi" |
    Select-Object -First 1).FullName
**VALIDATE ON WINDOWS HOST** $nsis = (Get-ChildItem "src-tauri\target\x86_64-pc-windows-msvc\release\bundle\nsis\*-setup.exe" |
    Select-Object -First 1).FullName

**VALIDATE ON WINDOWS HOST** & $signtool sign /f $pfx /p $pfxPwd `
    /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $msi
**VALIDATE ON WINDOWS HOST** & $signtool verify /pa /v $msi

**VALIDATE ON WINDOWS HOST** & $signtool sign /f $pfx /p $pfxPwd `
    /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $nsis
**VALIDATE ON WINDOWS HOST** & $signtool verify /pa /v $nsis
```

### §7.4 SmartScreen reputation note

**VALIDATE ON WINDOWS HOST** Even with a valid cert, SmartScreen will
show a warning until the cert builds reputation (typically thousands
of downloads over weeks). This is expected; users click "More info" →
"Run anyway". For production, use an EV (Extended Validation) cert to
bypass the reputation requirement immediately.

ADR-0020 §13.1 notes: the Nuitka `--onefile` inner exe extracts to a
temp dir at runtime and is NOT separately signed — AV may briefly flag
the temp extraction; that is expected and benign, NOT a packaging bug.

---

## §8 Rollback to Electron

**VALIDATE ON WINDOWS HOST** ADR-0020 mandates the migration stays
reversible per-platform. If any of §6.1–§6.9 fails and cannot be
fixed within the validation window, roll back to the existing
Electron build — the Electron code is untouched by the Tauri migration
(no source files shared; Tauri is purely additive).

### §8.1 Uninstall the Tauri build

```powershell
**VALIDATE ON WINDOWS HOST** # Via Add/Remove Programs:
**VALIDATE ON WINDOWS HOST** Get-Package "Voice Typer*" -ErrorAction SilentlyContinue |
    Uninstall-Package

# Or via winget:
**VALIDATE ON WINDOWS HOST** winget uninstall "Voice Typer"
```

The Tauri uninstaller removes:
- `%LOCALAPPDATA%\Programs\Voice Typer\`
- Start Menu entry
- The Task Scheduler `com.voicetyper.prewarm` task (if registered — the
  Tauri uninstaller must deregister this, along with the legacy
  `VoiceTyperPrewarm` from pre-rename installs; see ADR-0020 §5
  "Uninstall cleanup per platform")

**NOTE**: The Tauri install shares the `%APPDATA%\voice-typer\` config
dir with the Electron build — config.json, models/, history.db, logs/
are PRESERVED on uninstall (matches the Electron behavior; data is
never deleted on uninstall). The Electron app picks up the same
config + models on next launch.

### §8.2 Verify the Electron build still ships

**VALIDATE ON WINDOWS HOST** From the repo root:

```powershell
**VALIDATE ON WINDOWS HOST** cd voice_typer\client
**VALIDATE ON WINDOWS HOST** npm run build        # builds main + preload + renderer
**VALIDATE ON WINDOWS HOST** npx electron-builder --win --x64
**VALIDATE ON WINDOWS HOST** cd ..\..
# Output: dist/VoiceTyper Setup <ver>.exe (NSIS installer)
```

Install + launch the Electron build — it should pick up the same
`%APPDATA%\voice-typer\` config + models + history as the Tauri build
(no data loss on revert).

### §8.3 What to report when rolling back

If you roll back, file an issue with:
- Which gate item(s) failed (§6.1–§6.9).
- The exact error message + log excerpt.
- The Windows version + build number.
- The Nuitka version + python-build-standalone release date.
- `voice-typer.log` + `sidecar.log` (zipped).

This unblocks the next iteration of the Tauri migration without
blocking the release.

---

## §9 Capturing results

**VALIDATE ON WINDOWS HOST** Fill in this table on the host and
attach the supporting logs. Copy the filled table into the migration
tracking issue.

| # | Gate item | Pass? | Observed | Log excerpt |
|---|---|---|---|---|
| §6.1 | Sidecar spawn via externalBin | ☐ Pass ☐ Fail | server_started port=____ | `[SIDECAR] server_started port=...` |
| §6.2 | WS + bearer-token handshake | ☐ Pass ☐ Fail | auth accepted at T+__s | `[SIDECAR-WS] auth accepted` |
| §6.3 | faster-whisper transcribe | ☐ Pass ☐ Fail | transcribed "____" in __s | model + device name from History |
| §6.4 | enigo paste (short + long) | ☐ Pass ☐ Fail | short=__ long=__ | `[PASTE] injected N chars via enigo` |
| §6.5 | Toast notification | ☐ Pass ☐ Fail | toast appeared at T+__s | screenshot of Action Center |
| §6.6 | Cooperative shutdown | ☐ Pass ☐ Fail | sidecar exited in __ms | `[SHUTDOWN] sidecar exited gracefully (code=..., signal=...)` OR `[SHUTDOWN] sidecar kill completed (graceful=...)` |
| §6.7 | Prewarm LogonTrigger | ☐ Pass ☐ Fail | Last Run Time=____ Last Result=____ | `schtasks /query /v` output |
| §6.8 | Native windows-key-listener | ☐ Pass ☐ Fail | hotkey ____ toggles dictation | `tasklist` shows windows-key-listener.exe |
| §6.9 | Single-instance | ☐ Pass ☐ Fail | second launch focused existing | `tasklist` shows 1+1 processes |
| §10.1 | Export History (Sub-agent A) | ☐ Pass ☐ Fail | file written at ____ | file path + size |
| §10.2 | Export Vocabulary (Sub-agent A) | ☐ Pass ☐ Fail | file written at ____ | file path + size |
| §10.3 | Bubble window (Sub-agent A) | ☐ Pass ☐ Fail | bubble appeared + hid | bubble coords + duration |

**Host info** (fill in):

| Field | Value |
|---|---|
| Windows version + build | |
| CPU arch | x86_64 |
| RAM | |
| Python dev version | |
| python-build-standalone release date | 20241219 |
| python-build-standalone CPython version | 3.12.8 |
| Nuitka version | 2.5.4 |
| Rust toolchain | stable-x86_64-pc-windows-msvc |
| Node version | |
| Code signing cert type | ☐ None ☐ Self-signed ☐ Real Authenticode ☐ EV |
| Validator name | |
| Validation date | |

**Artifacts to attach**:
- `%APPDATA%\voice-typer\logs\voice-typer.log` (rotated; zip the whole
  `logs/` dir).
- `%APPDATA%\voice-typer\logs\sidecar.log` (rotated; same zip).
- `%APPDATA%\voice-typer\logs\prewarm.log`.
- `schtasks /query /tn "com.voicetyper.prewarm" /v /fo LIST > prewarm-task.txt`.
- `tasklist /v > tasklist.txt` (captured while app running).
- Screenshots of: main window, bubble window (§10.3), toast (§6.5),
  Action Center.
- `src-tauri\target\x86_64-pc-windows-msvc\release\bundle\msi\*.msi`
  (or a SHA-256 of it if too large to attach).
- The §6.3 transcription text + the model used.

---

## §10 New Rust commands validation (Sub-agent A additions)

**VALIDATE ON WINDOWS HOST** Sub-agent A is adding three new feature
areas to the Tauri host (Rust + TS bridge): `export_history`,
`export_vocabulary`, and the bubble-window commands (`bubble_show`,
`bubble_signal_ready`, `bubble_set_position`, `bubble_set_draggable`,
`bubble_move_by`, `bubble_hide_complete`). These are NOT in the
ADR-0020 §2 command table — they are new commands per ADR-0020 §16
("New commands / events process"). Validate each below.

### §10.1 Export History

**VALIDATE ON WINDOWS HOST**

**Tests**: the React UI calls `invoke('dispatch', {cmd: 'export_history', data: {...}})`
→ Rust forwards over WS → sidecar's `_handle_export_history` reads the
history DB and writes a file (JSON or CSV) to a user-chosen path via
the Tauri `dialog.save` API.

**Prerequisite**: history must have at least one entry (run §6.3 first
to create a transcription).

**Command** (in the running app):

1. Open the **History** page (sidebar → "History").
2. Verify at least one entry exists (from §6.3).
3. Click the **Export** button.
4. In the save dialog, choose a path (e.g., `Desktop\vt-history.json`).
5. Click Save.

**Expected**:
- The file is written at the chosen path within ~2 s.
- A toast "History exported" appears.
- No `dispatch timeout` or `server error` in `voice-typer.log`.

**Verify**:

```powershell
**VALIDATE ON WINDOWS HOST** Test-Path "$env:USERPROFILE\Desktop\vt-history.json"
# Expected: True
**VALIDATE ON WINDOWS HOST** Get-Content "$env:USERPROFILE\Desktop\vt-history.json" |
    Select-Object -First 5
# Expected: valid JSON with the history entries
```

**Pass criteria**:
- File exists at the chosen path.
- File is valid JSON (or CSV if the user chose CSV).
- File contains the expected history entries (count matches the
  History page).
- Toast "History exported" appears.

**Fail scenarios**:
- `dispatch timeout (30s)` → the sidecar's `_handle_export_history`
  didn't respond within 30 s. Check `sidecar.log` for the handler
  error.
- Save dialog doesn't appear → the `dialog:allow-save` capability is
  missing (it's NOT in the current `main-runtime.json` — Sub-agent
  A must add it). Check `src-tauri/capabilities/main-runtime.json`.
- File written to wrong path → the dialog returned a path the sidecar
  couldn't write to (permissions). Check the path is writable.

### §10.2 Export Vocabulary

**VALIDATE ON WINDOWS HOST**

**Tests**: same as §10.1 but for the vocabulary (custom words + their
pronunciations).

**Prerequisite**: vocabulary must have at least one entry. Add one via
**Settings → Vocabulary → Add word** before this step.

**Command** (in the running app):

1. Open the **Vocabulary** page (sidebar → "Vocabulary").
2. Verify at least one entry exists.
3. Click the **Export** button.
4. In the save dialog, choose a path (e.g., `Desktop\vt-vocab.json`).
5. Click Save.

**Expected**:
- File is written at the chosen path within ~2 s.
- Toast "Vocabulary exported" appears.

**Verify**:

```powershell
**VALIDATE ON WINDOWS HOST** Test-Path "$env:USERPROFILE\Desktop\vt-vocab.json"
# Expected: True
**VALIDATE ON WINDOWS HOST** Get-Content "$env:USERPROFILE\Desktop\vt-vocab.json" |
    Select-Object -First 5
# Expected: valid JSON with the vocabulary entries
```

**Pass criteria**:
- File exists at the chosen path.
- File is valid JSON.
- File contains the expected vocabulary entries.

### §10.3 Bubble window

**VALIDATE ON WINDOWS HOST**

**Tests**: ADR-0020 §9 — the bubble window (a 240×80 always-on-top
transparent window declared in `tauri.conf.json:29-39`) shows the
dictation status (RMS level, "Listening…", transcription preview)
at the cursor position during dictation. The sidecar emits
`bubble_level` at ~60 Hz; Rust coalesces to ≤30 Hz to prevent WebView
jank.

The bubble lifecycle:
1. User triggers dictation (hotkey).
2. Sidecar emits `bubble_show` event → Rust shows the bubble window at
   the cursor position (via `bubble_set_position`).
3. Sidecar emits `bubble_level` events (~60 Hz) → Rust coalesces + emits
   to the bubble WebView.
4. User stops dictation → sidecar emits the final transcription + a
   `bubble_hide_complete` event → Rust hides the bubble window.

**Command** (in the running app):

1. Open Notepad + click in it (so it has focus + a cursor position).
2. Press the dictation hotkey (e.g., `F8`).
3. Observe the bubble window appears near the cursor position in
   Notepad.
4. Speak a test phrase.
5. Observe the bubble shows "Listening…" + an RMS level indicator
   (animated bar).
6. Press the hotkey again to stop.
7. Observe the bubble hides within ~500 ms.

**Expected**:
- Bubble window appears at the cursor position (within ~50 px; the
  exact offset is implementation-defined by Sub-agent A's
  `bubble_set_position`).
- Bubble shows "Listening…" + RMS level indicator while dictating.
- Bubble hides within 500 ms of dictation stopping.
- Transcription is pasted into Notepad (per §6.4).

**Verify** in logs:

```powershell
**VALIDATE ON WINDOWS HOST** Get-Content "$env:APPDATA\voice-typer\logs\voice-typer.log" -Tail 200 |
    Select-String "bubble"
**VALIDATE ON WINDOWS HOST** Get-Content "$env:APPDATA\voice-typer\logs\sidecar.log" -Tail 200 |
    Select-String "bubble"
```

Expected (in `voice-typer.log`): bubble show/hide events at the
expected times.

Expected (in `sidecar.log`): NO `bubble_level` log entries (per
ADR-0020 §11, `bubble_level` must be excluded from the file log to
prevent disk fill — only `DEBUG`-level or suppressed entirely).

**Pass criteria**:
- Bubble appears at the cursor position when dictation starts.
- Bubble shows "Listening…" + RMS level while dictating.
- Bubble hides within 500 ms of dictation stopping.
- No `bubble_level` spam in `sidecar.log` (would indicate the §11
  exclusion is broken).
- No WebView jank in the bubble (would indicate the §9 coalescing
  isn't working — the bubble should update at ≤30 Hz, not 60 Hz).

**Fail scenarios**:
- Bubble doesn't appear → the `bubble_show` command didn't fire (check
  Sub-agent A's implementation in `main.rs`), OR the bubble window's
  `visible: false` initial state wasn't toggled to `true`.
- Bubble appears at wrong position → `bubble_set_position` got the
  wrong cursor coords. Check the Win32 `GetCaretPos` /
  `GetForegroundWindow` + `GetCursorPos` call in Sub-agent A's
  implementation.
- Bubble doesn't hide → `bubble_hide_complete` didn't fire, OR the
  Rust hide handler didn't run. Check the WS event subscription in
  `main.rs`.
- `bubble_level` spam in `sidecar.log` → the §11 exclusion isn't
  applied; fix `log.py` to drop `bubble_level` from the file handler.

---

## §11 Known Issues (Windows-specific failure modes)

This section documents common Windows-specific failure modes a
validator is likely to hit, with the exact remediation. Cross-reference
from the relevant §6 gate item when a failure matches.

### §11.1 MSVC not on PATH

**Symptom**: `cl.exe` not found by `compile_native.ps1` (§3) or by
Nuitka (§1). Error: "neither cl.exe (MSVC) nor MinGW gcc found" or
"Microsoft Visual C++ 14.0 is required".

**Cause**: VS Build Tools installed but the user opened a plain
PowerShell window (not a "Developer PowerShell for VS 2022"), so the
MSVC env vars (`INCLUDE`, `LIB`, `PATH`) aren't loaded.

**Fix**: Open "Developer PowerShell for VS 2022" from the Start Menu
(it auto-loads `vcvars64.bat`), OR run `vcvars64.bat` from a plain
PowerShell before continuing:

```powershell
**VALIDATE ON WINDOWS HOST** & "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
```

Verify: `where cl.exe` should resolve to the MSVC `cl.exe`.

### §11.2 `libiomp5md.dll` missing (CTranslate2 / OpenMP runtime)

**Symptom**: §1 Nuitka build succeeds, but the smoke test (§1.1) or
the running sidecar (§6.3) crashes instantly with
`ImportError: libiomp5md.dll not found` or a silent crash on
`import ctranslate2`.

**Cause**: CTranslate2 links Intel MKL / OpenMP for fast x86 CPU
inference. The `libiomp5md.dll` (OpenMP) + MKL redistributables are
shipped under `<site-packages>\ctranslate2\lib\` but Nuitka does NOT
auto-collect them — they must be explicitly included via
`--include-data-dir=$SITE\ctranslate2\lib=...` (which IS in the §1
command). If you altered the command or the CTranslate2 version
changed, the DLL may now be at a different path.

**Fix**:
1. Enumerate the lib dir: `Get-ChildItem "$SITE\ctranslate2\lib\*.dll"`.
2. Confirm `libiomp5md.dll` + `ctranslate2.dll` are present.
3. If `libiomp5md.dll` is at a different path (e.g.,
   `$SITE\..\ intel\...`), add an explicit
   `--include-dll=<full-path-to-libiomp5md.dll>` to the §1 command.
4. Rebuild + rerun the §1.1 smoke test.

ADR-0020 §4.2 note: "CPU inference runtimes (easy to miss, instant
crash if absent) — if `libiomp5md.dll` (OpenMP) or the MKL
redistributables are missing, the frozen exe builds fine but crashes
instantly on `import ctranslate2` at launch."

### §11.3 `--onefile` tempdir bloat

**Symptom**: `%TEMP%\onefile_*` accumulates gigabytes of extracted
sidecar/prewarm binaries across launches/crashes. Disk fills up.

**Cause**: Nuitka `--onefile` extracts to a fresh temp dir on every
launch; orphaned dirs accumulate if the process crashes before
cleanup.

**Fix**: The §1 + §2 commands pin a deterministic extract dir via
`--onefile-tempdir-spec=%LOCALAPPDATA%\voice-typer\onefile-tmp`
(sidecar) and `...\prewarm-onefile-tmp` (prewarm). This means the
extract dir is reused across launches (no accumulation). Verify the
flag is present in your command.

If bloat has already accumulated:

```powershell
**VALIDATE ON WINDOWS HOST** Get-ChildItem "$env:TEMP\onefile_*" -Directory |
    Remove-Item -Recurse -Force
```

The Tauri installer/uninstaller should also purge the deterministic
extract dir on uninstall (per ADR-0020 §4.2 "have the
installer/uninstaller purge that dir"); this is a packaging TODO for
Sub-agent F.

### §11.4 Task Scheduler requires admin for some triggers

**Symptom**: §6.7 prewarm task registration fails with
"ERROR: The task requires the user to be an administrator" or the
task registers but never fires.

**Cause**: Windows Task Scheduler requires admin privileges to
register certain trigger types (notably `OnEvent` and some
`LogonTrigger` configurations). A `LogonTrigger` scoped to the
current user (NOT "any user") should NOT require admin, but the
`schtasks /create` invocation must include `/RL LIMITED` (or omit
`/RL` entirely) to avoid requesting admin.

**Fix**: Check `task_scheduler.py`'s `register_prewarm_task()` — it
must use `/RL LIMITED` (or the equivalent XML task definition with
`<Principal><UserId>` = current user + `<LogonType>InteractiveToken`).
If the sidecar logs "access denied" on task registration, run the
app once as admin (right-click → "Run as administrator") to register
the task, then close + relaunch as a normal user.

ADR-0020 §6.4 doesn't explicitly call out this Windows quirk; it's a
known issue with `schtasks` and the LogonTrigger.

### §11.5 `enigo` focus-restore on Windows 11

**Symptom**: §6.4 paste works on Windows 10 but on Windows 11 the
text appears in the Voice Typer window (or nowhere) instead of the
target app.

**Cause**: ADR-0020 §6.3 describes the Win32 focus-restore dance
(`AttachThreadInput` + `SetForegroundWindow`) that the Python
sidecar's `clipboard.py` uses to ensure the target window has focus
before paste. The Rust `paste_text` command (in `main.rs:660`)
delegates to `enigo` directly WITHOUT the focus-restore dance —
`enigo` injects into whatever window currently has focus. If the
Voice Typer main/bubble window stole focus (e.g., the bubble appeared
+ took focus), the paste goes to the wrong window.

On Windows 11, focus-stealing prevention is stricter than Windows 10
— `SetForegroundWindow` silently fails more often, especially when
the target app was recently backgrounded.

**Fix**:
1. Ensure the bubble window has `skipTaskbar: true` + `alwaysOnTop:
   true` + `focus: false` (the latter is set via Rust
   `window.set_focus()` NOT being called on the bubble). The current
   `tauri.conf.json:29-39` declares `skipTaskbar` + `alwaysOnTop` but
   does NOT explicitly set `focus: false` — Sub-agent A should add
   this.
2. The UI must call `paste_text` AFTER ensuring the target window is
   focused (e.g., by NOT calling `set_focus` on the Voice Typer main
   window when dictation completes).
3. If the issue persists on Windows 11, the Rust `paste_text` should
   implement the §6.3 focus-restore dance (`AttachThreadInput` +
   `SetForegroundWindow`) via the `windows` crate (NOT `enigo` —
   `enigo` doesn't expose `AttachThreadInput`). This is a known gap
   in the current `main.rs` implementation.

ADR-0020 §6.3 explicitly calls out the UIPI failure mode: "if
`AttachThreadInput` returns `0`, do NOT retry the window-switch —
fall back immediately: write the text to the system clipboard, push
it to crash-recovery, and surface a toast 'Could not paste — text
copied to clipboard'". This fallback is NOT yet implemented in the
Rust `paste_text` — it's a TODO for the Sub-agent A or a follow-up.

### §11.6 SmartScreen warning on unsigned installer

**Symptom**: §5 installer launch shows "Windows protected your PC"
SmartScreen warning.

**Cause**: No code-signing cert (§7 not done) OR the cert doesn't
have enough reputation.

**Fix**: Click "More info" → "Run anyway". For production, sign the
installer (§7.3) with a real Authenticode cert. EV certs bypass
SmartScreen immediately; standard certs need reputation (thousands
of downloads over weeks).

### §11.7 WebView2 not installed (Win10 LTSC / stripped images)

**Symptom**: §4 Tauri build succeeds but the app fails to launch with
"WebView2 runtime not found" or a blank window.

**Cause**: Windows 10 LTSC / stripped enterprise images may not have
WebView2 preinstalled.

**Fix**: Install WebView2 Runtime from
`https://go.microsoft.com/fwlink/p/?LinkId=2124703` (Microsoft's
official evergreen bootstrapper). Verify per §0.6.

### §11.8 `cargo tauri` not installed

**Symptom**: §4 fails with "cargo: command not found" or
"error: no such command: `tauri`".

**Fix**:

```powershell
**VALIDATE ON WINDOWS HOST** cargo install tauri-cli --version "^1.5" --locked
**VALIDATE ON WINDOWS HOST** cargo tauri --version
```

The `--locked` flag uses the locked dependencies from
`Cargo.lock`, ensuring reproducible builds.

### §11.9 NSIS path with spaces

**Symptom**: §4 build fails with "The system cannot find the path
specified" when Tauri tries to invoke NSIS.

**Cause**: The repo is cloned to a path with spaces (e.g.,
`C:\Users\John Doe\voice-typer`), and the NSIS bundler doesn't quote
the path correctly in some Tauri versions.

**Fix**: Move the repo to a path without spaces (e.g.,
`C:\dev\voice-typer`). This is a known Tauri/NSIS issue; check the
Tauri issue tracker for the version-specific fix.

### §11.10 Antivirus false positives on Nuitka `--onefile`

**Symptom**: §1 build succeeds but Windows Defender quarantines
`python-sidecar-x86_64-pc-windows-msvc.exe` immediately.

**Cause**: Nuitka `--onefile` self-extraction (extract inner exe to
temp dir at runtime) matches some AV heuristics for "packed
malware". This is also a known issue with PyInstaller `--onefile`
(see `voice-typer.spec` `upx=False` comment for the same rationale).

**Fix**:
1. Add an exclusion in Windows Defender for
   `%LOCALAPPDATA%\Programs\Voice Typer\` + the build output dir
   `src-tauri\bin\`.
2. Code-signing (§7) dramatically reduces false positives.
3. For end users: document the Defender exclusion in the install
   README. ADR-0020 §4.5 notes: "AV may briefly flag the temp
   extraction; that is expected and benign".

---

## Appendix — Dev mode (`VOICE_TYPER_SIDECAR_DEV=1`)

**VALIDATE ON WINDOWS HOST** Sub-agent A is adding a dev mode to the
Tauri host: when the `VOICE_TYPER_SIDECAR_DEV=1` env var is set
BEFORE launching `cargo tauri dev` (or the installed app), the Rust
host spawns `python -m voice_typer.server.ipc_server` (with
`--ws` + the same `VOICE_TYPER_IPC_TOKEN` + `VOICE_TYPER_NATIVE_DIR`
env vars) instead of the frozen `externalBin` sidecar.

This lets a validator iterate on Python sidecar code in seconds (no
Nuitka rebuild between changes) — useful when debugging a §6 gate
failure that requires Python-side changes.

**To enable dev mode** (PowerShell):

```powershell
**VALIDATE ON WINDOWS HOST** $env:VOICE_TYPER_SIDECAR_DEV = "1"
**VALIDATE ON WINDOWS HOST** $env:VOICE_TYPER_NATIVE_DIR = `
    (Resolve-Path "voice_typer\server\native").Path
**VALIDATE ON WINDOWS HOST** cd src-tauri
**VALIDATE ON WINDOWS HOST** cargo tauri dev
```

Expected:
- The Rust host's `spawn_sidecar_and_get_port` (or a dev-mode branch
  Sub-agent A adds) detects `VOICE_TYPER_SIDECAR_DEV=1` and spawns
  `python -m voice_typer.server.ipc_server --ws` instead of the
  `externalBin` sidecar.
- The same `server_started` JSON appears on stdout; the same WS +
  bearer-token handshake (§6.2) runs.
- Edits to `voice_typer/server/*.py` are picked up on the next
  `cargo tauri dev` restart (no Nuitka rebuild).

ADR-0020 §14 note: "On Windows dev, `python -m voice_typer.server.ipc_server`
opens a console window. Use `pythonw.exe` instead, or accept the
console for dev (it shows logs)."

**Dev mode is NOT a substitute for §6 host validation** — the final
gate must pass against the FROZEN Nuitka sidecar (§1) because that's
what ships. Dev mode is for iterating on a fix; once the fix is in,
rebuild the sidecar (§1) and re-run the failing §6 item.

---

## Appendix A — CI workflow reference

The CI workflow at `.github/workflows/tauri-windows-build.yml` is the
automated counterpart of this runbook. It is ENABLED for validation:
the active x86_64 matrix leg runs on `workflow_dispatch` (or via the
`tauri-build.yml` orchestrator's `workflow_call`) so a Phase 0-W
validation dispatch actually produces the installer artifacts. It does
NOT run on push/PR until the Phase 0-W gate (§6) has actually passed on
a real Windows host — the push/PR triggers stay commented out.

Once §6 passes, the release engineer should:
1. Un-comment the `push`/`pull_request` triggers on the
   `tauri-windows-build` job (the workflow is otherwise active).
2. Verify the workflow runs green on `main`.
3. Download the workflow artifacts (NSIS installer, MSI installer,
  sidecar binary, prewarm binary, native listener binary, SHA256SUMS)
  and run §5 + §6 against them on a fresh Windows host to confirm CI
  produces shippable binaries.

The workflow:
- Installs Rust + Node + Python 3.12 + MSVC + Nuitka + uv on a
  `windows-2022` runner.
- Downloads + SHA-256-verifies python-build-standalone cpython-3.12.8
  + 20241219 (matches §0.7).
- Builds the sidecar + prewarm with Nuitka (matches §1 + §2).
- Builds the native listener via `compile_native.ps1` (matches §3).
- (Optional, if `WIN_CSC_LINK` + `WIN_CSC_KEY_PASSWORD` secrets are
  set) Authenticode-signs the sidecar + prewarm + MSI + NSIS (matches
  §7).
- Builds the Tauri app via `cargo tauri build --target
  x86_64-pc-windows-msvc` (matches §4).
- Uploads all artifacts + SHA-256 checksums.
- (On tag pushes) Attests build provenance (SLSA) via
  `actions/attest-build-provenance@v1`.

The workflow does NOT (and cannot) run the §6 host validation gate —
that requires a real Windows desktop session with audio + GUI, which
GitHub Actions runners don't provide. The workflow only PRODUCES the
binaries; the human validator runs §6 against them.

---

## Appendix B — Cross-references

| This runbook section | ADR-0020 section | Source file |
|---|---|---|
| §0.3 MSVC Build Tools | §4.2 | `scripts/build/compile_native.ps1` |
| §0.7 python-build-standalone | §4.2 | `.github/workflows/tauri-windows-build.yml` |
| §1 Sidecar Nuitka build | §4.2 | `voice_typer/server/ipc_server.py` |
| §2 Prewarm Nuitka build | §5 | `voice_typer/server/prewarm/` package (entry point `__main__.py`), `prewarm_resolver.py` |
| §3 Native listener build | §6.4 | `scripts/build/compile_native.ps1`, `voice_typer/server/native/windows-key-listener.c` |
| §4 Tauri build | §7 | `src-tauri/tauri.conf.json`, `src-tauri/src/main.rs` |
| §5 Install + smoke | §Phase 0-W | (installer) |
| §6.1 Sidecar spawn | §1 | `src-tauri/src/main.rs:169` (`spawn_sidecar_and_get_port`) |
| §6.2 WS + bearer-token | §3 | `voice_typer/server/sidecar_ws.py` |
| §6.3 faster-whisper | §4.5 (verify step) | `voice_typer/server/asr_setup.py` |
| §6.4 enigo paste | §6.2 + §6.3 | `src-tauri/src/main.rs:660` (`paste_text`) |
| §6.5 Toast | §6.1 | `tauri-plugin-notification` |
| §6.6 Cooperative shutdown | §10 | `src-tauri/src/main.rs:705` (`shutdown_sidecar`) |
| §6.7 Prewarm LogonTrigger | §5 | `voice_typer/server/task_scheduler.py` |
| §6.8 Native listener | §6.4 | `voice_typer/server/native_hotkeys/` package |
| §6.9 Single-instance | §12 | `src-tauri/src/main.rs:744` (plugin init) |
| §7 Code signing | §13.1 | `docs/migration/signing-guide.md` |
| §8 Rollback | Reversibility | (Electron build) |
| §10.1 Export History | §16 (new commands) | (Sub-agent A: `main.rs` `export_history`) |
| §10.2 Export Vocabulary | §16 (new commands) | (Sub-agent A: `main.rs` `export_vocabulary`) |
| §10.3 Bubble window | §9 + §16 | `src-tauri/tauri.conf.json:29-39` (bubble window decl) |
| §11 Known Issues | §4.2 + §6.3 | (this runbook) |

---

**End of Phase 0-W runbook.** All steps labeled `**VALIDATE ON WINDOWS
HOST**` must be executed on a real Windows host. The Linux sandbox
this was authored in cannot run any of them. Record each result in
the §9 table and attach the supporting logs. **All 9 §6 items + all
3 §10 items must pass before Windows Tauri cutover (Phase 5-W).**
