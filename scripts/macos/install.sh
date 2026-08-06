#!/bin/bash
# Voice Typer — macOS post-install helper.
#
# Strips the macOS Gatekeeper quarantine xattr from the freshly
# installed .app bundle and re-registers it with LaunchServices so the
# app appears in Spotlight / Finder / Launchpad immediately (without
# requiring the user to log out + log back in).
#
# Why this exists:
#   macOS applies the ``com.apple.quarantine`` extended attribute to any
#   file downloaded from the Internet (or copied from a disk image that
#   was downloaded). The first time the user double-clicks the .app,
#   Gatekeeper intercepts the launch and shows either:
#     a) "Voice Typer cannot be opened because Apple cannot check it for
#        malicious software." (the hard block — app refuses to launch),
#   or
#     b) "Voice Typer is an app downloaded from the Internet. Are you sure
#        you want to open it?" (the soft prompt — user must click Open).
#
#   For users who installed via the official .dmg / .pkg installer (a
#   trust decision they already made by downloading the .dmg from the
#   official site), this prompt is pure friction. Stripping the
#   quarantine xattr post-install means subsequent launches go straight
#   through without the prompt.
#
#   The ``lsregister -f`` call registers the .app's Info.plist with the
#   LaunchServices database so the app shows up in Spotlight, Finder's
#   "Open With" menus, and Launchpad. Without this, the freshly mounted
#   .app may not appear in Spotlight until the next LaunchServices
#   rebuild (which can take minutes-to-hours depending on system load).
#
# Right-click → Open workaround:
#   If the user reports the app still won't launch (e.g. on macOS 13+
#   with stricter Gatekeeper rules, or after a quarantine flag was
#   re-applied by a re-download), the manual workaround is:
#     1. In Finder, locate Voice Typer.app in /Applications.
#     2. Right-click (or Control-click) the .app icon.
#     3. Select "Open" from the context menu.
#     4. macOS shows the "downloaded from the Internet" prompt — click
#        "Open" again. macOS remembers the trust decision for future
#        double-click launches.
#   This is documented in the README's "macOS troubleshooting" section
#   as the fallback when the post-install script is bypassed (e.g. the
#   user dragged the .app to /Applications manually instead of running
#   the .pkg installer).
#
# Usage:
#   bash scripts/macos/install.sh
#   bash scripts/macos/install.sh --app-path "/Applications/Voice Typer.app"
#   bash scripts/macos/install.sh -h|--help
#
# Tauri installerHooks integration:
#   Tauri v2's .dmg / .pkg bundler does NOT support a post-install hook
#   for .dmg (the user drags the .app to /Applications manually). For
#   .pkg installs, add the following to src-tauri/tauri.conf.json's
#   bundle.macOS block (when supported):
#       "installerHooks": {
#           "postInstall": "bash $RESOURCE_DIR/scripts/macos/install.sh"
#       }
#   Until then, document this script in the README / install
#   instructions as a manual post-install step:
#       bash /Applications/voice-typer.app/Contents/Resources/scripts/macos/install.sh
#
# VALIDATE ON MACOS HOST:
#   1. Build the .dmg / .pkg:
#         cd src-tauri && cargo tauri build --target universal-apple-darwin
#   2. Install the .dmg (drag to /Applications) or run the .pkg.
#   3. Run this script:
#         bash scripts/macos/install.sh
#   4. Verify the quarantine xattr is gone:
#         xattr "/Applications/Voice Typer.app"
#         # output should be empty (or NOT contain com.apple.quarantine)
#   5. Verify LaunchServices registration:
#         mdfind "kMDItemCFBundleIdentifier == 'abdallahisdev.VoiceTyper'"
#         # should list /Applications/Voice Typer.app
#   6. Double-click Voice Typer.app — should launch WITHOUT the
#      Gatekeeper prompt.

set -e

APP_PATH="/Applications/Voice Typer.app"
for arg in "$@"; do
    case "$arg" in
        --app-path)
            shift
            APP_PATH="$1"
            ;;
        -h|--help)
            echo "Usage: $0 [--app-path \"/Applications/Voice Typer.app\"]"
            echo ""
            echo "Strips the macOS Gatekeeper quarantine xattr from the"
            echo "Voice Typer.app bundle and re-registers it with"
            echo "LaunchServices so it launches without prompts and"
            echo "appears in Spotlight immediately."
            echo ""
            echo "Options:"
            echo "  --app-path PATH  Override the .app location (default:"
            echo "                   \"${APP_PATH}\")"
            exit 0
            ;;
        *)
            # Ignore unknown args (forward-compatible) — log a warning.
            echo "[voice-typer-install] WARNING: unknown argument: $arg" >&2
            ;;
    esac
    shift || true
done

# Sanity: bail out if the .app isn't where we expect. We don't ``set -e``
# here because we want a helpful error message rather than a bare
# "No such file or directory" from ``xattr``.
if [ ! -d "$APP_PATH" ]; then
    echo "[voice-typer-install] ERROR: app bundle not found at: $APP_PATH" >&2
    echo "[voice-typer-install]        Pass --app-path /path/to/Voice Typer.app" >&2
    echo "[voice-typer-install]        if you installed to a non-default location." >&2
    exit 1
fi

# Strip the com.apple.quarantine extended attribute recursively
# (-r) from the .app bundle. ``-d`` deletes the xattr; without ``-d``,
# ``xattr`` would just print the current value (a no-op).
#
# Why recursive (-r): macOS applies the quarantine xattr to the .app
# bundle root, but the binary inside (Contents/MacOS/voice-typer) and
# the dylibs in Contents/Frameworks may also be tagged if they were
# downloaded separately. ``-r`` walks the whole bundle so we don't
# leave a residual tag on an inner binary that triggers Gatekeeper
# when the binary is exec'd.
#
# Why 2>/dev/null || true: if the bundle has NO quarantine xattr
# (e.g. it was built locally with ``cargo tauri build`` and never
# touched by a browser download), ``xattr -d`` exits non-zero with
# "No such xattr: com.apple.quarantine". That's the happy path — we
# don't want the script to fail in that case.
echo "[voice-typer-install] Stripping com.apple.quarantine from: $APP_PATH"
xattr -dr com.apple.quarantine "$APP_PATH" 2>/dev/null || true

# Re-register the .app with LaunchServices so Spotlight / Finder
# / Launchpad pick it up immediately (without waiting for the next
# background LaunchServices rebuild).
#
# ``lsregister`` is the LaunchServices registration CLI. It lives at
# /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
# (a path so unwieldy that Apple's own docs alias it via the
# ``lsregister`` symlink in /usr/bin on some macOS versions — but NOT
# all). We hard-code the absolute path so the script works on every
# macOS install regardless of PATH / symlink setup.
LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"

if [ -x "$LSREGISTER" ]; then
    echo "[voice-typer-install] Registering with LaunchServices: $APP_PATH"
    "$LSREGISTER" -f "$APP_PATH" 2>/dev/null || true
else
    echo "[voice-typer-install] WARNING: lsregister not found at: $LSREGISTER" >&2
    echo "[voice-typer-install]          Spotlight / Launchpad may take longer to" >&2
    echo "[voice-typer-install]          index the app. (Non-fatal — the app still" >&2
    echo "[voice-typer-install]          launches by double-click.)" >&2
fi

echo "[voice-typer-install] Done."
echo "[voice-typer-install] You can now launch Voice Typer by double-clicking"
echo "[voice-typer-install] $APP_PATH"
echo "[voice-typer-install] "
echo "[voice-typer-install] If macOS STILL shows the Gatekeeper prompt, the"
echo "[voice-typer-install] manual workaround is:"
echo "[voice-typer-install]   1. In Finder, right-click (or Control-click) Voice Typer.app"
echo "[voice-typer-install]   2. Select \"Open\" from the context menu"
echo "[voice-typer-install]   3. Click \"Open\" in the prompt — macOS remembers the"
echo "[voice-typer-install]      trust decision for future launches."
