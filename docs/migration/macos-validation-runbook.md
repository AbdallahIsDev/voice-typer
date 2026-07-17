# macOS Validation Runbook — Phase 0-M (ADR-0020)

**Status**: **VALIDATE ON MACOS HOST**. This runbook documents the 9-point Phase 0-M validation gate that must pass on a real macOS host (Apple Silicon AND Intel) with Xcode + Developer ID before the Tauri cutover.

**Prerequisites**:
- macOS 13.0+ (Ventura or newer; matches `PLATFORM_STATUS.md` minimum + ADR-0020 §13.2 `LSMinimumSystemVersion: 13.0`)
- Xcode 15+ command-line tools (`xcode-select --install`) — provides `swiftc` + `clang` + `lipo` + `codesign` + `xcrun`
- Python 3.12.x (for running Nuitka + the dev sidecar)
- Nuitka (installed via `uv pip install nuitka zstandard` in the venv — see §0 below)
- Rust toolchain (`rustup init` → `stable` + `aarch64-apple-darwin` + `x86_64-apple-darwin` targets — both required for `cargo tauri build --target universal-apple-darwin`)
- Node.js 20+ (for the React renderer build)
- Git
- `uv` (Python package installer — faster + more reliable than `pip` for the qwen-asr / ctranslate2 wheels)
- Rosetta 2 (only required if you build the x86_64 binary on an Apple Silicon host)
- Apple Developer ID Application certificate (for distribution signing — see §7)
- Apple notarization credentials: `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID` (for `xcrun notarytool`)

**Time estimate**: 3-5 hours per arch (first run; subsequent runs ~30-45 min with cached deps).

**Per-arch**: Phase 0-M MUST pass on BOTH Apple Silicon (`aarch64-apple-darwin`) AND Intel (`x86_64-apple-darwin`). The two arches are independently revertible per ADR-0020 §Reversibility — you can ship Apple Silicon Tauri while Intel still ships Electron.

---

## Step 0 — Environment setup

**VALIDATE ON MACOS HOST**

```bash
# Install Xcode command-line tools (required for swiftc + codesign)
xcode-select --install

# Install Rosetta 2 (only needed if you'll build x86_64 on an Apple Silicon host)
softwareupdate --install-rosetta --agree-to-license

# Install Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"
rustup default stable
# Add both targets so `cargo tauri build --target universal-apple-darwin` works:
rustup target add aarch64-apple-darwin x86_64-apple-darwin

# Install Node.js 20+ (use nvm or homebrew)
brew install nvm
nvm install 20
nvm use 20

# Install uv
brew install uv

# Clone + enter the repo
git clone https://github.com/AbdallahIsDev/voice-typer.git
cd voice-typer

# Python venv + deps
uv venv
source .venv/bin/activate
uv pip install -e ".[dev,test]"
# Nuitka + zstandard are required for the sidecar / prewarm freeze (§1, §2).
uv pip install nuitka zstandard

# Node deps
cd voice_typer/client
npm install
cd ../..

# Verify the toolchain
which swiftc cargo node python3 uv nuitka
swiftc --version
cargo --version
nuitka --version
```

**Expected output**: All `which` commands resolve; `swiftc --version` prints "swift-driver version: 1.x Apple Swift version 5.x"; `cargo --version` prints "cargo 1.x"; `nuitka --version` prints "Nuitka 2.x".

**Pass criteria**: `swiftc`, `cargo`, `node`, `python3`, `uv`, `nuitka` all on PATH. `lipo -archs /usr/lib/libSystem.B.dylib` should print the host arch.

---

## Step 1 — Nuitka sidecar `.app` builds from `python-build-standalone` (BOTH arches)

**VALIDATE ON MACOS HOST**

The Phase 0-M gate requires this on BOTH Apple Silicon (`aarch64-apple-darwin`) and Intel (`x86_64-apple-darwin`). The build script `scripts/build/build_sidecar_macos.sh <arch>` handles the per-arch Nuitka invocation:

```bash
# Apple Silicon build — run on a macos-14 (Apple Silicon) host.
scripts/build/build_sidecar_macos.sh aarch64
# Expected output:
#   [build_sidecar_macos] SUCCESS
#     Path: /path/to/src-tauri/bin/python-sidecar-aarch64-apple-darwin
#     Size: ~80-120 MB
#     Arch: aarch64 (triple aarch64-apple-darwin)
#     File: .../python-sidecar-aarch64-apple-darwin: Mach-O 64-bit executable arm64

# Intel build — run EITHER on a macos-13 (Intel) host, OR on an Apple Silicon
# host with Rosetta 2 installed (the script auto-prepends `arch -x86_64` and
# passes `--target-arch x86_64` to Nuitka).
scripts/build/build_sidecar_macos.sh x86_64
# Expected output:
#   [build_sidecar_macos] SUCCESS
#     Path: .../src-tauri/bin/python-sidecar-x86_64-apple-darwin
#     Size: ~80-120 MB
#     Arch: x86_64 (triple x86_64-apple-darwin)
#     File: .../python-sidecar-x86_64-apple-darwin: Mach-O 64-bit executable x86_64
```

**Verify the binary runs**:

```bash
# Apple Silicon
file src-tauri/bin/python-sidecar-aarch64-apple-darwin
# Expect: Mach-O 64-bit executable arm64

# Intel
file src-tauri/bin/python-sidecar-x86_64-apple-darwin
# Expect: Mach-O 64-bit executable x86_64

# Smoke-test the sidecar binary (it should start the WS server and emit
# server_started JSON on stdout):
VOICE_TYPER_IPC_TOKEN=test-token \
  ./src-tauri/bin/python-sidecar-aarch64-apple-darwin &
SIDECAR_PID=$!
sleep 3
kill $SIDECAR_PID
# Expected: sidecar prints `[SIDECAR] server_started port=NNNN` to stdout,
# exits cleanly on SIGTERM.
```

**Pass criteria**: Both binaries exist with the correct `file` output (arm64 vs x86_64). The smoke test produces a `server_started` log line within 3 seconds. `otool -L` on each binary shows `libctranslate2.dylib` + `libiomp5.dylib` resolved to onefile-extracted paths.

**Common failures**:
- `error: Rosetta 2 is not installed` (Apple Silicon building x86_64) → install with `softwareupdate --install-rosetta --agree-to-license`.
- `error: cannot build aarch64 on an Intel host via Nuitka directly` → Intel hosts cannot build for Apple Silicon. Use a separate Apple Silicon host (macos-14 CI runner) and merge with `lipo` if you need a universal binary.
- `ImportError: No module named 'pyobjc'` → add `--include-package=pyobjc` (already in the script). If sub-framework bridges fail, run the sidecar once in dev mode and watch for the missing pyobjc-framework-* package, then add `--include-package=pyobjc-framework-Cocoa` etc.
- `dyld: Library not loaded: @rpath/libctranslate2.dylib` → the `--include-data-dir` glob for `ctranslate2/lib` failed. Verify `$SITE/ctranslate2/lib/` exists in the python-build-standalone install and contains `libctranslate2.dylib` + `libiomp5.dylib`.
- `codesign failed: no identity` → set `MAC_SIGNING_IDENTITY="Developer ID Application: <name>"` env var, or accept ad-hoc signing for local dev.

---

## Step 2 — Nuitka prewarm binary builds from `python-build-standalone` (BOTH arches)

**VALIDATE ON MACOS HOST**

Per ADR-0020 §5, the prewarm helper (`voice_typer/server/prewarm.py`) is frozen the same Nuitka way as the sidecar, into `prewarm-<triple>` — but it is a `bundle.resource` (NOT `externalBin`) because it is launched by the macOS LaunchAgent, NOT spawned by Tauri as a managed child.

```bash
# Apple Silicon prewarm build — run on a macos-14 (Apple Silicon) host.
scripts/build/build_prewarm_macos.sh aarch64
# Expected output:
#   [build_prewarm_macos] SUCCESS
#     Path: src-tauri/resources/prewarm-aarch64-apple-darwin
#     Size: ~40-60 MB
#     Arch: aarch64 (triple aarch64-apple-darwin)
#     File: .../prewarm-aarch64-apple-darwin: Mach-O 64-bit executable arm64

# Intel prewarm build — run EITHER on a macos-13 (Intel) host, OR on an
# Apple Silicon host with Rosetta 2 installed.
scripts/build/build_prewarm_macos.sh x86_64
# Expected output:
#   [build_prewarm_macos] SUCCESS
#     Path: src-tauri/resources/prewarm-x86_64-apple-darwin
#     Size: ~40-60 MB
#     Arch: x86_64 (triple x86_64-apple-darwin)
#     File: .../prewarm-x86_64-apple-darwin: Mach-O 64-bit executable x86_64
```

**Verify the binary runs**:

```bash
# Apple Silicon
file src-tauri/resources/prewarm-aarch64-apple-darwin
# Expect: Mach-O 64-bit executable arm64

# Intel
file src-tauri/resources/prewarm-x86_64-apple-darwin
# Expect: Mach-O 64-bit executable x86_64

# Smoke-test the prewarm binary (it should write a [PREWARM] log line
# to stdout and exit 0 within ~30s — long enough to warm torch +
# transformers + ctranslate2 weights).
VOICE_TYPER_PREWARM_SMOKE=1 \
  ./src-tauri/resources/prewarm-aarch64-apple-darwin
# Expected: [PREWARM] starting ...
#           [PREWARM] complete in Ns
#           (exit 0)
```

**Pass criteria**: Both binaries exist with the correct `file` output (arm64 vs x86_64). The smoke test prints `[PREWARM] starting` + `[PREWARM] complete` to stdout and exits 0. The LaunchAgent integration test in §6.7 below exercises the full LaunchAgent path.

**Common failures**:
- `error: Rosetta 2 is not installed` (Apple Silicon building x86_64) → same fix as Step 1.
- `prewarm binary segfaults on launch` → likely a missing ctranslate2 dylib. The script includes `--include-data-dir=$SITE/ctranslate2/lib` — verify the path exists in the build env.
- `ImportError: No module named 'voice_typer.server.prewarm'` → run from the project root (the script cds there), or set `PYTHONPATH=$PWD` before invoking Nuitka.

---

## Step 3 — Native `macos-key-listener` (Swift) build (BOTH archs OR universal)

**VALIDATE ON MACOS HOST**

Per ADR-0020 §6.4, the native `macos-key-listener` binary is built by `scripts/build/compile_native.sh` (which calls `swiftc -O` with Cocoa + CoreGraphics) and copied to `src-tauri/resources/native/macos-key-listener` by `scripts/build/build_native_listener_macos.sh`. The wrapper script supports host-arch, single-arch, and universal (`lipo`-merged) output.

```bash
# Build for the host arch only (faster, sufficient for per-arch validation):
scripts/build/build_native_listener_macos.sh
# Expected output:
#   [build_native_listener_macos] SUCCESS
#     Path: src-tauri/resources/native/macos-key-listener
#     Size: ~XX KB
#     Archs: arm64            # OR x86_64

# Build a universal binary (both archs, merged with lipo):
scripts/build/build_native_listener_macos.sh --universal
# Expected output:
#   [build_native_listener_macos] SUCCESS
#     Path: src-tauri/resources/native/macos-key-listener
#     Size: ~XX KB
#     Archs: arm64 x86_64
```

**Verify the binary**:

```bash
file src-tauri/resources/native/macos-key-listener
# Expected (host-arch): Mach-O 64-bit executable arm64
# Expected (universal): Mach-O universal binary with 2 architectures:
#                       [x86_64:Mach-O 64-bit executable x86_64] [arm64:Mach-O 64-bit executable arm64]

lipo -archs src-tauri/resources/native/macos-key-listener
# Expected (host-arch on Apple Silicon): arm64
# Expected (universal): arm64 x86_64

# Verify the binary is signed (ad-hoc OK for dev, Developer ID for distribution)
codesign -dv src-tauri/resources/native/macos-key-listener
# Expected:
#   Identifier=macos-key-listener
#   TeamIdentifier=not set   (ad-hoc)
#   ... or ...
#   TeamIdentifier=<TEAM_ID> (Developer ID)
```

**Pass criteria**: `file` reports the expected Mach-O arch(s). `codesign -dv` shows a valid signature (ad-hoc or Developer ID). The binary launches and emits `READY` on stdout within 1 second when invoked directly (the full integration test is in §6.8 below):

```bash
# Smoke-test the native listener in isolation (requires Accessibility
# permission for the terminal that spawns it — see ADR-0008 Gap 2).
./src-tauri/resources/native/macos-key-listener &
LISTENER_PID=$!
sleep 1
kill $LISTENER_PID
# Expected: the binary prints `READY` on stdout within 1s.
```

**Common failures**:
- `error: Rosetta 2 is not installed` (Apple Silicon host building `--universal`) → install Rosetta 2 as in Step 0.
- `swiftc: error: unknown target triple: aarch64-apple-macos13.0` → older Xcode (pre-13) does not know the aarch64-apple-macos target. Upgrade to Xcode 15+.
- `lld: warning: ignoring -target arm64-apple-macos13.0` → similar; upgrade Xcode.
- `codesign failed: no identity` → set `MAC_SIGNING_IDENTITY` env var for distribution signing, or accept ad-hoc signing for dev.

---

## Step 4 — Build Tauri host `.app` + `.dmg` (`cargo tauri build --target universal-apple-darwin`)

**VALIDATE ON MACOS HOST**

Tauri v2 selects the right `externalBin` binary by matching the Rust target triple at runtime via `std::env::consts::ARCH` + `std::env::consts::OS`. The host's `main.rs` calls `tauri::api::process::Command::new_sidecar("python-sidecar")` which appends `-<target-triple>` to the binary name automatically.

```bash
# Verify tauri.conf.json uses the single base name (Tauri v2 appends the host
# triple at runtime via std::env::consts::ARCH + std::env::consts::OS).
python3 -c "
import json
c = json.load(open('src-tauri/tauri.conf.json'))
eb = c['bundle']['externalBin']
# Per ADR-0020 §7 + the implementation decision in the worklog: list the
# single base name 'bin/python-sidecar' — Tauri v2 resolves the per-arch
# suffix at runtime. Per-arch entries are NOT listed in externalBin.
assert 'bin/python-sidecar' in eb, 'missing base name bin/python-sidecar'
print('OK: externalBin base name present (Tauri v2 appends the host triple)')

# Verify resources list BOTH macOS arch prewarm binaries + the native listener
res = c['bundle']['resources']
assert 'resources/prewarm-aarch64-apple-darwin' in res, 'missing prewarm-aarch64-apple-darwin'
assert 'resources/prewarm-x86_64-apple-darwin' in res, 'missing prewarm-x86_64-apple-darwin'
assert 'resources/native/macos-key-listener' in res, 'missing native/macos-key-listener'
print('OK: both macOS prewarm arches + native listener present in bundle.resources')
"

# Verify BOTH per-arch sidecar binaries exist on disk (the build step from §1
# must have produced them — Tauri's externalBin resolver will fail at runtime
# if the host-arch binary is missing).
test -f src-tauri/bin/python-sidecar-aarch64-apple-darwin || {
    echo "ERROR: missing src-tauri/bin/python-sidecar-aarch64-apple-darwin — run §1 aarch64 build first"; exit 1; }
test -f src-tauri/bin/python-sidecar-x86_64-apple-darwin || {
    echo "ERROR: missing src-tauri/bin/python-sidecar-x86_64-apple-darwin — run §1 x86_64 build first"; exit 1; }
echo "OK: both per-arch sidecar binaries present on disk"

# Build the Tauri host for the current arch (universal-apple-darwin if both
# targets are installed)
cd src-tauri
cargo tauri build --target universal-apple-darwin
# OR for a single arch:
# cargo tauri build --target aarch64-apple-darwin
# cargo tauri build --target x86_64-apple-darwin

# Verify the built .app + .dmg exist
ls -la target/universal-apple-darwin/release/bundle/dmg/
ls -la target/universal-apple-darwin/release/bundle/macos/
```

**Expected output**: `cargo tauri build` compiles the Rust host (~10-15 min for universal), bundles the sidecar + prewarm + native listener into the `.app`, and produces a `.dmg` at `target/universal-apple-darwin/release/bundle/dmg/Voice Typer_1.0.0_universal.dmg`.

**Pass criteria**: The `.app` and `.dmg` both exist. The `.app/Contents/Resources/` directory contains `prewarm-aarch64-apple-darwin`, `prewarm-x86_64-apple-darwin`, and `macos-key-listener`. The `.app/Contents/MacOS/` (or `Contents/Resources/`) contains the sidecar binary for the host arch.

---

## Step 5 — Install the `.app` + smoke test (gate point 1: sidecar spawn via externalBin)

**VALIDATE ON MACOS HOST**

This is the first of the 9-point Phase 0-M validation gate. Install the built `.app` to `/Applications`, launch it, and verify the Tauri host successfully spawns the sidecar binary via the `externalBin` mechanism.

```bash
# Install: mount the DMG and drag the .app to /Applications.
open "target/universal-apple-darwin/release/bundle/dmg/Voice Typer_1.0.0_universal.dmg"
# In Finder: drag "Voice Typer.app" to /Applications.
# OR script it:
#   hdiutil attach "target/universal-apple-darwin/release/bundle/dmg/Voice Typer_1.0.0_universal.dmg"
#   cp -R "/Volumes/Voice Typer 1.0.0-universal/Voice Typer.app" /Applications/
#   hdiutil detach "/Volumes/Voice Typer 1.0.0-universal"

# First launch: macOS will prompt for Accessibility + Microphone permission
# (TCC). Grant both — see Apple Silicon specific notes below for details.
open "/Applications/Voice Typer.app"

# Verify the sidecar binary spawned via externalBin (gate point 1).
pgrep -lf python-sidecar
# Expected: a line with "python-sidecar-<arch>-apple-darwin" matching the host arch.

# Tail the Tauri log to verify the sidecar emitted server_started.
tail -f "$HOME/Library/Application Support/voice-typer/logs/voice-typer.log"
# Expected lines within 5 seconds of launch:
#   [SIDECAR] spawning externalBin: python-sidecar-aarch64-apple-darwin
#   [SIDECAR] server_started port=NNNN
#   [WS] connected to ws://127.0.0.1:NNNN
#   [AUTH] token accepted
```

**Pass criteria (gate point 1)**: `pgrep -lf python-sidecar` finds the sidecar process matching the host arch (`python-sidecar-aarch64-apple-darwin` on Apple Silicon, `python-sidecar-x86_64-apple-darwin` on Intel). The Tauri log shows `[SIDECAR] spawning externalBin: python-sidecar-<arch>-apple-darwin` followed by `[SIDECAR] server_started port=N` within 5 seconds of launch.

**Common failures**:
- `error: sidecar binary not found` → the host-arch `python-sidecar-<arch>-apple-darwin` is missing from `src-tauri/bin/`. Run §1 for the missing arch.
- `error: permission denied` → the .app was quarantined by Gatekeeper. Run `xattr -dr com.apple.quarantine "/Applications/Voice Typer.app"` for local dev (or sign + notarize per §7).
- `error: macOS blocked the app from opening` → the app is unsigned; right-click → Open → confirm, OR sign + notarize per §7.
- `macOS prompt: "Voice Typer" would like to control this computer using accessibility features` → grant Accessibility permission in System Settings → Privacy & Security → Accessibility. Required for `enigo` + `macos-key-listener` (see §6.4, §6.8 below).

---

## Step 6 — 9-point Phase 0-M validation gate (BOTH arches)

**VALIDATE ON MACOS HOST**

The 9-point gate below is the heart of Phase 0-M. **All 9 must pass on BOTH Apple Silicon AND Intel.** Cutover is per-arch — Apple Silicon can ship Tauri while Intel still ships Electron (ADR-0020 §Reversibility).

Gate point 1 (Sidecar spawn via externalBin) was verified in Step 5 above. The remaining 8 points (2-9) follow as Steps 6.1 through 6.8 below; Step 6.9 is the single-instance gate point.

---

### Step 6.1 — WS + HMAC handshake (gate point 2, BOTH arches)

**VALIDATE ON MACOS HOST**

```bash
# The .app was installed in Step 5. If not already running, launch it:
open "/Applications/Voice Typer.app"

# Tail the Tauri log
tail -f "$HOME/Library/Application Support/voice-typer/logs/voice-typer.log"

# Expected line within 5 seconds of launch:
# [SIDECAR] server_started port=NNNN
# [WS] connected to ws://127.0.0.1:NNNN
# [AUTH] token accepted
```

**Pass criteria (gate point 2)**: The log contains `[SIDECAR] server_started port=N`. The Tauri host's WS client connects to `ws://127.0.0.1:N` and sends the auth frame `{"type":"auth","token":"<64-char-hex>"}`. The sidecar accepts the token and the WS connection stays open (no `[AUTH] token rejected` log line).

To verify HMAC rejection (negative test):

```bash
# Manually connect with a wrong token — sidecar should close the socket.
python3 -c "
import asyncio, websockets, json
async def t():
    async with websockets.connect('ws://127.0.0.1:NNNN') as ws:
        await ws.send(json.dumps({'type': 'auth', 'token': 'wrong'}))
        try:
            print(await asyncio.wait_for(ws.recv(), timeout=2))
        except asyncio.TimeoutError:
            print('OK: socket closed (token rejected)')
asyncio.run(t())
"
```

---

### Step 6.2 — `faster-whisper` transcribes inside the Nuitka bundle (gate point 3, BOTH arches)

**VALIDATE ON MACOS HOST**

```bash
# In the running app:
# 1. Open Settings → Models
# 2. Download a small model (e.g., "tiny" or "base")
# 3. Open the Home page
# 4. Press the dictation hotkey (default: Ctrl+Alt+V — or whatever is set)
# 5. Speak a test phrase ("hello world")
# 6. Release the hotkey

# Verify the transcription appears in the focused text field (TextEdit)
# Verify the transcription appears in History (Settings → History)
```

**Pass criteria**: The transcription text appears in TextEdit within 5 seconds of releasing the hotkey. The History page shows the new entry with the correct model name + device name. The Tauri log shows `[ASR] loaded model tiny in 2.3s` and `[TRANSCRIBE] 0.8s for 3.2s audio`.

**Common failures**:
- `dyld: Library not loaded: libctranslate2.dylib` → the `--include-data-dir=$SITE/ctranslate2/lib=...` flag was missing or the path was wrong. Verify with `otool -L <sidecar-binary>` that `@rpath/libctranslate2.dylib` resolves.
- `libiomp5.dylib not found` → OpenMP runtime missing from the bundle. The script includes `$SITE/ctranslate2/libs` for this; verify the python-build-standalone install actually contains `libiomp5.dylib` under `$SITE/ctranslate2/lib/`.
- `WhisperModel("tiny") segfault on aarch64` → CTranslate2 aarch64 wheel ABI mismatch. Verify the wheel matches the python-build-standalone cpython-3.12.x+aarch64-apple-darwin build.
- Apple Silicon-only: `Operation not permitted` → the `.app` needs the `NSMicrophoneUsageDescription` entitlement in `Info.plist` and the user must grant microphone permission in System Settings → Privacy & Security → Microphone.

---

### Step 6.3 — `enigo` paste (gate point 4, BOTH arches)

**VALIDATE ON MACOS HOST**

```bash
# In the running app:
# 1. Open TextEdit
# 2. Press the dictation hotkey
# 3. Speak a short phrase ("test paste")
# 4. Release the hotkey

# Verify the text appears in TextEdit

# Test long-text path (>300 chars):
# 1. Press the dictation hotkey
# 2. Speak a long phrase (or copy-paste a paragraph of lorem ipsum into the
#    sidecar's clipboard via Settings → Developer → Inject Text)
# 3. Release the hotkey

# Verify the long text appears via clipboard + Cmd+V (not via enigo.text())
```

**Pass criteria**: Short text (<300 chars) is injected via `enigo.text()` directly (uses `CGEventCreateKeyboardEvent` + `CGEventKeyboardSetUnicodeString` on macOS). Long text is copied to the clipboard via `tauri-plugin-clipboard-manager`, then `Cmd+V` is sent via `enigo`, then the previous clipboard contents are restored.

**Required permission**: Accessibility permission for "Voice Typer" in System Settings → Privacy & Security → Accessibility. Without this, `enigo` cannot synthesize keystrokes (CGEvent API requires it). The app should prompt on first launch; if it doesn't, manually add `/Applications/Voice Typer.app` to the Accessibility list.

---

### Step 6.4 — `tauri-plugin-notification` posts a notification (gate point 5, BOTH arches)

**VALIDATE ON MACOS HOST**

macOS 11+ requires `UNUserNotificationCenter` authorization for in-app notifications (ADR-0020 §6.1).

```bash
# In the running app:
# 1. Open Settings → General
# 2. Toggle "Notifications" on
# 3. The app should request notification authorization on first enable:
#    System prompt: "Voice Typer Would Like to Send You Notifications"
# 4. Click "Allow"
# 5. Trigger a notification (e.g., start dictation → stop → "Dictation complete")
```

**Pass criteria**: A macOS notification banner appears in the top-right corner with the Voice Typer icon + the notification text. The notification also appears in Notification Center.

**Required Info.plist keys** (verified in the built `.app/Contents/Info.plist`):
- `NSUserNotificationsUsageDescription`: a human-readable description of why Voice Typer needs notifications.
- `NSMicrophoneUsageDescription`: required for `sounddevice` to access the mic.

```bash
# Verify Info.plist contains the required keys
plutil -p "/Applications/Voice Typer.app/Contents/Info.plist" | grep -E "NSMicrophone|NSUserNotif"
# Expected:
#   "NSMicrophoneUsageDescription" => "Voice Typer needs microphone access for dictation."
#   "NSUserNotificationsUsageDescription" => "Voice Typer posts notifications for dictation events."
```

If the keys are missing, the sidecar `Info.plist` (set via `--macos-signed-app-name=com.voicetyper.sidecar` in the Nuitka command) must be merged with the host's `Info.plist` at bundle time. The host `Info.plist` is the canonical one for the `.app` bundle.

---

### Step 6.5 — Cooperative shutdown (gate point 6, BOTH arches)

**VALIDATE ON MACOS HOST**

```bash
# In the running app:
# 1. Close the main window (click the red close button, or Cmd+Q)
# 2. Verify the sidecar exits within 2 seconds

# Check Activity Monitor or pgrep for "python-sidecar"
pgrep -lf python-sidecar
# Expected: no output (no sidecar process running)

# Check the Tauri log
tail -20 "$HOME/Library/Application Support/voice-typer/logs/voice-typer.log"
# Expected lines:
#   [SHUTDOWN] sending {"type":"shutdown"}
#   [SHUTDOWN] sidecar exited cleanly in 0.3s
```

**Pass criteria**: `pgrep -lf python-sidecar` returns nothing within 2 seconds of closing the main window. The Tauri log shows `[SHUTDOWN] sidecar exited cleanly`.

**Hard-kill backstop test** (verify `kill_children` cleans on a hung sidecar):

```bash
# In dev mode (VOICE_TYPER_SIDECAR_DEV=1), inject a hung sidecar by
# attaching to it with lldb and pausing it:
SIDECAR_PID=$(pgrep -f python-sidecar)
lldb -p $SIDECAR_PID -o "process interrupt" -o "process pause" -o "quit"

# Close the main window → the cooperative shutdown will time out after 2s,
# Rust should then force-kill the process tree.
sleep 3
pgrep -lf python-sidecar
# Expected: no output (killed by kill_children)
```

---

### Step 6.6 — Prewarm LaunchAgent (gate point 7, BOTH arches)

**VALIDATE ON MACOS HOST**

```bash
# In the running app:
# 1. Open Settings → General
# 2. Enable "Prewarm on login"
# 3. Sign out (Apple menu → Log Out) and sign back in

# Verify the LaunchAgent was registered
ls -la "$HOME/Library/LaunchAgents/com.voicetyper.prewarm.plist"
plutil -p "$HOME/Library/LaunchAgents/com.voicetyper.prewarm.plist"

# Verify the LaunchAgent ran
launchctl list | grep voicetyper
# Expected: a line like:
#   -  0  com.voicetyper.prewarm

# Verify the prewarm log
cat "$HOME/Library/Application Support/voice-typer/logs/prewarm.log" | tail -10
# Expected:
#   [PREWARM] starting (trigger=RunAtLoad)
#   [PREWARM] free RAM: 12.3 GB (budget: 6 GB) — OK
#   [PREWARM] warming torch (4.2 GB) ... done in 8.2s
#   [PREWARM] warming transformers (1.1 GB) ... done in 2.1s
#   [PREWARM] warming Parakeet weights (2.4 GB) ... done in 4.5s
#   [PREWARM] complete in 14.8s
```

**Pass criteria**: `~/Library/LaunchAgents/com.voicetyper.prewarm.plist` exists with `RunAtLoad=true` and `ProgramArguments` pointing at `<resourceDir>/prewarm-<triple>` (the frozen Nuitka binary). The prewarm log shows a successful run on login. The `resolve_prewarm_exe()` resolver in `voice_typer/server/prewarm_resolver.py` returns the frozen binary path (not the dev fallback).

**Verify the resolver picks the right arch**:

```bash
# On Apple Silicon, _target_triple() must return 'aarch64-apple-darwin'
# (NOT 'arm64-apple-darwin' — Tauri's externalBin suffix uses the Rust
# triple naming convention).
python3 -c "
from voice_typer.server.prewarm_resolver import _target_triple
print(_target_triple())
"
# Expected on Apple Silicon: aarch64-apple-darwin
# Expected on Intel:         x86_64-apple-darwin
```

---

### Step 6.7 — Native `macos-key-listener` (Swift) toggles dictation (gate point 8, BOTH arches)

**VALIDATE ON MACOS HOST**

The native `macos-key-listener` binary is built by `scripts/build/compile_native.sh` (which calls `swiftc -O` with Cocoa + CoreGraphics) and copied to `src-tauri/resources/native/macos-key-listener` by `scripts/build/build_native_listener_macos.sh` (see §3 above for the build instructions).

```bash
# Build the native listener (run on each arch separately, or merge with lipo)
scripts/build/build_native_listener_macos.sh
# Expected output:
#   [build_native_listener_macos] SUCCESS
#     Path: src-tauri/resources/native/macos-key-listener
#     Size: ~XX KB
#     Archs: arm64   # OR x86_64 OR arm64 x86_64 (universal)

# Verify the binary
file src-tauri/resources/native/macos-key-listener
# Expected (single arch): Mach-O 64-bit executable arm64
# Expected (universal):   Mach-O 64-bit executable arm64 x86_64

# In the running app:
# 1. Open Settings → Hotkey
# 2. Set a custom hotkey (e.g., F8)
# 3. Focus a text field (e.g., TextEdit)
# 4. Press F8
# 5. Speak a test phrase
# 6. Press F8 again

# Verify the dictation toggles correctly
pgrep -lf macos-key-listener
# Expected while dictating: a line with the binary name
```

**Pass criteria**: Pressing F8 starts recording (the bubble appears with "Listening…"). Pressing F8 again stops recording and pastes the transcription. The native `macos-key-listener` process is visible in Activity Monitor while the app is running.

**Accessibility permission** (ADR-0008 Gap 2): the sidecar + native listener need Accessibility permission to synthesize keystrokes. On first launch the app should prompt; if not, manually add `Voice Typer.app` in System Settings → Privacy & Security → Accessibility.

```bash
# Verify the binary is signed (ad-hoc OK for dev, Developer ID for distribution)
codesign -dv src-tauri/resources/native/macos-key-listener
# Expected:
#   Identifier=macos-key-listener
#   TeamIdentifier=not set   (for ad-hoc)
#   ... or ...
#   TeamIdentifier=<TEAM_ID> (for Developer ID)
```

---

### Step 6.8 — Single-instance enforcement (gate point 9, BOTH arches)

**VALIDATE ON MACOS HOST**

Per ADR-0020 §12, the Tauri host uses `tauri-plugin-single-instance` to enforce that only one Voice Typer process is running at a time. A second launch must NOT spawn a second sidecar — instead, the second instance forwards its argv to the first (typically focusing the existing main window) and exits immediately.

```bash
# 1. Launch the app for the first time:
open "/Applications/Voice Typer.app"
sleep 3

# 2. Verify exactly ONE main process + ONE sidecar process are running:
pgrep -lf "Voice Typer.app/Contents/MacOS/Voice Typer" | wc -l
# Expected: 1

pgrep -lf python-sidecar | wc -l
# Expected: 1

# 3. Launch the app a second time:
open "/Applications/Voice Typer.app"
sleep 2

# 4. Verify still ONE process + ONE sidecar (NOT two):
pgrep -lf "Voice Typer.app/Contents/MacOS/Voice Typer" | wc -l
# Expected: 1   (the second launch forwarded to the first instance and exited)

pgrep -lf python-sidecar | wc -l
# Expected: 1

# 5. Verify the second launch was logged as a single-instance rejection:
tail -20 "$HOME/Library/Application Support/voice-typer/logs/voice-typer.log" | grep -i "single.instance\|already.running\|second.instance"
# Expected: a line like "[SINGLE_INSTANCE] second instance rejected, focusing main window"
```

**Pass criteria (gate point 9)**: After the second `open` invocation, the process counts remain at 1+1 (not 2+2). The Tauri log shows the single-instance plugin intercepted the second launch. The first instance's main window comes to the foreground.

**Common failures**:
- `pgrep shows 2 main processes` → the `tauri-plugin-single-instance` plugin is not initialized in `main.rs`. Verify `app.handle().plugin(tauri_plugin_single_instance::init(...))` is called before `app.run(...)`.
- `pgrep shows 2 sidecar processes` → the second Tauri instance started a sidecar before the single-instance plugin killed it. The plugin MUST be initialized BEFORE `spawn_sidecar_and_get_port`. Verify the order in `main.rs` (gate at entry, per ADR-0020 §12).
- `Second launch does nothing (no focus, no log line)` → the single-instance plugin forwarded the event but the main window handler didn't call `window.show() + window.set_focus()`. Verify the `init` closure handles the argv + focuses the main window.

---

### Step 6.9 — New Rust commands validation (export_history, export_vocabulary, bubble_*)

**VALIDATE ON MACOS HOST**

The MIG-1.1+1.2 wave adds new Rust commands (`export_history`, `export_vocabulary`, `bubble_show`, `bubble_signal_ready`, `bubble_set_position`, `bubble_set_draggable`, `bubble_move_by`, `bubble_hide_complete`) to the Tauri host's `generate_handler!` macro. These commands are dispatched over the same WS bridge as the existing 68 sidecar commands. Validate each one works end-to-end on macOS.

#### export_history

```bash
# In the running app:
# 1. Ensure at least one transcription exists in History (run §6.2 first).
# 2. Open Settings → History → click "Export…"
# 3. The tauri-plugin-dialog save dialog appears.
# 4. Choose a path (e.g., ~/Desktop/voice-typer-history.json).
# 5. Click "Save".

# Verify the file exists + is valid JSON:
test -f ~/Desktop/voice-typer-history.json
python3 -c "import json; data=json.load(open('$HOME/Desktop/voice-typer-history.json')); print(f'OK: {len(data)} entries')"
```

**Pass criteria**: The save dialog appears, the file is written, the file is valid JSON containing the history entries. If the user cancels the save dialog, no file is written and no error is logged.

#### export_vocabulary

```bash
# In the running app:
# 1. Add a custom word to the vocabulary (Settings → Vocabulary → Add).
# 2. Click "Export…"
# 3. Choose a path (e.g., ~/Desktop/voice-typer-vocab.json).
# 4. Click "Save".

test -f ~/Desktop/voice-typer-vocab.json
python3 -c "import json; data=json.load(open('$HOME/Desktop/voice-typer-vocab.json')); print(f'OK: {len(data)} words')"
```

**Pass criteria**: Same as `export_history` — save dialog → file written → valid JSON.

#### bubble_show / bubble_signal_ready / bubble_set_position / bubble_set_draggable / bubble_move_by / bubble_hide_complete

The bubble window is declared in `tauri.conf.json` (label `"bubble"`, 240×80, `alwaysOnTop: true`, `transparent: true`, `decorations: false`, `visible: false`). The 6 `bubble_*` commands orchestrate showing/hiding the dictation bubble.

```bash
# 1. Start dictation (press the hotkey — default F8 or Fn).
# 2. The bubble window appears near the cursor.
# 3. Verify the bubble window is visible + alwaysOnTop:
osascript -e 'tell application "System Events" to count (windows of (every process whose name contains "Voice Typer"))'
# Expected: at least 2 (main + bubble) while dictating

# 4. Drag the bubble — verify bubble_set_draggable + bubble_move_by work:
#    - The bubble should follow the cursor while dragging.
#    - The bubble should snap to the new position after release.

# 5. Stop dictation (press the hotkey again).
# 6. The bubble hides (bubble_hide_complete).

# 7. Verify in the Tauri log:
tail -20 "$HOME/Library/Application Support/voice-typer/logs/voice-typer.log" | grep -i bubble
# Expected lines:
#   [BUBBLE] show (pos=NNN,NNN)
#   [BUBBLE] signal_ready
#   [BUBBLE] set_position (x=NNN, y=NNN)
#   [BUBBLE] set_draggable (true)
#   [BUBBLE] move_by (dx=NN, dy=NN)
#   [BUBBLE] hide_complete
```

**Pass criteria**: All 6 `bubble_*` commands log their invocation. The bubble window appears + disappears at the right times. The bubble follows the cursor while dragging. Per ADR-0020 §9, `bubble_level` events are throttled (coalesced to ≤30Hz) so the log should NOT show more than 30 `bubble_*` events per second.

**Common failures**:
- `invoke('bubble_show') returns "window not found"` → the bubble window label is missing from `tauri.conf.json`'s `app.windows` array. Verify the label `"bubble"` exists.
- `Bubble appears but doesn't follow cursor` → `bubble_set_draggable(true)` was not called before `bubble_move_by`. Verify the bubble command sequence in `main.rs`.
- `Bubble stays on screen after dictation stops` → `bubble_hide_complete` was not invoked, or `window.hide()` failed. Check the Tauri log for `window.hide()` errors.

---

## Step 7 — Code signing + notarization + stapling (ADR-0020 §13.2)

**VALIDATE ON MACOS HOST**

This step is REQUIRED for distribution (a `.dmg` with an unsigned `.app` will be rejected by Gatekeeper on user machines). For local dev, ad-hoc signing (Steps 1 + 3) is sufficient to run the app, but Phase 0-M distribution validation requires full Developer ID + notarization + stapling.

### 7.1 Prerequisites

- Apple Developer Program membership ($99/year)
- "Developer ID Application" certificate in Keychain Access (under "My Certificates")
- App-specific password for `xcrun notarytool` (generate at https://appleid.apple.com → Sign-In and Security → App-Specific Passwords)
- Set env vars:
  ```bash
  export MAC_SIGNING_IDENTITY="Developer ID Application: Your Name (TEAM_ID)"
  export APPLE_ID="you@example.com"
  export APPLE_APP_SPECIFIC_PASSWORD="xxxx-xxxx-xxxx-xxxx"
  export APPLE_TEAM_ID="TEAM_ID"
  ```

### 7.2 Sign the sidecar + prewarm + native listener

The build scripts (`build_sidecar_macos.sh`, `build_prewarm_macos.sh`, `build_native_listener_macos.sh`) automatically sign with Developer ID when `MAC_SIGNING_IDENTITY` is set. Verify:

```bash
codesign -dv --verbose=4 src-tauri/bin/python-sidecar-aarch64-apple-darwin
# Expected:
#   Identifier=VoiceTyperSidecar
#   TeamIdentifier=<TEAM_ID>
#   Authority=Developer ID Application: Your Name (TEAM_ID)
#   ...

codesign -dv --verbose=4 src-tauri/resources/prewarm-aarch64-apple-darwin
codesign -dv --verbose=4 src-tauri/resources/native/macos-key-listener
```

### 7.3 Build + sign the .app bundle

```bash
# cargo tauri build with universal target + Developer ID signing
cd src-tauri
cargo tauri build --target universal-apple-darwin

# The .app is at:
#   target/universal-apple-darwin/release/bundle/macos/Voice Typer.app
APP_PATH="target/universal-apple-darwin/release/bundle/macos/Voice Typer.app"

# Verify the .app is signed (Tauri v2 calls codesign automatically when
# tauri.conf.json bundle.macOS.signingIdentity is set; if not, sign manually:
# codesign --force --deep --options runtime --sign "$MAC_SIGNING_IDENTITY" "$APP_PATH")
codesign --verify --deep --strict --verbose=2 "$APP_PATH"
# Expected: "Voice Typer.app: valid on disk"
```

### 7.4 Notarize the .app

```bash
# Submit to Apple's notarization service. This typically takes 2-10 minutes.
ZIP_PATH="/tmp/VoiceTyper.zip"
ditto -c -k --keepParent "$APP_PATH" "$ZIP_PATH"
xcrun notarytool submit "$ZIP_PATH" \
  --apple-id "$APPLE_ID" \
  --password "$APPLE_APP_SPECIFIC_PASSWORD" \
  --team-id "$APPLE_TEAM_ID" \
  --wait
# Expected:
#   Submission ID: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
#   ...
#   status: Accepted
```

### 7.5 Staple the notarization ticket

```bash
xcrun stapler staple "$APP_PATH"
# Expected: "The staple and validate action worked!"

# Verify
xcrun stapler validate "$APP_PATH"
# Expected: "The validate action worked!"
```

### 7.6 Build + sign + notarize + staple the .dmg

```bash
# Tauri's bundler produces the .dmg from the stapled .app
DMG_PATH="target/universal-apple-darwin/release/bundle/dmg/Voice Typer_1.0.0_universal.dmg"

# Sign the .dmg
codesign --force --sign "$MAC_SIGNING_IDENTITY" "$DMG_PATH"

# Notarize the .dmg
xcrun notarytool submit "$DMG_PATH" \
  --apple-id "$APPLE_ID" \
  --password "$APPLE_APP_SPECIFIC_PASSWORD" \
  --team-id "$APPLE_TEAM_ID" \
  --wait

# Staple the .dmg
xcrun stapler staple "$DMG_PATH"
xcrun stapler validate "$DMG_PATH"
```

**Pass criteria**: Both the `.app` and the `.dmg` pass `xcrun stapler validate`. On a clean Mac (no Developer ID cert in Keychain), double-clicking the `.dmg` and dragging the `.app` to /Applications produces no Gatekeeper warning — the app launches directly.

### 7.7 Hardened runtime + entitlements

The `.app`'s `Info.plist` must declare:
- `CFBundleIdentifier`: `com.voicetyper.app` (matches `tauri.conf.json` identifier)
- `LSMinimumSystemVersion`: `13.0`
- `LSUIElement`: `false` (the main app shows in the Dock)
- `NSMicrophoneUsageDescription`: required for `sounddevice` mic access
- `NSUserNotificationsUsageDescription`: required for `tauri-plugin-notification` on macOS 11+

The hardened runtime entitlements (`com.apple.security.cs.*`) are required for notarization:
- `com.apple.security.cs.allow-jit`: required if CTranslate2 uses JIT (verify with `otool -L` for libctranslate2)
- `com.apple.security.cs.disable-library-validation`: required if Nuitka's `--onefile` extracts unsigned dylibs at runtime (verify by running the sidecar binary and watching `log show --predicate 'process == "python-sidecar"'` for `library validation` errors)

The Tauri `bundle.macOS.entitlements` config in `tauri.conf.json` should point at an `entitlements.plist` file declaring these keys. (This is a Tauri host concern — see the Tauri host runbook for the entitlements file template.)

---

## Step 8 — Rollback to Electron

**VALIDATE ON MACOS HOST**

Per ADR-0020 §Reversibility: macOS Tauri cutover is independently revertible. To roll back:

```bash
# 1. Uninstall the Tauri .app
sudo rm -rf "/Applications/Voice Typer.app"

# 2. Remove the LaunchAgent (if registered)
launchctl unload "$HOME/Library/LaunchAgents/com.voicetyper.prewarm.plist" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/com.voicetyper.prewarm.plist"

# 3. The user data at ~/Library/Application Support/voice-typer/ stays intact
#    (it's shared with the Electron fallback). Confirm:
ls -la "$HOME/Library/Application Support/voice-typer/"
# Expected: config.json, models/, history.db, logs/, etc.

# 4. Reinstall the Electron build from the prior release DMG.
#    (See voice_typer/client/electron-builder.yml for the Electron .dmg path.)
```

**Pass criteria**: After uninstall, `~/Library/LaunchAgents/com.voicetyper.prewarm.plist` is gone, `~/Library/Application Support/voice-typer/` is intact, and the Electron build launches with the same config + history + models.

---

## Step 9 — Capture results

**VALIDATE ON MACOS HOST**

After all 9 gate points (Step 6.1-6.9) + signing (Step 7) + rollback (Step 8) pass, capture the results:

```bash
# Capture system info
sw_vers
uname -a
sysctl -n machdep.cpu.brand_string   # Intel: "Intel(R) Core(TM)..."
sysctl -n machdep.cpu.brand_string   # Apple Silicon: "Apple M1/M2/M3..."
rustc --version
swiftc --version
node --version
python3 --version

# Capture binary info
file src-tauri/bin/python-sidecar-{aarch64,x86_64}-apple-darwin
file src-tauri/resources/prewarm-{aarch64,x86_64}-apple-darwin
file src-tauri/resources/native/macos-key-listener

# Capture signing info
codesign -dv src-tauri/bin/python-sidecar-aarch64-apple-darwin
codesign -dv src-tauri/bin/python-sidecar-x86_64-apple-darwin

# Capture notarization info
xcrun stapler validate "target/universal-apple-darwin/release/bundle/macos/Voice Typer.app"
xcrun stapler validate "target/universal-apple-darwin/release/bundle/dmg/Voice Typer_1.0.0_universal.dmg"

# Capture file sizes
du -h src-tauri/bin/python-sidecar-{aarch64,x86_64}-apple-darwin
du -h src-tauri/resources/prewarm-{aarch64,x86_64}-apple-darwin
du -h "target/universal-apple-darwin/release/bundle/dmg/Voice Typer_1.0.0_universal.dmg"

# Save results to a Phase 0-M report file
mkdir -p docs/migration/reports
REPORT="docs/migration/reports/phase-0m-$(date -u +%Y%m%d).md"
{
  echo "# Phase 0-M Validation Report"
  echo ""
  echo "**Date**: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "**Host arch**: $(uname -m)"
  echo "**macOS version**: $(sw_vers -productVersion)"
  echo ""
  echo "## Checklist"
  echo ""
  echo "| # | Check | Pass? | Notes |"
  echo "|---|---|---|---|"
  echo "| 1 | Nuitka sidecar builds (both arches) | ✅ | sizes: ... |"
  echo "| 2 | externalBin spawns via Tauri | ✅ | ... |"
  # ... etc
} > "$REPORT"
echo "Wrote $REPORT"
```

---

## Summary checklist

| Step | Check | Pass criteria |
|---|---|---|
| §1 | Nuitka macOS sidecar builds (BOTH arches) | `python-sidecar-{aarch64,x86_64}-apple-darwin` exist with correct Mach-O arch (~80-120 MB each) |
| §2 | Nuitka macOS prewarm builds (BOTH arches) | `prewarm-{aarch64,x86_64}-apple-darwin` exist with correct Mach-O arch (~40-60 MB each) |
| §3 | Native `macos-key-listener` (Swift) build | `macos-key-listener` exists with correct Mach-O arch (host-arch OR universal) |
| §4 | Tauri `.app` + `.dmg` build (`--target universal-apple-darwin`) | `cargo tauri build` produces both `.app` + `.dmg` with both prewarm arches + native listener in `Contents/Resources/` |
| §5 | Install + smoke test (gate point 1: sidecar spawn) | `pgrep -lf python-sidecar` finds the host-arch sidecar within 5s of launch |
| §6.1 | WS + HMAC handshake (gate point 2) | `[SIDECAR] server_started port=N` in log; wrong token rejected |
| §6.2 | `faster-whisper` transcribes (gate point 3) | Transcription appears in TextEdit within 5s; `libctranslate2.dylib` + `libiomp5.dylib` resolve |
| §6.3 | `enigo` paste (gate point 4) | Short text via `enigo.text()`; long text via clipboard + `Cmd+V`; Accessibility permission granted |
| §6.4 | `tauri-plugin-notification` (gate point 5) | macOS notification banner appears; `Info.plist` has `NSUserNotificationsUsageDescription` + `NSMicrophoneUsageDescription` |
| §6.5 | Cooperative shutdown (gate point 6) | Sidecar exits within 2s of window close; `kill_children` backstop verified on hung sidecar |
| §6.6 | Prewarm LaunchAgent (gate point 7) | `~/Library/LaunchAgents/com.voicetyper.prewarm.plist` registered with `RunAtLoad=true`; `resolve_prewarm_exe()` returns frozen binary path |
| §6.7 | Native `macos-key-listener` toggle (gate point 8) | F8 starts/stops dictation; Accessibility permission granted; binary signed |
| §6.8 | Single-instance (gate point 9) | Second launch forwards to first; only 1 main + 1 sidecar process |
| §6.9 | New Rust commands (export_history, export_vocabulary, bubble_*) | All 8 new commands dispatch + log correctly; bubble appears + hides; exports write valid JSON |
| §7 | Codesign + notarize + staple (BOTH arches) | `.app` + `.dmg` pass `xcrun stapler validate`; Gatekeeper accepts on clean Mac |
| §8 | Rollback | Uninstall + Electron reinstall preserves user data |

**All 9 gate points (§6.1-§6.9) must pass on BOTH Apple Silicon AND Intel before the macOS Tauri cutover.** §7 (signing/notarization) is required for distribution but optional for local validation. §8 (rollback) verifies the safety net. Electron remains the fallback until all gate points pass. Cutover is per-arch — Apple Silicon can ship Tauri while Intel still ships Electron.

---

## Apple Silicon specific notes

- **Rosetta 2 for x86_64 builds**: An Apple Silicon host can build x86_64 binaries via Rosetta 2 (`arch -x86_64` prefix). The `build_sidecar_macos.sh`, `build_prewarm_macos.sh`, and `build_native_listener_macos.sh` scripts auto-detect this and prepend the prefix when the host is `arm64` and the target arch is `x86_64`. Install Rosetta 2 with `softwareupdate --install-rosetta --agree-to-license`.
- **Intel hosts cannot build aarch64 via Nuitka**: Nuitka has no `--target-arch arm64` flag on Intel macOS. To produce an aarch64 binary, run `build_sidecar_macos.sh aarch64` (or `build_prewarm_macos.sh aarch64`) on a separate Apple Silicon host (macos-14 CI runner).
- **Universal binary via `lipo`**: After building both arches separately, merge into a single universal binary. The `build_native_listener_macos.sh --universal` flag does this for the Swift listener. For the sidecar + prewarm, Nuitka cannot produce a universal binary directly — but Tauri's `externalBin` mechanism selects the right per-arch binary at runtime via `std::env::consts::ARCH`, so a universal binary is NOT required. Example for the native listener:
  ```bash
  lipo -create \
    src-tauri/resources/native/macos-key-listener.aarch64 \
    src-tauri/resources/native/macos-key-listener.x86_64 \
    -output src-tauri/resources/native/macos-key-listener
  file src-tauri/resources/native/macos-key-listener
  # Expected: Mach-O universal binary with 2 architectures: [x86_64:Mach-O 64-bit executable x86_64] [arm64:Mach-O 64-bit executable arm64]
  ```
  Note: `build_native_listener_macos.sh --universal` does this in one step (no manual lipo needed).
- **`cargo tauri build --target universal-apple-darwin`** builds a universal `.app` bundle that runs natively on both arches. The Rust host is universal, but the Python sidecar binary inside is selected per-arch by Tauri at runtime from the `bundle.externalBin` list (Tauri appends the host triple to the base name `bin/python-sidecar`).
- **CTranslate2 aarch64 wheels are CPU-only** (no CUDA on macOS). Verify with `otool -L $SITE/ctranslate2/lib/libctranslate2.dylib` that every `@rpath` dependency resolves. Apple Silicon wheels ship `libctranslate2.dylib` + `libiomp5.dylib` (OpenMP) — no CUDA, no cuBLAS.
- **`pyobjc` framework bridges**: Nuitka's `--include-package=pyobjc` does not always pick up the framework sub-packages (`pyobjc-framework-Cocoa`, `pyobjc-framework-CoreAudio`, etc.). If the sidecar crashes on launch with `ImportError: pyobjc-...`, add explicit `--include-package=pyobjc-framework-Cocoa` flags to the Nuitka command in `build_sidecar_macos.sh`. The build scripts already include `pyobjc-framework-Cocoa` + `pyobjc-framework-CoreAudio` defensively.
- **Accessibility permission (TCC)**: `enigo` + the native `macos-key-listener` both require Accessibility permission. The app should prompt on first launch via `AXIsProcessTrustedWithOptions`; if it doesn't, manually add `/Applications/Voice Typer.app` in System Settings → Privacy & Security → Accessibility. ADR-0008 Gap 2 covers the onboarding flow for this. **Important: in dev mode (`cargo tauri dev`), the terminal that spawned the binary (Terminal.app / iTerm / VS Code) ALSO needs Accessibility permission — the permission does NOT transfer to the binary alone.**
- **Microphone permission (TCC)**: `sounddevice` requires microphone permission. The `Info.plist` must declare `NSMicrophoneUsageDescription`; on first mic open the system prompts the user. Denied mic → sidecar logs `[AUDIO] microphone permission denied` and the WS connection stays open (no crash), but dictation silently fails.
- **Notification permission (TCC, macOS 11+)**: `tauri-plugin-notification` requires `UNUserNotificationCenter.requestAuthorization(...)` to be called on first launch. Without it, notifications silently no-op. The Tauri host should call this once on startup (or on first `notification:allow-notify` invoke). The `Info.plist` must declare `NSUserNotificationsUsageDescription`. Verified in §6.4.
- **`--onefile` temp-dir on macOS**: Nuitka `--onefile` extracts to `$TMPDIR/onefile_*` on every launch. The build script pins it to `$HOME/Library/Application Support/voice-typer/onefile-tmp` via `--onefile-tempdir-spec` so stale extracts don't accumulate in `$TMPDIR`. The LaunchAgent's prewarm binary uses a separate `prewarm-tmp` dir to avoid contention.
- **`LSUIElement=true` for the sidecar**: The Nuitka `--macos-app-mode=background` flag sets `LSUIElement=true` in the sidecar's bundle `Info.plist` — the sidecar runs with no Dock icon, no menu bar item. This is the macOS equivalent of Windows `--windows-disable-console`. The main `.app` (the Tauri host) keeps `LSUIElement=false` so it shows in the Dock normally.
- **`_target_triple()` returns `aarch64-apple-darwin` (NOT `arm64-apple-darwin`)**: ADR-0020 §4.1 explicitly lists `aarch64-apple-darwin` as the macOS Apple Silicon target triple. The Rust toolchain + Tauri's externalBin mechanism use `aarch64-`, not `arm64-`. The `prewarm_resolver._target_triple()` function in `voice_typer/server/prewarm_resolver.py` correctly returns `aarch64-apple-darwin` on Apple Silicon (line 90: `arch = "aarch64" if machine == "arm64" else "x86_64"`). The original ADR §5 code snippet had a bug returning `arm64-apple-darwin` — the implementation is correct, the ADR snippet is not. The `tests/tauri/test_prewarm_resolver.py::test_target_triple_apple_silicon_returns_aarch64` test guards against regression.
