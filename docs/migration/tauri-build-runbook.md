# Tauri Build Runbook — Windows / macOS / Linux

**Status**: VALIDATE ON WINDOWS HOST / VALIDATE ON MACOS HOST / VALIDATE ON LINUX HOST. This runbook documents how to build the Tauri v2 host on each platform. The Rust code compiles on Linux (verified via `cargo check`), but the full `cargo tauri build` requires a display server for the WebView.

**Prerequisites (all platforms)**:
- Rust toolchain (`rustup`)
- Node.js 20+
- Python 3.12+ (for the sidecar build)
- Platform-specific WebView runtime (see below)

---

## Linux (x86_64 + aarch64)

**VALIDATE ON LINUX HOST** (with display server)

### Prerequisites

```bash
# System deps (Tauri v2 Linux requirements)
sudo apt-get install -y \
    libwebkit2gtk-4.1-dev \
    libgtk-3-dev \
    librsvg2-dev \
    libssl-dev \
    libxdo-dev \
    pkg-config \
    build-essential

# Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source ~/.cargo/env

# Node + Python
# (Install via your distro's package manager or nvm/pyenv)
```

### Build steps

```bash
cd voice-typer

# 1. Build the React renderer (shared between Electron + Tauri)
cd voice_typer/client
npm install
npm run build:renderer
cd ../..

# 2. Build the native hotkey binaries
bash scripts/build/compile_native.sh

# 3. Build the Tauri host
cd src-tauri
cargo tauri build

# 4. Verify the output
ls -la target/release/bundle/
# Expected: deb/ and rpm/ directories with the Linux installers
```

**Expected output**:
- `target/release/bundle/deb/voice-typer_1.0.0_amd64.deb`
- `target/release/bundle/rpm/voice-typer-1.0.0.x86_64.rpm`
- `target/release/voice-typer-tauri` (the standalone binary)

**Pass criteria**:
- `cargo check` exits 0 (already verified on Linux dev container)
- `cargo tauri build` produces the `.deb` + `.rpm` installers
- Installing the `.deb` (`sudo dpkg -i ...`) creates a desktop entry
- Launching "Voice Typer" from the app menu shows the React UI

**Common failures**:
- `error: failed to run custom build command for 'gdk-sys'` → Missing `libgtk-3-dev`. Install system deps.
- `error: failed to open icon 'icons/32x32.png'` → Run `python /home/z/my-project/scripts/gen_tauri_icons_stub.py` to generate stub icons (or use the real icons from `scripts/build/generate_icon.py`).
- `error: resource path 'bin/python-sidecar-x86_64-unknown-linux-gnu' doesn't exist` → Create a stub: `touch src-tauri/bin/python-sidecar-x86_64-unknown-linux-gnu` (for dev) or build the real sidecar with Nuitka.

### Wayland notes

- The Tauri WebView (webkit2gtk) works on both X11 and Wayland.
- The native `linux-key-listener` binary uses X11 by default. On Wayland, it falls back to `libinput` (requires root or `setcap CAP_INPUT_EVENT`).
- The tray icon (pystray) works on X11. On Wayland without AppIndicator/SNI support, the tray is skipped (see `tray.py:357-380`).

---

## Windows (x86_64 + aarch64)

**VALIDATE ON WINDOWS HOST**

### Prerequisites

```powershell
# Rust (x86_64 + aarch64 targets)
winget install Rustlang.Rustup
rustup default stable
rustup target add x86_64-pc-windows-msvc
rustup target add aarch64-pc-windows-msvc

# Visual Studio Build Tools (C++ workload — provides cl.exe + link.exe + Windows SDK)
winget install Microsoft.VisualStudio.2022.BuildTools --override "--add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"

# Node.js
winget install OpenJS.NodeJS.LTS

# WebView2 runtime (pre-installed on Windows 11; download for Windows 10)
# https://developer.microsoft.com/microsoft-edge/webview2/
```

### Build steps

```powershell
cd voice-typer

# 1. Build the React renderer
cd voice_typer\client
npm install
npm run build:renderer
cd ..\..

# 2. Build the native hotkey binaries
powershell -ExecutionPolicy Bypass -File scripts\build\compile_native.ps1

# 3. Build the sidecar with Nuitka (see Windows Validation Runbook Step 1)
# This produces src-tauri\bin\python-sidecar-x86_64-pc-windows-msvc.exe

# 4. Build the Tauri host
cd src-tauri
cargo tauri build

# 5. Verify the output
dir target\release\bundle\
# Expected: msi\ and nsis\ directories with the Windows installers
```

**Expected output**:
- `target/release/bundle/msi/Voice Typer_1.0.0_x64_en-US.msi`
- `target/release/bundle/nsis/Voice Typer_1.0.0_x64-setup.exe`
- `target/release/voice-typer-tauri.exe` (the standalone binary)

**Pass criteria**:
- `cargo check` exits 0
- `cargo tauri build` produces the MSI + NSIS installers
- Installing the MSI creates a Start Menu entry
- Launching "Voice Typer" shows the React UI

**Cross-compiling for aarch64 (Windows on ARM)**:
```powershell
rustup target add aarch64-pc-windows-msvc
cargo tauri build --target aarch64-pc-windows-msvc
```

---

## macOS (x86_64 + aarch64)

**VALIDATE ON MACOS HOST**

### Prerequisites

```bash
# Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source ~/.cargo/env

# Xcode Command Line Tools (provides clang + macOS SDK)
xcode-select --install

# Node.js
brew install node@20

# Python 3.12
brew install python@3.12
```

### Build steps

```bash
cd voice-typer

# 1. Build the React renderer
cd voice_typer/client
npm install
npm run build:renderer
cd ../..

# 2. Build the native hotkey binaries
bash scripts/build/compile_native.sh

# 3. Build the sidecar with Nuitka
# (Similar to Windows but with aarch64-apple-darwin / x86_64-apple-darwin targets)
python -m nuitka \
    --standalone \
    --onefile \
    --output-dir=src-tauri/bin \
    --output-filename=python-sidecar-$(rustc -vV | grep host | awk '{print $2}') \
    --include-package=voice_typer.server \
    --include-package=faster_whisper \
    --include-package=enigo \
    --include-package=pystray \
    --include-data-dir=voice_typer/server/native=native \
    -m voice_typer.server.ipc_server

# 4. Build the Tauri host
cd src-tauri
cargo tauri build

# 5. Verify the output
ls -la target/release/bundle/
# Expected: dmg/ and app/ directories with the macOS installers
```

**Expected output**:
- `target/release/bundle/dmg/Voice Typer_1.0.0_x64.dmg`
- `target/release/bundle/macos/Voice Typer.app`
- `target/release/voice-typer-tauri` (the standalone binary)

**Pass criteria**:
- `cargo check` exits 0
- `cargo tauri build` produces the `.dmg` + `.app`
- Opening the `.dmg` + dragging to Applications installs the app
- Launching "Voice Typer" from Launchpad shows the React UI

**Universal binary (x86_64 + aarch64)**:
```bash
rustup target add x86_64-apple-darwin aarch64-apple-darwin
cargo tauri build --target universal-apple-darwin
```

**Signing + notarization** (required for distribution):
```bash
# Set your Developer ID identity
export APPLE_DEVELOPER_ID="Developer ID Application: Your Name (XXXXXXXXXX)"
export APPLE_ID="your@email.com"
export APPLE_PASSWORD="app-specific-password"  # from appleid.apple.com
export APPLE_TEAM_ID="XXXXXXXXXX"

# Build with signing
cargo tauri build --target universal-apple-darwin

# Notarize + staple
xcrun notarytool submit target/release/bundle/dmg/*.dmg \
    --apple-id "$APPLE_ID" \
    --password "$APPLE_PASSWORD" \
    --team-id "$APPLE_TEAM_ID" \
    --wait
xcrun stapler staple target/release/bundle/dmg/*.dmg
```

---

## Dev mode (all platforms)

For fast iteration without rebuilding the Nuitka sidecar:

```bash
# Terminal 1 — start the Python sidecar in WS mode
cd voice-typer
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
VOICE_TYPER_IPC_TOKEN=$(python -c "import secrets; print(secrets.token_hex(32))")
python -m voice_typer.server.ipc_server --ws

# Terminal 2 — start the Tauri dev server
cd voice-typer/src-tauri
VOICE_TYPER_SIDECAR_DEV=1 cargo tauri dev
```

**Note**: The `VOICE_TYPER_SIDECAR_DEV=1` branch is implemented in `src-tauri/src/sidecar/spawn.rs` per ADR-0020 §14 — see `is_dev_mode()` + `spawn_sidecar_dev_mode()`. When the env var is set, the Rust host spawns `python -m voice_typer.server.ipc_server --ws` via `tokio::process::Command` instead of the `externalBin` binary, so dev iteration does NOT require a Nuitka rebuild.

---

## Troubleshooting

### `cargo check` fails with `gdk-sys` / `gtk-sys` errors (Linux)

Install the system deps:
```bash
sudo apt-get install -y libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev libssl-dev libxdo-dev pkg-config
```

### `cargo check` fails with `resource path doesn't exist`

Create stub files for the sidecar + resources (dev only):
```bash
cd src-tauri
mkdir -p bin resources/native
touch bin/python-sidecar-$(rustc -vV | grep host | awk '{print $2}')
touch resources/native/windows-key-listener.exe
touch resources/native/macos-key-listener
touch resources/native/linux-key-listener
for triple in x86_64-pc-windows-msvc aarch64-pc-windows-msvc x86_64-apple-darwin aarch64-apple-darwin x86_64-unknown-linux-gnu aarch64-unknown-linux-gnu; do
    touch "resources/prewarm-${triple}"
done
```

### `cargo tauri build` fails with `no display server found`

The Tauri build requires a display server for the WebView. On a headless server, use `xvfb-run`:
```bash
xvfb-run cargo tauri build
```

### Icons missing

Generate stub icons:
```bash
python /home/z/my-project/scripts/gen_tauri_icons_stub.py
```

Or generate real icons from the SVG:
```bash
cd voice_typer/client
node scripts/generate-icons.mjs
```

---

## Phase 1 Packaging Status (ADR-0020 §4 + §5 + §6.4 + §7 + §13 + §15)

**Last updated**: 2026-07-16 by sub-agent F (`mig1-8-packaging-signing`).

This section tracks what's implemented vs pending for Phase 1 (sidecar
packaging + per-arch externalBin + signing scaffolding). It is the
single source of truth for "is the Tauri packaging pipeline ready to
run on a real host?" — answer: **scaffolded, NOT yet validated**. The
scripts + CI workflows + signing guide exist; the actual Nuitka freeze
runs require per-platform host validation (Phase 0) first.

### Implemented (scaffolding in place)

| Component | Path | Status |
|---|---|---|
| Build orchestrator | `scripts/build/build_tauri_all.sh` | ✅ Local dev wrapper around the per-platform build scripts + `cargo tauri build`. Bash-syntax-verified. |
| Sidecar build (Windows x86_64+aarch64) | `scripts/build/build_sidecar_windows.sh` | ✅ Nuitka `--standalone --onefile` against `python-build-standalone` cpython-3.12.x+x86_64-pc-windows-msvc. Includes ctranslate2/lib + ctranslate2.dll. `--windows-disable-console`. |
| Sidecar build (macOS x86_64+aarch64) | `scripts/build/build_sidecar_macos.sh` | ✅ Nuitka with `--macos-create-bundle --macos-app-mode=background` (LSUIElement=true). Rosetta 2 fallback for x86_64 on Apple Silicon host. |
| Sidecar build (Linux x86_64+aarch64) | `scripts/build/build_sidecar_linux.sh` | ✅ Nuitka with `--onefile-tempdir-spec=$XDG_CACHE_HOME/voice-typer/onefile-tmp`. qemu-user-static cross-build for aarch64 on x86_64 host. glibc ≤ 2.35 baseline check. |
| Prewarm build (Windows) | `scripts/build/build_prewarm_windows.sh` | ✅ Nuitka freeze of `voice_typer/server/prewarm.py` into `resources/prewarm-<triple>.exe`. Separate `--onefile-tempdir-spec` from sidecar (no collision). |
| Prewarm build (macOS) | `scripts/build/build_prewarm_macos.sh` | ✅ Same Nuitka pattern as macOS sidecar; `--macos-app-name=VoiceTyperPrewarm`. |
| Prewarm build (Linux) | `scripts/build/build_prewarm_linux.sh` | ✅ Same Nuitka pattern as Linux sidecar. |
| Native listener build (Windows) | `scripts/build/build_native_listener_windows.sh` | ✅ Wraps `scripts/build/compile_native.ps1` (PowerShell). Copies the compiled `windows-key-listener.exe` into `src-tauri/resources/native/`. |
| Native listener build (macOS) | `scripts/build/build_native_listener_macos.sh` | ✅ Wraps `scripts/build/compile_native.sh` (Swift). Copies + ad-hoc codesigns `macos-key-listener` into `src-tauri/resources/native/`. |
| Native listener build (Linux) | `scripts/build/build_native_listener_linux.sh` | ✅ Wraps `scripts/build/compile_native.sh` (gcc). Copies `linux-key-listener` into `src-tauri/resources/native/`. glibc baseline check. |
| CI aggregator workflow | `.github/workflows/tauri-build.yml` | ✅ `workflow_dispatch` only; fans out to the 3 per-platform workflows via `workflow_call`. NO `latest.json` updater manifest (ADR-0020 §15). |
| CI Windows workflow | `.github/workflows/tauri-windows-build.yml` | ✅ `workflow_call` + `workflow_dispatch`. Job-level `if: false` guard until Phase 0-W passes. Nuitka build + signtool + MSI/NSIS upload. |
| CI macOS workflow | `.github/workflows/tauri-macos-build.yml` | ✅ `workflow_call` + `workflow_dispatch` only (push/PR triggers commented out — ADR-0020 §15 manual-trigger-only). Three jobs (aarch64, x86_64-via-Rosetta, universal). `if: false` per-job guards. |
| CI Linux workflow | `.github/workflows/tauri-linux-build.yml` | ✅ `workflow_call` + `workflow_dispatch` only (push/PR triggers commented out). x86_64 + aarch64 (qemu). `if: false` job guard. glibc baseline check. |
| Tauri config | `src-tauri/tauri.conf.json` | ✅ `bundle.externalBin` uses single base name `bin/python-sidecar` (Tauri v2 appends host triple — NOT per-arch entries). `bundle.resources` lists 3 native + 6 prewarm binaries. `plugins.updater` is ABSENT (ADR-0020 §15). |
| Tauri capabilities | `src-tauri/capabilities/migrate-runtime.json` | ✅ Least-privilege permissions. NO `updater:*` perms. `shell:allow-spawn` scoped to `bin/python-sidecar` via `plugins.shell.scope`. |
| Cargo deps | `src-tauri/Cargo.toml` | ✅ NO `tauri-plugin-updater` dependency. |
| Signing guide | `docs/migration/signing-guide.md` | ✅ Windows Authenticode (signtool + RFC-3161 timestamp + OV/EV cert tradeoff). macOS Developer ID + notarytool + stapler (Info.plist keys + entitlements). Linux unsigned by default. Updater audit results documented. |
| Cutover playbook | `docs/migration/cutover-playbook.md` | ✅ Per-platform cutover criteria (9-point Phase 0 gate + supervisor + side-by-side smoke + signing verification + user sign-off). Per-platform rollback procedure. Mixed-mode period support. |
| PyInstaller fallback | `scripts/build/voice-typer.spec` | ✅ Entry point is `voice_typer/server/ipc_server.py` (the same entry point the Nuitka builds use). Bundles native hotkey binaries + Linux permission scripts + Silero VAD JIT model. Used when Nuitka proves impractical on a target (ADR-0020 §4.5). |
| Stub generator | `scripts/gen_tauri_icons_stub.py` | ✅ Generates RGBA PNGs (color_type=6) — required by Tauri v2 `generate_context!()`. Generates stub sidecar + prewarm + native binaries so `cargo tauri build` dry-runs succeed without real Nuitka artifacts. `--check` mode for CI gates; `--clean` mode preserves real artifacts. |

### Pending (requires per-platform host validation — Phase 0 first)

| Item | Why pending | Gate |
|---|---|---|
| Actual Nuitka freeze run on Windows x86_64 | Need a real Windows host with VS Build Tools + WebView2 + python-build-standalone. Cannot run in headless Linux container. | Phase 0-W validation runbook (`windows-validation-runbook.md`) — all 9 points must pass on a real Windows host. |
| Actual Nuitka freeze run on Windows aarch64 | Windows-on-ARM runner (`windows-11-arm`) is not yet generally available in GitHub Actions. | Either wait for `windows-11-arm` runner, or build on a real Windows-on-ARM device + upload the binary as a release asset. |
| Actual Nuitka freeze run on macOS aarch64 + x86_64 | Need a real macOS host (Apple Silicon + Intel). Nuitka cannot cross-compile between arches. | Phase 0-M validation runbook (`macos-validation-runbook.md`) — both arches on both macOS 13 + macOS 14. |
| Actual Nuitka freeze run on Linux x86_64 | Headless Linux container cannot run `cargo tauri build` (no display server). | Phase 0-L validation runbook (`linux-validation-runbook.md`) — X11 + Wayland. |
| Actual Nuitka freeze run on Linux aarch64 | `ubuntu-22.04-arm` runner not generally available; qemu-user-static path untested. | Either wait for `ubuntu-22.04-arm`, or build on a real Linux-ARM device, or validate the qemu path on a real Linux host. |
| CTranslate2 DLL/dylib/.so set per platform | ADR-0020 §4.2-4.4 mandate enumerating the exact runtime DLL set at build time on each host (libiomp5md.dll, mkl_*.dll, libgomp.so, libiomp5.dylib). | Run `import ctranslate2` in the build env on each platform + enumerate loaded companion DLLs (`listdlls`/`otool -L`/`ldd`). |
| Code-signing end-to-end | Needs real certs + secrets in CI: `WIN_CSC_LINK`/`WIN_CSC_KEY_PASSWORD` (Windows), `MAC_SIGNING_IDENTITY`/`APPLE_ID`/`APPLE_APP_SPECIFIC_PASSWORD`/`APPLE_TEAM_ID` (macOS). | Provision secrets in GitHub Actions per `signing-guide.md` §"Reused signing identities". |
| Per-platform cutover | Each platform must pass its Phase 0 gate + supervisor + side-by-side smoke + signing verification + user acceptance. | `cutover-playbook.md` §"Cutover criteria per platform". |

### PyInstaller fallback path (ADR-0020 §4.5)

If Nuitka proves impractical on a target triple (e.g., macOS Apple
Silicon ABI issues, Linux aarch64 missing wheels), the existing
PyInstaller spec at `scripts/build/voice-typer.spec` is the fallback.
The spec's entry point is **`voice_typer/server/ipc_server.py`** — the
same module the Nuitka builds use — so the wire contract is identical;
only the freeze tool changes.

Caveats:
- PyInstaller `--onedir` produces a folder, not a single file. Tauri
  `externalBin` cannot point at a folder, so the folder must be wrapped:
  - **Windows**: a thin launcher `.exe` that `CreateProcess`es the real
    entrypoint inside the folder.
  - **macOS/Linux**: a shell-script launcher with the executable bit set.
  - The launcher MUST be named with the target-triple suffix
    (`python-sidecar-<triple>[.exe]`).
- The PyInstaller spec already bundles: native hotkey binaries, Linux
  permission scripts, Silero VAD JIT model, corrections/hotkey/model
  JSON, the Windows application manifest (asInvoker), and
  platform-specific hiddenimports (pycaw/comtypes on Win, CoreAudio on
  macOS).
- Build: `pyinstaller scripts/build/voice-typer.spec --noconfirm`
  → `dist/VoiceTyper/VoiceTyper.exe` (windowed, no console).

### Validation commands (re-runnable)

These can be run from the headless dev container to verify the
scaffolding is intact:

```bash
# 1. All build scripts parse.
for f in scripts/build/build_*.sh; do bash -n "$f" || exit 1; done

# 2. All CI workflows are valid YAML.
python -c "
import yaml
for f in ['tauri-build.yml','tauri-windows-build.yml','tauri-macos-build.yml','tauri-linux-build.yml']:
    yaml.safe_load(open(f'.github/workflows/{f}'))
print('YAML OK')
"

# 3. No `updater` references in src-tauri/ (ADR-0020 §15).
grep -rn "updater" src-tauri/   # expected: empty (or only comments)

# 4. Icon stub generator produces RGBA PNGs (color_type=6).
python scripts/gen_tauri_icons_stub.py --check   # exit 0 after generate

# 5. tauri.conf.json externalBin is single base name (NOT per-arch entries).
python -c "
import json
c = json.load(open('src-tauri/tauri.conf.json'))
assert c['bundle']['externalBin'] == ['bin/python-sidecar'], c['bundle']['externalBin']
print('externalBin OK:', c['bundle']['externalBin'])
print('resources count:', len(c['bundle']['resources']))
"

# 6. PyInstaller spec entry point is ipc_server.py (NOT __main__.py).
grep -n 'ipc_server.py' scripts/build/voice-typer.spec
```

### Next actions (for the next round, post-Phase-0 validation)

1. **Run Phase 0 on each platform** — see
   `windows-validation-runbook.md`, `macos-validation-runbook.md`,
   `linux-validation-runbook.md`. File the evidence trail per
   `cutover-playbook.md` §"Evidence trail".
2. **Once Phase 0 passes on a platform**, flip the per-platform CI
   workflow's `if: false` → `if: true` and remove the `if:` guard. Run
   the top-level `tauri-build.yml` orchestrator with `platform:
   <platform>` to produce a signed installer.
3. **Per-platform cutover** — see `cutover-playbook.md` §"Cutover
   procedure". One platform per release; reversible per-platform.
4. **Auto-update (out of scope for v1)** — `tauri-plugin-updater` is
   intentionally NOT wired (ADR-0020 §15). Track as a follow-up ADR
   after the Tauri cutover stabilizes.

### See also

- [`signing-guide.md`](./signing-guide.md) — per-platform signing +
  notarization + the no-auto-update audit.
- [`cutover-playbook.md`](./cutover-playbook.md) — per-platform cutover
  criteria + rollback procedure.
- [`windows-validation-runbook.md`](./windows-validation-runbook.md) /
  [`macos-validation-runbook.md`](./macos-validation-runbook.md) /
  [`linux-validation-runbook.md`](./linux-validation-runbook.md) —
  per-platform Phase 0 validation gates.
- [`../adr/0020-desktop-runtime-migration-analysis.md`](../adr/0020-desktop-runtime-migration-analysis.md)
  — §4 (Nuitka), §5 (prewarm), §6.4 (native listener), §7 (Tauri config),
  §13 (signing), §15 (no auto-update).
