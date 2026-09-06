/**
 * powerMonitor lifecycle bridge.
 *
 * Previously the main process had NO `powerMonitor`
 * listeners anywhere. On a laptop suspend (lid close, low-battery
 * hibernate, manual `systemctl suspend`) the OS freezes the Electron
 * process AND the Python sidecar together — but the TCP socket between
 * them silently goes stale, the heartbeat watchdog keeps ticking
 * against a frozen socket, and on resume the first thing the user
 * sees is a "Python backend not connected" toast because the socket
 * never recovered. On `on-battery` the heartbeat interval should
 * tighten so a frozen backend is caught sooner (default 15s is too
 * generous on battery where the OS may also be throttling).
 *
 * This module registers three listeners on Electron's `powerMonitor`:
 *
 *   • `suspend`  — stop the Python backend so its socket / audio
 *     streams / model GPU memory are released cleanly BEFORE the OS
 *     freezes the process. `stopPython()` is graceful (sends
 *     `quit_app`, force-kills after 3s) so the backend's
 *     `atexit`-style flush of `history_db` + audio stream close +
 *     single-instance mutex release runs first (C-NEVER-DOWNGRADE /
 *     rule 4: powerMonitor suspend must not lose user data).
 *
 *   • `resume`   — re-spawn the backend via `startPython()`. The
 *     Python backend re-arms its heartbeat interval on boot and
 *     re-enumerates the mic list (the OS may have changed audio
 *     devices during sleep — USB mics unplugged, Bluetooth headset
 *     re-paired, etc.). We do NOT need to manually refresh the mic
 *     list here; `startPython()` → backend boot →
 *     `microphone_watcher` re-runs its device enumeration.
 *
 *   • `on-battery` — best-effort tightening. The Python backend
 *     already backs off prewarm on battery; here we just log the
 *     transition so the operator can see it in `electron-runtime.log`.
 *     A future iteration could send a `set_config` IPC to tighten the
 *     heartbeat, but that requires a config schema change owned by
 *     another lane — out of scope here (rule 9: stay in your
 *     lane).
 *
 * All three handlers are wrapped in try/catch — `powerMonitor` events
 * can fire during app teardown when `state.pythonProcess` is already
 * null. `stopPython()` and `startPython()` are both idempotent (see
 * `stop-python.ts`'s `isStopping`/`isStopped` guard and
 * `start-python.ts`'s live-process early-exit — it returns without
 * spawning while `state.pythonProcess` is set and its
 * `exitCode`/`signalCode` are both still `null`), so calling them when
 * the backend is already stopped / already running is a safe no-op.
 *
 * C-DATA-1: powerMonitor is a local OS event (no network) — the
 * `suspend` / `resume` / `on-battery` events come from the OS power
 * subsystem, not from any remote endpoint. This module makes ZERO
 * network calls.
 *
 * Idempotency: `registerPowerMonitorHandlers()` is idempotent — a
 * module-level flag prevents stacking duplicate listeners across
 * repeated calls (e.g. in tests via `vi.resetModules()`, or in dev
 * HMR). The production call site (`index.ts::app.whenReady()`)
 * invokes it exactly once.
 */
import { powerMonitor } from "electron";
import { log } from "./logging";
import { startPython, stopPython } from "./python";

/**
 * Module-level idempotency guard. `true` once the three
 * `powerMonitor.on(...)` listeners have been registered. Prevents
 * duplicate listeners if `registerPowerMonitorHandlers()` is called
 * more than once (tests, HMR, defensive double-call from a future
 * refactor).
 */
let _powerMonitorHandlersRegistered = false;

/**
 * Test-only: reset the idempotency guard so a fresh test case can
 * re-invoke `registerPowerMonitorHandlers()` and assert the listeners
 * were registered. Underscore-prefixed to signal "internal/test-only"
 * — mirrors the existing `_resetNativeThemeListenerForTest`
 * convention in `windows/main-window.ts`.
 *
 * Does NOT remove the already-registered listeners from
 * `powerMonitor` (production tests mock `powerMonitor` per-file via
 * `vi.mock("electron")`, so each test file gets a fresh mock and the
 * stale listeners don't leak across files).
 */
export function _resetPowerMonitorHandlersForTest(): void {
	_powerMonitorHandlersRegistered = false;
}

/**
 * Test-only accessor: returns whether the three powerMonitor
 * listeners have been registered. Used by `power.test.ts` to assert
 * idempotency (calling N times still leaves the flag flipped once).
 */
export function _powerMonitorHandlersRegisteredForTest(): boolean {
	return _powerMonitorHandlersRegistered;
}

/**
 * Register the `suspend` / `resume` / `on-battery` listeners on
 * Electron's `powerMonitor`. Idempotent — see
 * `_powerMonitorHandlersRegistered` above.
 *
 * Must be called AFTER `app.whenReady()` resolves (Electron's
 * `powerMonitor` is not usable before the app is ready — its
 * internal `PowerObserver` is initialized during
 * `ElectronMain::OnPreReady`). The production call site in
 * `index.ts::app.whenReady().then(...)` honors this.
 *
 * Wrap each listener body in try/catch so a throw inside the handler
 * (e.g. `startPython` fails because the Python binary is missing
 * after an OS update during sleep) doesn't take down the whole
 * process — the user can still close the lid and try again.
 */
export function registerPowerMonitorHandlers(): void {
	if (_powerMonitorHandlersRegistered) return;
	_powerMonitorHandlersRegistered = true;

	// Defensive guard: wrap the `powerMonitor.on(...)` registrations
	// in try/catch so a missing / broken `powerMonitor` (test mocks
	// that don't expose it, hypothetical older Electron versions
	// where it's not yet initialized at `whenReady` time, Electron
	// variants like Electron-Forge test runners) doesn't crash the
	// main process. The idempotency flag is already set above so a
	// failure here doesn't retry on every `bootstrapRuntime()` call
	// — the user gets one warning per process lifetime and the rest
	// of the app continues to work (just without suspend/resume
	// handling, which is a graceful degradation, not a hard
	// failure).
	try {
		registerPowerMonitorHandlersInner();
	} catch (e) {
		log.warn(
			"[power] registerPowerMonitorHandlers failed (non-fatal — suspend/resume handling disabled):",
			e,
		);
	}
}

/**
 * Inner registration — separated so the outer try/catch can catch the
 * `powerMonitor` property-access errors thrown by vitest's mock Proxy
 * (and any other module-load-time access errors). Each `.on(...)`
 * call is also individually guarded so a throw on one event doesn't
 * skip the others.
 */
function registerPowerMonitorHandlersInner(): void {
	// suspend: OS is about to freeze the process. Stop Python
	// gracefully so its atexit hooks flush history_db, close audio
	// streams, and release the single-instance mutex BEFORE the
	// freeze. Without this, on resume the socket is stale, the
	// heartbeat watchdog fires spurious "Python not responding"
	// toasts, and the mutex is held against the next launch
	// attempt.
	//
	// C-NEVER-DOWNGRADE / rule 4: stopPython() sends `quit_app`
	// over TCP and waits up to 3s for graceful exit before
	// SIGKILL — this gives the backend's history_db flush +
	// audio stream close time to complete, so user data is not
	// lost on suspend.
	powerMonitor.on("suspend", () => {
		log.info("[power] suspend — stopping Python backend gracefully");
		try {
			stopPython();
		} catch (e) {
			log.warn("[power] stopPython() during suspend failed:", e);
		}
	});

	// resume: OS unfroze the process. Re-spawn the backend so the
	// user can immediately dictate again. `startPython()` is
	// idempotent — if the backend somehow survived the suspend
	// (rare: desktop on AC power with `systemctl suspend`
	// inhibited by another app), its live-process early-exit guard
	// (a non-null `state.pythonProcess` whose `exitCode` and
	// `signalCode` are both still `null`) makes this a no-op
	// instead of double-spawning into the single-instance mutex.
	powerMonitor.on("resume", () => {
		log.info("[power] resume — re-arming Python backend + heartbeats");
		try {
			startPython();
		} catch (e) {
			log.warn("[power] startPython() during resume failed:", e);
		}
	});

	// on-battery: OS reports the device switched to battery power.
	// The Python backend's prewarm scheduler already backs off on
	// battery; here we just log the transition for operator
	// visibility in `electron-runtime.log`. A future iteration
	// could tighten the heartbeat interval via a `set_config`
	// IPC, but that requires a config-schema change owned by
	// another lane (rule 9: stay in your lane).
	powerMonitor.on("on-battery", () => {
		log.info(
			"[power] on-battery — backend prewarm should back off; consider tightening heartbeat",
		);
	});
}
