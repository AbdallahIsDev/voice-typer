# ADR 0018: Electron-Alive Heartbeat Watchdog (RW-10)

## Status

Accepted — implemented in `voice_typer/server/ipc_server.py:_heartbeat_loop`, `_check_heartbeat_timeout`, `_handle_heartbeat`, and `client/src/main/index.ts` heartbeat interval.

## Date

2026-07-14

## Context

Voice Typer's architecture is a **two-process** design: the Electron frontend spawns the Python backend as a subprocess and communicates over a local TCP socket. The two processes have separate lifecycles:

- Electron can crash or be force-killed by the user (Task Manager "End task" on Windows, `kill -9` on Linux/macOS).
- The Python backend runs independently once spawned — it has its own thread for audio capture, its own hotkey registration, its own volume ducking, and its own Win32 named mutex for single-instance enforcement.

**The problem:** If Electron crashes or is force-killed, the Python backend keeps running:
- The microphone stream stays open (recording audio).
- Hotkeys stay registered (consuming OS input).
- System volume stays ducked (platform-level side effect).
- The single-instance mutex is held (preventing the next launch — the user sees "Only one instance can run").

Without a heartbeat mechanism, the only way to recover is for the user to manually find and kill the `python.exe` process in Task Manager. This is a poor user experience that occurs in practice (Electron can be killed by a crash, a hung renderer, or an aggressive memory-reduction tool).

**Alternatives considered:**

1. **PID-based liveness check.** The Python backend periodically checks if the parent process (Electron) is still alive via `os.kill(parent_pid, 0)`. This is simple but platform-specific (`os.kill` semantics differ), and the parent PID may not be reliable (orphaned process groups, intermediate launcher scripts). Also, Electron may be alive but unresponsive (renderer crash), which PID checking cannot detect.

2. **Shared memory / named pipe heartbeat.** Electron writes a timestamp to a shared memory region or named pipe; Python reads it. This adds OS-specific IPC mechanisms beyond the existing TCP socket.

3. **TCP-level heartbeat over the existing IPC socket.** Since Electron and Python already communicate over a persistent TCP connection, the most natural heartbeat mechanism is to send periodic "I'm alive" messages over this same socket. If the socket closes (Electron crash kills the OS socket), the TCP handler detects this immediately. If the socket stays open but Electron stops sending heartbeats (Electron process still running but renderer thread hung), the watchdog detects the missing heartbeats after a configurable timeout. **Chosen.**

## Decision

Implement an **application-level heartbeat watchdog** over the existing TCP IPC socket:

### Protocol

1. **Heartbeat sender (Electron):** The Electron main process sends a `{"type": "heartbeat"}` IPC command every 5 seconds once the TCP connection is established. The heartbeat is sent by a `setInterval` in `client/src/main/index.ts`, started in the TCP connect callback.

2. **Heartbeat receiver (Python):** The `_handle_heartbeat` handler updates `self._last_heartbeat_at = time.monotonic()` on every received heartbeat. The handler returns a `{"type": "heartbeat_ack"}` response (fire-and-forget — Electron does not wait for the response).

3. **Watchdog thread (Python):** A daemon thread (`_heartbeat_loop`) wakes every 5 seconds and calls `_check_heartbeat_timeout()`. If the elapsed time since the last heartbeat exceeds `_HEARTBEAT_TIMEOUT_SECONDS` (120 seconds), the thread calls `self.app.quit()`.

### Timeout Constants

```
_HEARTBEAT_INTERVAL_SECONDS = 5.0   # How often Electron sends heartbeats
_HEARTBEAT_TIMEOUT_SECONDS = 120.0  # 24 missed heartbeats before quitting
```

The timeout was increased from 15 seconds (3 missed heartbeats) to 120 seconds (24 missed heartbeats) because model downloads block the Python IPC dispatch loop for their entire duration. A large model download can take 30-60 seconds, during which no heartbeats are processed. The 120-second window accommodates this without false-positive exits.

### First-Heartbeat Guard

The watchdog only fires **after** the first heartbeat has been received. While `_last_heartbeat_at` is `None`, the watchdog is silent. This prevents a false-positive exit during a slow Electron cold start (10+ seconds for the torch import and window creation).

### Cleanup Path

When `app.quit()` is called from the watchdog:
1. The shared `_do_cleanup()` path runs (RW-3), which:
   - Restores system volume (volume_ducker).
   - Flushes recovery data.
   - Releases the single-instance Win32 mutex.
   - Closes the PortAudio stream.
   - Stops the tray icon (breaks the pystray event loop).
2. The process exits cleanly, allowing the next Electron launch to succeed without hitting the "Only one instance" error.

## Consequences

### Easier
- **Automatic recovery:** If Electron crashes, the Python backend shuts down cleanly within 2 minutes, releasing all OS resources. The next launch works without manual intervention.
- **No zombie processes:** The watchdog ensures the backend does not linger indefinitely after the frontend dies.
- **Minimal overhead:** A 5-second timer and a `time.monotonic()` check. Negligible CPU cost.

### More difficult
- **False-positive risk during model downloads:** A model download blocks the IPC dispatch loop. If the download takes longer than the timeout, the watchdog fires and kills the backend during a download. Mitigated by the 120-second window (accommodates most model sizes).
- **Coordination with TCP connect:** The heartbeat must not start before the TCP connection is authenticated (SEC-018). The heartbeat interval is started in the TCP connect callback, after the auth line is sent.
- **Testability:** The 120-second timeout makes direct testing impractical. `_check_heartbeat_timeout()` is extracted as a public method so tests can invoke it directly without waiting 120 seconds.

### Risks
- **Timeout too long:** 120 seconds means the backend runs for up to 2 minutes without a healthy frontend. During this time, the user may start a new dictation session, which would proceed normally (the backend is fully functional without the frontend). Only after the timeout does the backend quit, losing any unsaved transcription. Acceptable trade-off — the timeout is generous enough to avoid false positives while still ensuring eventual cleanup.
- **Race on planned shutdown:** During a planned `stop()`, the heartbeat stop event is set but the watchdog thread may fire one last `_check_heartbeat_timeout()` check before it exits. The check calls `app.quit()` which calls `_do_cleanup()` again — `_do_cleanup()` is idempotent and gated by a flag, so the double call is handled safely.

## References

- `voice_typer/server/ipc_server.py` — `_HEARTBEAT_INTERVAL_SECONDS`, `_HEARTBEAT_TIMEOUT_SECONDS`, `_heartbeat_loop()`, `_check_heartbeat_timeout()`, `_handle_heartbeat()`.
- `voice_typer/client/src/main/index.ts` — heartbeat `setInterval` in TCP connect callback.
- `tests/test_heartbeat.py` — heartbeat watchdog regression suite. The 10 tests in `_HeartbeatWatchdogTests` (e.g. `test_fires_after_timeout`) plus the function-level `test_heartbeat_over_real_tcp_socket_updates_timestamp` exercise the watchdog logic directly via the extracted `_check_heartbeat_timeout()` method (no 120-second real-time wait). The prior draft of this ADR pointed at `tests/test_ipc_server.py::test_heartbeat_timeout_calls_quit()` — that function name never existed; the real heartbeat tests have always lived in `tests/test_heartbeat.py`.
- `tests/test_heartbeat_force_exit.py` — 8 force-exit backstop tests (`test_force_exit_*`) covering the `app.quit()` cleanup path the watchdog invokes once the 120-second timeout elapses. The prior draft pointed at `tests/test_feature_hardening_regressions.py::test_e2e_heartbeat_timeout()` — that file never existed; the force-exit backstop is the correct E2E-equivalent coverage.
- SECURITY.md — RW-10 documentation.

*End of document.*
