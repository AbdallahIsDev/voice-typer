#!/bin/bash
#Voice Typer — macOS uninstall cleanup script ( + ).
#
# Removes per-user autostart + data artifacts that survive `app deletion`
# from /Applications. macOS apps don't have a system uninstaller (the user
# just drags the .app to Trash), so this script is the equivalent of the
# Linux prerm + uninstall_permissions.py cleanup.
#
# Usage:
#   bash scripts/macos/uninstall.sh           # default: remove autostart + user data
#   bash scripts/macos/uninstall.sh --purge   # also remove HuggingFace cache
#   bash scripts/macos/uninstall.sh -h|--help
#
#removes ~/Library/LaunchAgents/com.voicetyper.plist (the
# LaunchAgent written by autostart_macos._enable_autostart_macos when the
# user enables autostart in Settings). Without this, launchd would keep
# trying to launch the (now-deleted) binary at every login — spamming
# the system log with "command not found" errors. Also unloads the
# running agent first (best-effort) so it dies immediately rather than
# lingering until next logout.
#
#removes ~/Library/Application Support/voice-typer (the per-user
# data dir: settings JSON, history DB, downloaded vocabularies, etc.).
# This is the macOS equivalent of ~/.local/share/voice-typer on Linux.
#
#(HuggingFace cache): the HuggingFace cache lives at
# ~/.cache/huggingface on macOS (HuggingFace's library follows XDG
# conventions even on macOS). It stores downloaded ASR model weights
# (faster-whisper, WhisperCPP, etc.) and can grow to multiple GB. We do
# NOT remove it by default — the user may want to reuse it for OTHER
# HF-based apps (transformers, datasets, other voice tools). Pass
# --purge to remove it.
#
# Tauri installerHooks integration:
#   Tauri v2's .dmg bundler does NOT support a post-uninstall hook
#   (macOS apps are uninstalled by dragging to Trash, not via a
#   package-manager callback). To wire this into a future Tauri
#   installerHooks flow, add the following to src-tauri/tauri.conf.json's
#   bundle.macOS block (when supported):
#       "installerHooks": {
#           "postUninstall": "bash $RESOURCE_DIR/scripts/macos/uninstall.sh"
#       }
#   Until then, document this script in the README / uninstall
#   instructions as a manual post-uninstall step:
#       bash /Applications/voice-typer.app/Contents/Resources/scripts/macos/uninstall.sh
#
# VALIDATE ON MACOS HOST:
#   1. Build the .dmg:
#         cd src-tauri && cargo tauri build --target universal-apple-darwin
#   2. Install the .dmg (drag to /Applications).
#   3. Launch Voice Typer → enable autostart via Settings.
#   4. Verify the LaunchAgent is installed:
#         ls -l ~/Library/LaunchAgents/com.voicetyper.plist
#         launchctl list | grep com.voicetyper
#   5. Verify the data dir exists:
#         ls -la ~/Library/Application\ Support/voice-typer/
#   6. Drag voice-typer.app to Trash (or `rm -rf /Applications/voice-typer.app`).
#   7. Run this script:
#         bash scripts/macos/uninstall.sh
#   8. Verify the LaunchAgent + data dir are gone:
#         ls ~/Library/LaunchAgents/com.voicetyper.plist   # No such file
#         ls ~/Library/Application\ Support/voice-typer/   # No such directory
#         launchctl list | grep com.voicetyper             # no matches
#   9. (Optional) Verify HF cache purge with --purge:
#         bash scripts/macos/uninstall.sh --purge
#         ls ~/.cache/huggingface   # No such file (if it existed before)

set -e

PURGE=0
for arg in "$@"; do
    case "$arg" in
        --purge)
            PURGE=1
            ;;
        -h|--help)
            echo "Usage: $0 [--purge]"
            echo "  --purge  Also remove the HuggingFace cache (~/.cache/huggingface)"
            echo ""
            echo "Removes per-user Voice Typer artifacts on macOS:"
            echo "  - ~/Library/LaunchAgents/com.voicetyper.plist (autostart)"
            echo "  - ~/Library/Application Support/voice-typer (user data)"
            echo "  - ~/.cache/huggingface (only with --purge)"
            exit 0
            ;;
        *)
            echo "[voice-typer-uninstall] WARNING: unknown argument: $arg" >&2
            ;;
    esac
done

#remove the LaunchAgent plist (autostart).
PLIST_PATH="$HOME/Library/LaunchAgents/com.voicetyper.plist"
if [ -f "$PLIST_PATH" ]; then
    # Best-effort: unload the agent before deleting so it dies immediately
    # (rather than lingering until next logout). Try the modern
    # `launchctl bootout` (macOS 10.10+) first, then the legacy
    # `launchctl remove` for older systems. Both are non-fatal on failure
    # (the job dies when launchd next re-reads its config + the plist is
    # gone anyway).
    UID_NUM="$(id -u 2>/dev/null || echo 501)"
    launchctl bootout "gui/${UID_NUM}/com.voicetyper" 2>/dev/null || true
    launchctl remove "com.voicetyper" 2>/dev/null || true
    rm -f "$PLIST_PATH"
    echo "[voice-typer-uninstall] Removed LaunchAgent: $PLIST_PATH"
else
    echo "[voice-typer-uninstall] No LaunchAgent to remove ($PLIST_PATH not present)"
fi

# Prewarm LaunchAgent cleanup.
# The prewarm scheduler (voice_typer/server/prewarm_scheduler_posix.py) registers
# a SEPARATE LaunchAgent at ~/Library/LaunchAgents/com.voicetyper.prewarm.plist
# with label `com.voicetyper.prewarm` (RunAtLoad=true). This is distinct from
# the main-app autostart plist above. Without this cleanup block, the prewarm
# agent survives uninstall and launchd keeps trying to launch the (now-deleted)
# frozen prewarm binary at every login — spamming the system log with "command
# not found" errors. Same bootout/unload/rm pattern as the main-app cleanup
# above.
PREWARM_PLIST="$HOME/Library/LaunchAgents/com.voicetyper.prewarm.plist"
if [ -f "$PREWARM_PLIST" ]; then
    UID_NUM="$(id -u 2>/dev/null || echo 501)"
    # Try modern `launchctl bootout` first (macOS 10.10+), then legacy
    # `launchctl unload` as a fallback for older systems. Both are
    # non-fatal: if the agent is already gone or launchctl is restricted,
    # we still delete the plist file so launchd won't re-load it on next
    # login.
    launchctl bootout "gui/${UID_NUM}/com.voicetyper.prewarm" 2>/dev/null || \
        launchctl unload "$PREWARM_PLIST" 2>/dev/null || true
    rm -f "$PREWARM_PLIST"
    echo "[voice-typer-uninstall] Removed prewarm LaunchAgent: $PREWARM_PLIST"
else
    echo "[voice-typer-uninstall] No prewarm LaunchAgent to remove ($PREWARM_PLIST not present)"
fi

#remove the per-user data directory.
# Quote the path to handle the space in "Application Support".
DATA_DIR="$HOME/Library/Application Support/voice-typer"
if [ -d "$DATA_DIR" ]; then
    rm -rf "$DATA_DIR"
    echo "[voice-typer-uninstall] Removed user data directory: $DATA_DIR"
else
    echo "[voice-typer-uninstall] No user data directory to remove ($DATA_DIR not present)"
fi

#(HF cache): opt-in purge of the HuggingFace cache.
if [ "$PURGE" = "1" ]; then
    HF_CACHE="$HOME/.cache/huggingface"
    if [ -d "$HF_CACHE" ]; then
        rm -rf "$HF_CACHE"
        echo "[voice-typer-uninstall] Removed HuggingFace cache: $HF_CACHE"
    else
        echo "[voice-typer-uninstall] No HuggingFace cache to remove ($HF_CACHE not present)"
    fi
fi

echo "[voice-typer-uninstall] Done."
