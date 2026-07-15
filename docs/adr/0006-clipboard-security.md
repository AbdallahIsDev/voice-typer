# ADR 0006: Clipboard Security Approach

## Status

Accepted

## Date

2024-03-01

## Context

Voice Typer pastes transcribed text into the user's active application by simulating
keyboard input (Ctrl+V). This requires reading and writing the system clipboard, which
has security implications:

1. **Clipboard history** — pasting stores the transcription in the clipboard history,
   which may be accessible to other applications.
2. **Elevated processes** — if the focused window is an elevated (UAC) process, the
   clipboard paste will silently fail, and the user may not understand why.
3. **Sensitive content** — transcriptions may contain passwords, API keys, or personal
   information that should not persist in the clipboard.
4. **Race conditions** — another application may modify the clipboard between our write
   and the simulated Ctrl+V, causing the wrong text to be pasted.

Options considered:

1. **No clipboard, use typing emulation** — type each character individually; very slow
   for long texts, breaks with IME, and triggers keyboard shortcuts.
2. **Clipboard with immediate restore** — save the current clipboard content, paste the
   transcription, then restore the original content after a short delay.
3. **Clipboard without restore** — paste and leave the transcription in the clipboard;
   simplest but leaks content.

## Decision

We chose **clipboard with immediate restore** (option 2). After pasting, we schedule a
restore of the original clipboard content after 500ms. This minimizes the window during
which the transcription is visible in the clipboard.

We also added:
- **Clipboard sequence number tracking** (Windows) to detect if another app modified the
  clipboard during our paste window.
- **Elevated process detection** — skip paste when the foreground window is an elevated
  (UAC/Winlogon) process and notify the user instead.
- **Secure file permissions** — config files containing API keys are created with 0o600
  permissions on POSIX systems.

## Consequences

### Positive
- Minimal clipboard exposure: the transcription is only in the clipboard for ~500ms.
- User-friendly: the user's original clipboard content is preserved after pasting.
- Secure by default: config files are not world-readable.
- Graceful degradation: elevated-process detection prevents silent failures.

### Negative
- Timing-sensitive: the 500ms restore delay may fail if the user switches applications
  rapidly.
- Platform-specific: clipboard sequence numbers are Windows-only; on macOS/Linux we
  rely on the restore delay.
- Not perfect: a determined attacker with clipboard-monitoring malware could capture
  the transcription during the 500ms window.
