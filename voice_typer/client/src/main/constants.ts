/**
 * Top-level process constants for the Electron main process.
 *
 * Extracted from `index.ts` (REF-2). These are computed once at module
 * load and shared across python/, windows/, ipc/, and bootstrap/.
 */
import { randomBytes } from "node:crypto";

// P1-1.2: VT_PYTHON_PORT lets a Python backend that spawned us tell us
// where it's already listening.  When set, startPython() skips spawning
// a fresh backend and connects directly to 127.0.0.1:VT_PYTHON_PORT.
// Falls back to the legacy default (9876) when not set.
export const IPC_PORT = process.env.VT_PYTHON_PORT
	? parseInt(process.env.VT_PYTHON_PORT, 10)
	: 9876;

// SEC-018: per-launch random session token.  Passed to the Python
// subprocess via the VOICE_TYPER_IPC_TOKEN env var and sent as the
// first JSON line after TCP connect.  The Python IPC server validates
// it and drops the connection if it doesn't match.  This prevents any
// local process from connecting to 127.0.0.1:9876 and sending
// quit_app / set_config / etc.
//
// Generated once per Electron process lifetime using crypto.randomBytes
// (32 bytes = 256 bits of entropy, hex-encoded for transport).
//
// P1-1.2: when a Python backend spawned us (VT_IPC_TOKEN env var set),
// we reuse its token instead of generating a new one — otherwise the
// backend's auth check would reject our connection.
export const IPC_TOKEN =
	process.env.VT_IPC_TOKEN || randomBytes(32).toString("hex");

// When set (autostart at login), the dashboard window is created hidden.
// The process + tray + bubble still work; the window appears on demand
// via the Start Menu (second-instance) or tray "Open app".
export const START_HIDDEN = process.env.VT_START_HIDDEN === "1";

// Bubble geometry (logical px).
//BUBBLE- (Round 0): BUBBLE_HEIGHT bumped from 27 → 46 to match
// the actual pill height (h-6 wrapper 24px + py-2.5 20px + border 2px = 46px).
// The previous 27px caused the pill to be clipped for the entire 180ms
// enter animation, then the renderer's useLayoutEffect resize caused a
// sudden snap to full size — the "cut-off then flash" artifact. With the
// correct initial height, the first frame is already full-size and the
// subsequent resize is a no-op (or sub-pixel adjustment).
export const BUBBLE_WIDTH = 74;
export const BUBBLE_HEIGHT = 46;

//heartbeat interval.  Once the Python backend is connected (TCP
// auth succeeded), we send a ``heartbeat`` IPC every 15 seconds.  The
// backend's heartbeat-watchdog daemon thread calls ``app.quit()`` if 3
// consecutive heartbeats are missed (45s timeout) so a crashed /
// force-killed Electron doesn't strand the backend with the mic open,
// hotkeys registered, volume ducked, and the single-instance mutex held.
//bumped from 5s to 15s to reduce idle CPU wakeups on laptops on
// battery. Same detection window (45s = 3 misses) as the prior 5s+45s
// (9 misses) config — a crashed peer is still detected within 45s.
export const HEARTBEAT_INTERVAL_MS = 15000;

//named magic numbers previously inlined across `main/`.
// Keeping them in one module makes the rationale (e.g. "why 3s vs 2s
// for the SIGTERM vs production-exit backstop") discoverable and lets a
// future tuning change touch one site instead of N.

// SIGTERM/SIGINT backstop: if `app.quit()` (called from the signal
// handler) doesn't actually exit the process within 3s, force-exit so a
// wedged `before-quit` listener can't trap us on SIGTERM. Longer than
// the production-exit backstop below because signal handlers also wait
// on the Python shutdown IPC handshake.
export const SIGTERM_EXIT_BACKSTOP_MS = 3000;

// Production-exit backstop in `bootstrap.ts::productionExit()`: if
// `app.quit()` doesn't exit within 2s (e.g. a `before-quit` handler
// called `event.preventDefault()` or the Python shutdown ack hangs),
// force-exit so the user isn't left with a zombie process.
export const PROCESS_EXIT_BACKSTOP_MS = 2000;

// Crash-storm backoff: 2s wait before reloading a renderer that crashed
// (`render-process-gone`) to avoid CPU-bound crash loops. Lives in
//`main-window.ts` (was previously in `bubble-window.ts` before
// split the bubble-window god-file).
export const RENDER_RELOAD_BACKOFF_MS = 2000;

// SEC-023: cap `tcpBuffer` at 1 MiB to prevent unbounded memory growth
// from malformed frames (e.g. a chunk with no newline that never gets
// split). `tcp-connect.ts` drops the connection on overflow.
//
// Aligned with Python sidecar_ws._MAX_FRAME_BYTES (1 MiB) and Rust
// util.rs MAX_FRAME_BYTES (1 MiB). The Python TCP sender also caps
// outbound frames at 1 MiB (ipc/sender.py::_TCP_MAX_OUTBOUND_BYTES),
// so no Python-side caller can ever emit a TCP frame larger than this —
// the previous 4 MiB cap was dead headroom that silently allowed a
// 4x divergence between the TS TCP path and the WS path's 1 MiB ceiling.
export const TCP_FRAME_MAX_BYTES = 1 * 1024 * 1024;

//IPC command timeouts in `send-to-python.ts::_commandTimeoutMs`.
// Long-running commands (model load, transcription) get the long
// timeout; everything else gets the short timeout. Per-command overrides
// live in `_SHORT_TIMEOUT_COMMANDS`.
export const IPC_TIMEOUT_SHORT_MS = 15_000;
export const IPC_TIMEOUT_LONG_MS = 120_000;
