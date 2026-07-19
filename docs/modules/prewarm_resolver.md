# PrewarmResolver

**File**: `voice_typer/server/prewarm_resolver.py` (213 lines)

## Responsibility

The `PrewarmResolver` module resolves the frozen prewarm executable path across all three desktop platforms. It is shared by the Windows Task Scheduler, macOS LaunchAgent, and Linux systemd user timer prewarm entry points.

Key responsibilities:
- Resolve the `prewarm-<triple>[.exe]` binary path from multiple sources
- Search order (first-match wins):
  1. `VOICE_TYPER_PREWARM_EXE` environment variable (single-file override)
  2. Tauri resource directory (`resourceDir/prewarm/`)
  3. PyInstaller bundled paths (frozen exe next to the main binary)
  4. Dev fallback (`python -m voice_typer.server.prewarm`)
- Validate that the resolved binary exists and is executable

## Entry Points

- **`resolve_prewarm_exe()`** — the primary entry point. Returns a `Path` to the prewarm executable, or `None` if no valid path is found (falling back to dev-mode `python -m`).
- **`_target_triple()`** — helper that returns the current platform's Rust target triple (e.g., `x86_64-pc-windows-msvc`, `aarch64-apple-darwin`).
- **`_exe_suffix()`** — returns `.exe` on Windows, empty string on macOS/Linux.

## IPC Surface

None. The `PrewarmResolver` is a utility module with no IPC surface. It is called at startup by the prewarm pipeline and by the platform-specific scheduler modules (`task_scheduler.py`, `prewarm_scheduler_posix.py`).
