# Linux Validation Runbook — Phase 0-L (ADR-0020)

**Status**: VALIDATE ON LINUX DISPLAY HOST. This runbook documents the 9-point Phase 0-L validation gate that must pass on a real Linux desktop (X11 AND Wayland, on both x86_64 and aarch64) before the Tauri cutover. The build steps run on any Linux host; the smoke-test steps that require a display server (Tauri WebView, paste keystroke, native hotkey toggle, libnotify toast) MUST be run on a host with an active graphical session.

**Scope**: Linux X11 + Wayland, both `x86_64-unknown-linux-gnu` and `aarch64-unknown-linux-gnu`. Cross-arch (aarch64) packages can be built on an x86_64 host using `python-build-standalone` + `qemu-user-static`, but the smoke tests MUST run on the matching arch.

**Reversibility**: Electron remains the shippable Linux fallback until ALL 9 points pass on BOTH X11 AND Wayland. Per ADR-0020 §"Reversibility", reverting one platform does NOT revert the others — Linux Tauri cutover is independent of Windows / macOS.

---

## Prerequisites

- **OS**: Ubuntu 22.04+ (glibc 2.35+) or Fedora 38+ (glibc 2.38+). The Nuitka sidecar binary is built against the glibc 2.35 baseline (per ADR-0020 §4.4) so it runs on Ubuntu 22.04+ / Debian 12+ / Fedora 36+. Ubuntu 20.04 (glibc 2.31) is the absolute floor — the binary will load but is unsupported.
- **Python**: 3.12.x (for Nuitka + the dev sidecar). The python-build-standalone release used by the build scripts pins to 3.12.x — do NOT use 3.13+ yet (CTranslate2 / faster-whisper wheel tags don't all match).
- **Nuitka**: installed in the build interpreter env (`pip install nuitka zstandard`).
- **python-build-standalone**: extract `cpython-3.12.x+<triple>.tar.gz` from https://github.com/indygreg/python-build-standalone/releases into `.python-build-standalone/`. The build scripts auto-discover this; without it they fall back to system Python 3.12 for NATIVE builds only (CROSS builds require pybs).
- **Rust toolchain**: `rustup init` → `stable` + `x86_64-unknown-linux-gnu` (or `aarch64-unknown-linux-gnu` on arm64 hosts).
- **Node.js 20+**: for the React renderer build.
- **System libs (apt, Ubuntu 22.04)**: `webkit2gtk-4.1-0 libnotify4 libxtst6 wl-clipboard xclip build-essential pkg-config libssl-dev libgtk-3-dev libayatana-appindicator3-dev librsvg2-dev patchelf`. The `wl-clipboard` package provides `wl-copy`/`wl-paste` for the Wayland clipboard fallback (ADR-0020 §6.6); `xclip` is the X11 fallback; `patchelf` is required by Nuitka's `--standalone` mode on Linux (Nuitka prints `FATAL: Error, standalone mode on Linux requires 'patchelf' to be installed` without it). Both `wl-clipboard` and `xclip` are required because the same binary runs on both session types.
- **System libs (dnf, Fedora 38+)**: `webkit2gtk4.1 libnotify libXtst wl-clipboard xclip @development-tools pkgconf-pkg-config openssl-devel gtk3-devel libayatana-appindicator-gtk3-devel librsvg2-devel patchelf`.
- **qemu-user-static** (CROSS builds only): `sudo apt-get install qemu-user-static binfmt-support` on the x86_64 build host. Enables Nuitka to execute the aarch64 python-build-standalone interpreter during compilation. The script `scripts/build/build_sidecar_linux.sh aarch64` refuses to cross-build without it.
- **input group membership**: the native `linux-key-listener` binary reads `/dev/input/event*`. After install, log out + log back in for the `input` group change to take effect (Linux kernel limitation; handled by `scripts/linux/postinst`).

**Time estimate**: 3-5 hours first run (Nuitka takes 10-15 min per binary × 2 arches × 2 binaries = up to 60 min just for Nuitka); ~30 min subsequent runs with cached deps + Nuitka artifacts.

---

## Step 0 — Environment setup

**Runs on: any Linux host (no display required).**

```bash
# Install Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.io | sh
source "$HOME/.cargo/env"
rustup default stable-x86_64-unknown-linux-gnu   # or aarch64-unknown-linux-gnu on arm64

# Install Node.js 20+ (use NodeSource for a recent version)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

# Install system deps (Ubuntu 22.04)
sudo apt-get update
sudo apt-get install -y \
    webkit2gtk-4.1-0 libnotify4 libxtst6 wl-clipboard xclip \
    build-essential pkg-config libssl-dev libgtk-3-dev \
    libayatana-appindicator3-devel librsvg2-dev \
    python3.12 python3.12-venv python3-pip

# Fedora 38+ equivalent:
# sudo dnf install -y webkit2gtk4.1 libnotify libXtst wl-clipboard xclip \
#     @development-tools pkgconf-pkg-config openssl-devel gtk3-devel \
#     libayatana-appindicator-gtk3-devel librsvg2-devel python3.12

# Clone + enter the repo
git clone https://github.com/AbdallahIsDev/voice-typer.git
cd voice-typer

# Python venv + deps (use uv, not pip — qwen-asr resolution issues with pip)
pip install uv
uv venv
source .venv/bin/activate
uv pip install -e ".[dev,test]"

# Nuitka + python-build-standalone (for the sidecar/prewarm freeze)
uv pip install nuitka zstandard
# Download python-build-standalone cpython-3.12.x+<triple> from
#   https://github.com/indygreg/python-build-standalone/releases
# Extract to .python-build-standalone/cpython-3.12.x+x86_64-unknown-linux-gnu/
# (The build scripts auto-discover any 3.12.x patch version.)

# Node deps
cd voice_typer/client
npm install
cd ../..

# Native hotkey binaries (Linux only — the script skips Win/macOS)
bash scripts/build/compile_native.sh
```

**Expected output**: All commands exit 0. `voice_typer/server/native/linux-key-listener` exists and is executable.

**Pass criteria**: `which cargo`, `which node`, `which python3.12` all resolve. `voice_typer/server/native/linux-key-listener` exists. `python -c "import nuitka; print(nuitka.__version__)"` prints a version.

---

## Step 1 — Nuitka Linux sidecar builds from `python-build-standalone` (glibc 2.35 baseline)

**Runs on: any Linux host matching the target arch (or x86_64 host with qemu-user-static for aarch64).**

```bash
# Native x86_64 build (on an x86_64 host):
bash scripts/build/build_sidecar_linux.sh x86_64

# Native aarch64 build (on an aarch64 host):
bash scripts/build/build_sidecar_linux.sh aarch64

# Cross aarch64 build (on an x86_64 host — requires qemu-user-static):
# sudo apt-get install qemu-user-static binfmt-support
bash scripts/build/build_sidecar_linux.sh aarch64
```

**Expected output**: Nuitka compiles for ~10-15 minutes, producing `src-tauri/bin/python-sidecar-<triple>` (~80-120 MB). The script then runs `ldd` (or `objdump -p` for cross binaries) and verifies the max GLIBC requirement is ≤ `GLIBC_2.35` (Ubuntu 22.04 baseline). A build log is written to `src-tauri/bin/.build-sidecar-<triple>.log`.

**Pass criteria**:
1. The binary `src-tauri/bin/python-sidecar-<triple>` exists and is executable.
2. `ldd src-tauri/bin/python-sidecar-<triple>` (or `objdump -p` for cross) shows `GLIBC_2.35` or lower as the max version requirement.
3. `./src-tauri/bin/python-sidecar-<triple> --help` prints the IPC server help text without errors. (This proves the Python interpreter + faster_whisper + ctranslate2 + websockets all loaded successfully inside the Nuitka bundle.)

**Common failures**:
- `error: gcc: command not found` → `sudo apt-get install build-essential`.
- `ModuleNotFoundError: faster_whisper` → Add `--include-package=faster_whisper` (already in the script). Verify the build env has `faster_whisper` + `ctranslate2` installed in the python-build-standalone site-packages.
- `error: cannot find 'libpython3.12.so'` → Ensure python-build-standalone is extracted to `.python-build-standalone/cpython-3.12.x+<triple>/`.
- `GLIBC_2.36 required` → The build env's glibc is newer than the baseline. Rebuild on ubuntu-22.04 (the CI runner pin per PLATFORM_STATUS.md), or use a python-build-standalone release explicitly built against glibc 2.35.
- `qemu-aarch64-static: command not found` (cross build) → `sudo apt-get install qemu-user-static binfmt-support` then `sudo update-binfmts --enable qemu-aarch64`.

---

## Step 2 — Nuitka prewarm builds (parallel shape to Step 1)

**Runs on: any Linux host matching the target arch.**

```bash
bash scripts/build/build_prewarm_linux.sh x86_64
bash scripts/build/build_prewarm_linux.sh aarch64
```

**Expected output**: Nuitka compiles for ~5-10 minutes (smaller include set — no `faster_whisper`/`ctranslate2`/`websockets`; prewarm only reads files to warm the OS file cache). Produces `src-tauri/resources/prewarm-<triple>`. Same glibc verification as Step 1.

**Pass criteria**:
1. `src-tauri/resources/prewarm-<triple>` exists and is executable.
2. `ldd`/`objdump -p` shows `GLIBC_2.35` or lower.
3. `./src-tauri/resources/prewarm-<triple> --help` (or running it once) prints the prewarm usage without errors.

---

## Step 3 — Native `linux-key-listener` binary build + copy to resources

**Runs on: any Linux host matching the target arch.**

```bash
bash scripts/build/build_native_listener_linux.sh
```

**Expected output**: Invokes `scripts/build/compile_native.sh` (which already handles Linux), producing `voice_typer/server/native/linux-key-listener`. Then copies the binary to `src-tauri/resources/native/linux-key-listener` so Tauri's `bundle.resources` entry picks it up. Runs `ldd` to verify the glibc baseline.

**Pass criteria**:
1. `voice_typer/server/native/linux-key-listener` exists and is executable.
2. `src-tauri/resources/native/linux-key-listener` exists and is executable.
3. `ldd` shows `GLIBC_2.35` or lower.

---

## Step 4 — Build the Tauri app

**VALIDATE ON LINUX DISPLAY HOST** (cargo tauri build needs webkit2gtk, which needs a display server for some link-time tests; use `xvfb-run` for headless builds if needed, but smoke tests in Steps 6-9 still need a real display).

```bash
cd src-tauri
cargo tauri build --target x86_64-unknown-linux-gnu
# Or for aarch64:
# cargo tauri build --target aarch64-unknown-linux-gnu

# Verify the built artifacts exist
ls -la target/release/bundle/
ls -la target/release/bundle/deb/   # .deb package
ls -la target/release/bundle/appimage/  # .AppImage
```

**Expected output**: `cargo tauri build` compiles the Rust host (~5 min), bundles the sidecar + prewarm + native listener + resources, and produces:
- `target/release/bundle/deb/voice-typer_1.0.0_amd64.deb` (or `_arm64.deb` on aarch64)
- `target/release/bundle/appimage/voice-typer_1.0.0_amd64.AppImage` (or `_arm64.AppImage`)

**Pass criteria**: Both `.deb` and `.AppImage` exist. The `.deb` lists the postinst/prerm scripts in its control archive (`dpkg-deb -e <deb> /tmp/control && cat /tmp/control/postinst /tmp/control/prerm`).

**Common failures**:
- `error: failed to run custom build command for webkit2gtk-sys` → `sudo apt-get install libwebkit2gtk-4.1-dev`.
- `error: linking with cc failed: exit status: 1` → Missing `libgtk-3-dev` or `libayatana-appindicator3-dev`.
- `externalBin 'bin/python-sidecar-x86_64-unknown-linux-gnu' not found` → Re-run Step 1; the binary must exist before `cargo tauri build`.

---

## Step 5 — Install + smoke test on X11

**VALIDATE ON LINUX DISPLAY HOST (X11 session).**

```bash
# Install the .deb
sudo apt-get install -y ./src-tauri/target/release/bundle/deb/voice-typer_1.0.0_amd64.deb

# OR run the AppImage without installing:
chmod +x src-tauri/target/release/bundle/appimage/voice-typer_1.0.0_amd64.AppImage
./src-tauri/target/release/bundle/appimage/voice-typer_1.0.0_amd64.AppImage

# Verify the postinst ran (udev rule + input group)
ls -l /etc/udev/rules.d/99-voice-typer.rules
groups $USER  # should include 'input'

# Log out + log back in for the input group to take effect, then:
voice-typer  # or find in application menu
```

**Expected output**: The Voice Typer window opens. The sidecar stdout appears in `~/.local/share/voice-typer/logs/sidecar.log` (per ADR-0020 §11). The native `linux-key-listener` process appears in `ps aux | grep linux-key-listener`.

**Pass criteria**:
1. The Voice Typer main window opens on X11.
2. `~/.local/share/voice-typer/logs/sidecar.log` contains `[SIDECAR] server_started port=N`.
3. `ps aux | grep linux-key-listener` shows the native listener process.
4. The Tauri host's WS client connects to `ws://127.0.0.1:N` and the auth handshake succeeds (log shows `WS connected` + `auth ok`).
5. The tray icon appears in the system tray.

**Common failures**:
- `permission denied: /dev/input/event*` → Log out + log back in for the `input` group change. Verify with `groups $USER`.
- `cannot open display: :0` → Run from an X11 session, not a TTY.
- `Wayland: cannot connect to display` → You're on Wayland; see Step 6 instead.

---

## Step 6 — Install + smoke test on Wayland

**VALIDATE ON LINUX DISPLAY HOST (Wayland session — Fedora 40 default, or Ubuntu 22.04 with `GNOME` session).**

```bash
# Verify you're on Wayland:
echo $XDG_SESSION_TYPE   # should print 'wayland'
echo $WAYLAND_DISPLAY    # should be non-empty (e.g. 'wayland-0')

# Install the same .deb (or run the AppImage — AppImage on Wayland is a
# required test per ADR-0020 Phase 0-L gate).
sudo apt-get install -y ./src-tauri/target/release/bundle/deb/voice-typer_1.0.0_amd64.deb
# OR:
./src-tauri/target/release/bundle/appimage/voice-typer_1.0.0_amd64.AppImage

voice-typer
```

**Expected output**: Same as Step 5, but on Wayland. The sidecar detects Wayland via `WAYLAND_DISPLAY` env var and uses `wl-copy`/`wl-paste` for clipboard I/O (per ADR-0020 §6.6 and the `_linux_wayland_copy` / `_linux_wayland_paste` helpers in `voice_typer/server/clipboard.py`). The native `linux-key-listener` uses evdev (works on Wayland — see ADR-0020 §6.4).

**Pass criteria** (in addition to Step 5's criteria):
1. The Voice Typer main window opens on Wayland.
2. `~/.local/share/voice-typer/logs/sidecar.log` contains `[SIDECAR] server_started port=N`.
3. The native `linux-key-listener` process is running (evdev works on Wayland).
4. **AppImage on Wayland**: running the AppImage does NOT print `wl-copy: failed to connect to wayland` errors. (If it does, the AppImage sandbox is restricting wl-clipboard access — see ADR-0020 §6.6.)

**Common failures**:
- `wl-copy: failed to connect to wayland` → Install `wl-clipboard` (`sudo apt-get install wl-clipboard` or `sudo dnf install wl-clipboard`).
- `cannot open display: :0` → You're on X11; see Step 5 instead. Wayland apps use `WAYLAND_DISPLAY`, not `DISPLAY`.
- `libinput: permission denied` → Log out + log back in for the `input` group change (same as X11).
- `enigo.text() failed on Wayland` → EXPECTED. Per ADR-0020 §6.6, `enigo.text()` is X11-only; the clipboard + `Ctrl+V` fallback must work. This is verified in Step 8.

---

## Step 7 — `faster-whisper` transcribes inside the Nuitka bundle

**VALIDATE ON LINUX DISPLAY HOST (X11 or Wayland).**

```bash
# In the running app (installed via Step 5 or Step 6):
# 1. Open Settings → Models
# 2. Download a small model (e.g., "tiny" or "base")
# 3. Open the Home page
# 4. Press the dictation hotkey (default: Caps Lock or Ctrl+Alt+V — see Settings → Hotkey)
# 5. Speak a test phrase ("hello world")
# 6. Release the hotkey

# Verify the transcription appears in the text field + History page
# Verify the model loaded by checking the log:
tail -f ~/.local/share/voice-typer/logs/sidecar.log | grep -E "model_loaded|whisper"
```

**Pass criteria**: The transcription text appears in the focused text field within 5 seconds of releasing the hotkey. The History page shows the new entry with the correct model name + device name. The log shows `model_loaded` and `whisper` (or `faster_whisper`) entries.

**Common failures**:
- `CUDA error: no kernel image` → The Nuitka bundle didn't include the CUDA runtime. Most Linux installs are CPU-only; if CUDA is required, add `--include-package=torch` + the CUDA libs to `scripts/build/build_sidecar_linux.sh`.
- `Model not found` → The model download path resolves to the wrong directory. Check `~/.local/share/voice-typer/models/` (per ADR-0020 §8 — `$XDG_DATA_HOME/voice-typer/models/`).
- `ctranslate2 ImportError` → The build env was missing `libiomp5.so` / `libgomp.so`. The build script's `--include-data-dir=$SITE/ctranslate2/lib=...` should pick these up; verify with `ldd src-tauri/bin/python-sidecar-<triple> | grep -E 'libiomp|libgomp'`.

---

## Step 8 — Paste keystroke works on X11 AND Wayland

**VALIDATE ON LINUX DISPLAY HOST (run on BOTH X11 and Wayland sessions).**

```bash
# In the running app:
# 1. Open a text editor (gnome-text-editor on X11, gedit on Wayland, OR a terminal)
# 2. Press the dictation hotkey
# 3. Speak a short phrase ("test paste")
# 4. Release the hotkey

# Verify the transcribed text appears in the text editor
```

**Pass criteria**:
- **On X11**: The transcribed text appears in the text editor. For short text (<300 chars), `enigo.text()` injects it directly via X11 `XTestFakeKeyEvent`. For long text, the clipboard + `Ctrl+V` path is used.
- **On Wayland**: The transcribed text appears in the text editor via the clipboard + `Ctrl+V` fallback (`enigo.text()` is X11-only per ADR-0020 §6.6). The `_linux_wayland_copy()` helper in `voice_typer/server/clipboard.py` writes the text via `wl-copy`; pynput's X11 backend sends `Ctrl+V` via XWayland (or the Rust host's `enigo` path uses `wl-copy` + a wlr-virtual-keyboard protocol — verify which path your build uses).
- **Clipboard restore**: after the paste, the original clipboard contents are restored (per ADR-0012 borrow/restore logic). Verify by copying something else to clipboard, dictating, then pasting manually with `Ctrl+V` — the original content should reappear.

**Common failures**:
- **Wayland**: `enigo.text() failed` → EXPECTED on Wayland. Verify the clipboard + `Ctrl+V` fallback path works. If neither works, check that `wl-clipboard` is installed and `WAYLAND_DISPLAY` is set.
- **Wayland**: `wl-copy: failed to connect` → Install `wl-clipboard` or run from a Wayland session (not X11).
- **AppImage on Wayland**: `wl-copy: permission denied` → The AppImage sandbox may restrict wl-clipboard access. Test the `.deb` install path first; if the `.deb` works but AppImage doesn't, document as a known AppImage-Wayland limitation.

---

## Step 9 — libnotify toast appears on X11 AND Wayland

**VALIDATE ON LINUX DISPLAY HOST (run on BOTH X11 and Wayland sessions).**

```bash
# In the running app:
# 1. Open Settings → General
# 2. Toggle "Notifications" on
# 3. Trigger a notification (e.g., start dictation, then stop)

# Verify a notification appears via libnotify
# On X11: the notification appears in the GNOME Shell notification list / KDE Plasma notification widget.
# On Wayland: same — libnotify works on both.
notify-send "test"  # verify libnotify itself works on the host
```

**Pass criteria**: A notification appears in the desktop environment's notification list with the Voice Typer icon + the notification text. Both X11 and Wayland show the notification (libnotify is display-server-agnostic via D-Bus).

**Common failures**:
- `notification:allow-notify not in capabilities` → Add `notification:allow-notify` to `src-tauri/capabilities/migrate-runtime.json`. Per ADR-0020 §7, Tauri v2 silently blocks notification APIs without the capability.
- `libnotify: command not found` → `sudo apt-get install libnotify4` (or `libnotify` on Fedora).
- Notification doesn't appear → Some desktop environments (Sway, i3) don't run a notification daemon by default. Install `mako` or `dunst` and start it.

---

## Step 10 — Cooperative shutdown + `kill_children` backstop

**VALIDATE ON LINUX DISPLAY HOST.**

```bash
# In the running app:
# 1. Close the main window (click X)
# 2. Verify the sidecar exits within 2 seconds

# Check for lingering processes:
ps aux | grep -E 'python-sidecar|linux-key-listener|voice-typer' | grep -v grep
# Should return nothing within 2 seconds of closing the window.

# Check the log for the shutdown handshake:
tail -20 ~/.local/share/voice-typer/logs/sidecar.log | grep -E 'shutdown|kill_children'
```

**Pass criteria**: `ps aux | grep python-sidecar` returns nothing within 2 seconds of closing the main window. The log shows `[SHUTDOWN] sidecar exited cleanly` (cooperative) OR `[SHUTDOWN] sidecar killed via kill_children` (backstop). No zombie `linux-key-listener` processes remain.

**Common failures**:
- Sidecar lingers >5 seconds → The cooperative shutdown handshake failed. Check the log for `WS disconnected` without a preceding `shutdown` frame. The Rust host should fall back to `kill_children` after the 2.0s timeout (ADR-0020 §10).
- `linux-key-listener` zombie → The sidecar didn't clean up its child process. Verify `kill_children` is invoked on the sidecar's process tree, not just the sidecar itself.

---

## Step 11 — Prewarm systemd user timer

**VALIDATE ON LINUX DISPLAY HOST.**

```bash
# In the running app:
# 1. Open Settings → General
# 2. Enable "Prewarm on login"
# 3. Reboot (or `systemctl --user restart voice-typer-prewarm.timer`)

# Verify the systemd user timer was registered:
systemctl --user list-timers voice-typer-prewarm.timer
systemctl --user status voice-typer-prewarm.service
cat ~/.config/systemd/user/voice-typer-prewarm.service
cat ~/.config/systemd/user/voice-typer-prewarm.timer

# Verify the prewarm binary ran:
journalctl --user -u voice-typer-prewarm.service --no-pager | tail -20
```

**Pass criteria**:
1. `systemctl --user list-timers voice-typer-prewarm.timer` shows the timer with `OnBootSec=10s`.
2. `~/.config/systemd/user/voice-typer-prewarm.service` exists with `ExecStart=` pointing at the frozen prewarm binary (NOT a `python3 -m ...` command — that's the dev fallback).
3. After reboot, `journalctl --user -u voice-typer-prewarm.service` shows the prewarm ran successfully.
4. The prewarm log at `~/.local/share/voice-typer/logs/prewarm.log` shows file-cache warming activity.

**Common failures**:
- `ExecStart=python3 -m voice_typer.server.prewarm` (dev fallback) → The frozen prewarm binary wasn't found. Verify `src-tauri/resources/prewarm-<triple>` exists in the install dir (`/usr/lib/voice-typer/resources/prewarm-<triple>` for `.deb`, or `$APPDIR/usr/resources/prewarm-<triple>` for AppImage). Check the `VOICE_TYPER_PREWARM_EXE` env var is set by the Tauri host.
- `systemctl --user` returns "Failed to connect to bus" → Run from a graphical session, not a TTY. The systemd user instance is per-login.
- `systemctl --user list-timers` shows nothing → The Tauri host didn't call `register_prewarm_task()` on first launch. Check the sidecar log for `[PREWARM-POSIX] Linux systemd user timer registered`.

---

## Step 12 — Native `linux-key-listener` toggles dictation on X11 AND Wayland

**VALIDATE ON LINUX DISPLAY HOST (run on BOTH X11 and Wayland sessions).**

```bash
# In the running app:
# 1. Open Settings → Hotkey
# 2. Set a custom hotkey (e.g., F8)
# 3. Focus a text field (e.g., gedit or gnome-text-editor)
# 4. Press F8
# 5. Speak a test phrase
# 6. Press F8 again

# Verify the dictation toggles correctly
# Verify the native listener process is running:
ps aux | grep linux-key-listener | grep -v grep
```

**Pass criteria**: Pressing F8 starts recording (the bubble appears with "Listening…"). Pressing F8 again stops recording and pastes the transcription (per Step 8). The native `linux-key-listener` process is visible in `ps aux` while the app is running. The same flow works on BOTH X11 and Wayland (evdev sits below the display server per ADR-0020 §6.4).

**Common failures**:
- `permission denied: /dev/input/event*` → Log out + log back in for the `input` group change. Verify `groups $USER` includes `input`.
- Hotkey doesn't fire on Wayland → evdev should work on Wayland; verify `linux-key-listener` is running and `/dev/input/event*` is readable. Sway users may need to add the user to the `input` group explicitly (some Sway configs don't honor the udev rule).
- Hotkey fires twice per press → The native binary's debounce logic may need tuning. Check the sidecar log for duplicate `FN_DOWN` events.

---

## Step 13 — `.deb` and `.AppImage` build with the existing `postinst`/`prerm` scripts

**Runs on: any Linux host (no display required for the build, but install + smoke needs a display).**

```bash
# Verify the .deb has the postinst + prerm scripts:
dpkg-deb -e src-tauri/target/release/bundle/deb/voice-typer_1.0.0_amd64.deb /tmp/control
cat /tmp/control/postinst
cat /tmp/control/prerm
cat /tmp/control/conffiles  # may be empty

# Verify the udev rule is in the .deb:
dpkg-deb -c src-tauri/target/release/bundle/deb/voice-typer_1.0.0_amd64.deb | grep -E 'voice-typer.rules|voice-typer.polkit|postinst|prerm'

# Install + verify the udev rule landed:
sudo apt-get install -y ./src-tauri/target/release/bundle/deb/voice-typer_1.0.0_amd64.deb
ls -l /etc/udev/rules.d/99-voice-typer.rules
ls -l /usr/share/voice-typer/scripts/install_permissions.py
ls -l /usr/share/voice-typer/scripts/uninstall_permissions.py

# Uninstall + verify the prerm cleaned up:
sudo apt-get remove -y voice-typer
ls /etc/udev/rules.d/99-voice-typer.rules 2>&1  # should be 'No such file'
```

**Pass criteria**:
1. The `.deb`'s control archive contains `postinst` and `prerm` scripts (per ADR-0020 §13.3, these are reused verbatim from `scripts/linux/`).
2. The `.deb` contains `/etc/udev/rules.d/99-voice-typer.rules` and `/usr/share/voice-typer/scripts/install_permissions.py`.
3. After install, `/etc/udev/rules.d/99-voice-typer.rules` exists.
4. After uninstall, the udev rule is removed (the prerm script handles this).
5. The AppImage runs without extraction errors on both X11 and Wayland.

**Common failures**:
- `postinst: not found` in control archive → The `tauri.conf.json` `bundle.linux.deb.postInstall` path is wrong. Per ADR-0020 §13.3, it should be `"../../scripts/linux/postinst"` (relative to `src-tauri/`).
- udev rule not installed → The postinst script failed silently. Run `sudo bash /usr/share/voice-typer/scripts/install_permissions.py` manually to see the error.

---

## 9-Point Validation Gate Summary

The 9 mandatory checks (per ADR-0020 §"Phase 0 validation gate" — Phase 0-L):

| # | Check | Pass criteria |
|---|---|---|
| 1 | Nuitka Linux sidecar builds (x86_64 + aarch64) | `python-sidecar-<triple>` exists; `ldd` shows GLIBC ≤ 2.35; `--help` works |
| 2 | `externalBin` sidecar spawns via Tauri on X11 + Wayland | Tauri app launches; `.deb` + `.AppImage` produced |
| 3 | HMAC handshake works on X11 + Wayland | Log shows `auth ok` |
| 4 | `faster-whisper` transcribes inside Nuitka exe | Transcription appears in text field + History |
| 5 | `enigo` types on X11; clipboard + Ctrl+V fallback works on Wayland | Text appears in editor on BOTH session types |
| 6 | `tauri-plugin-notification` posts via libnotify on X11 + Wayland | Notification appears in DE notification list |
| 7 | Cooperative `{"type":"shutdown"}` exits; `kill_children` cleans | No zombie processes within 2s of window close |
| 8 | Prewarm exe registered as systemd user timer | `systemctl --user list-timers` shows the timer; runs at boot |
| 9 | Native `linux-key-listener` (evdev) toggles dictation on X11 + Wayland | F8 starts/stops recording on BOTH session types |

**All 9 must pass on BOTH X11 AND Wayland before the Linux Tauri cutover.** Electron remains the fallback until all 9 pass on both session types.

Bonus checks (recommended but not blocking for Phase 0-L):
- `.deb` + `.rpm` build with `postinst`/`prerm` scripts (Step 13).
- AppImage runs on Wayland (Fedora 40 default — Step 6 AppImage subtest).
- aarch64 build + smoke test (run on a real aarch64 host or under qemu-system-aarch64 with a Wayland display).

---

## Linux unsigned packaging (ADR-0020 §13.3)

Linux packages are unsigned by default in both Electron (today) and Tauri. The `scripts/linux/postinst`, `prerm`, `postinst.rpm`, `prerm.rpm` scripts are reused verbatim — they install the udev rule, add the user to the `input` group, configure Caps Lock neutralization, and write a manifest at `/var/lib/voice-typer/permissions-manifest.json`.

**Wire into Tauri's `bundle.linux` config** (in `src-tauri/tauri.conf.json`):

```json
"bundle": {
  "linux": {
    "deb": {
      "depends": ["libnotify4", "libxtst6", "libwebkit2gtk-4.1-0", "python3", "wl-clipboard", "xclip"],
      "desktopTemplate": "voice-typer.desktop.template",
      "postInstall": "../../scripts/linux/postinst",
      "preRemove": "../../scripts/linux/prerm"
    },
    "rpm": {
      "depends": ["libnotify", "libXtst", "webkit2gtk3", "python3", "wl-clipboard", "xclip"],
      "postInstall": "../../scripts/linux/postinst.rpm",
      "preRemove": "../../scripts/linux/prerm.rpm"
    }
  }
}
```

**Optional signing (out of scope for v1)**:
- GPG-sign the `.deb`: `dpkg-sig --sign builder <deb>`. Users verify with `apt-key`.
- GPG-sign the `.rpm`: `rpm --addsign <rpm>`. Users verify with `rpm --checksig`.
- AppImage GPG signature: AppImage supports `zsync` + GPG; documented at the AppImage spec.

---

## Rollback

If any of the 9 checks fail and you need to revert to the Electron build on Linux:

1. `sudo apt-get remove -y voice-typer` (removes the Tauri `.deb`).
2. Remove the systemd user timer (the prerm should have done this, but verify):
   ```bash
   systemctl --user disable --now voice-typer-prewarm.timer 2>/dev/null || true
   rm -f ~/.config/systemd/user/voice-typer-prewarm.{service,timer}
   systemctl --user daemon-reload
   ```
3. Remove the udev rule (the prerm should have done this):
   ```bash
   sudo rm -f /etc/udev/rules.d/99-voice-typer.rules
   sudo udevadm control --reload-rules
   ```
4. Install the Electron `.deb`/`.AppImage` from the previous release. The user's data at `~/.local/share/voice-typer/` (config, models, history) is preserved — the Tauri build writes to the same `_paths.config_dir()` location per ADR-0020 §8.

No data loss on revert. The Electron app picks up the same config + models + history DB.

---

## Capture results

After running the runbook, capture the following artifacts for the migration record:

1. `src-tauri/bin/.build-sidecar-<triple>.log` (Nuitka build log).
2. `src-tauri/resources/.build-prewarm-<triple>.log` (Nuitka prewarm build log).
3. `ldd` output for both binaries (proves the glibc baseline).
4. `~/.local/share/voice-typer/logs/sidecar.log` (runtime log — contains `server_started`, `auth ok`, `model_loaded`, `shutdown` events).
5. `~/.local/share/voice-typer/logs/prewarm.log` (prewarm log).
6. `systemctl --user status voice-typer-prewarm.timer` output (proves the systemd user timer registered).
7. `journalctl --user -u voice-typer-prewarm.service` output (proves the prewarm ran at boot).
8. Screenshots of: the main window, the bubble, a notification, a transcription in a text editor — on BOTH X11 and Wayland.
9. `dpkg-deb -e` + `dpkg-deb -c` output for the `.deb` (proves the postinst/prerm scripts + udev rule are in the package).
10. The `cargo tauri build` stdout (proves the Rust host compiled + bundled successfully).

File these in `docs/migration/phase-0-l-results.md` (create if missing) for the migration decision record. Electron remains the shippable fallback until all 9 checks pass on both X11 and Wayland, on both x86_64 and aarch64.
