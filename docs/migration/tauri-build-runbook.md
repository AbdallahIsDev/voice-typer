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

**Note**: The `VOICE_TYPER_SIDECAR_DEV=1` branch is documented in ADR-0020 §14 but not yet implemented in `main.rs`. It's a ~15-line addition for the next round. Until then, dev mode requires the Nuitka-built sidecar.

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
