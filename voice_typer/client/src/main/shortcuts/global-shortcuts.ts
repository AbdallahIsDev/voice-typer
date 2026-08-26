/**
 * Main-process OS-global shortcut registration.
 *
 * Registers `CommandOrControl+Shift+D` as a SYSTEM-WIDE accelerator
 * (works even when no app window has focus) that dismisses the
 * dictation bubble. The callback delegates to
 * `ipc/bubble-handlers.ts::dismissAndHideBubble` — the exact same
 * cancel-then-hide body the bubble's own '×' button uses — so the
 * keyboard path can never drift from the click path (in-flight
 * recordings are cancelled via `toggle_dictation` before hiding).
 *
 * Lifecycle contract:
 *   - `registerGlobalShortcuts()` MUST be called after
 *     `app.whenReady()` resolves — Electron's `globalShortcut` is not
 *     usable before the app is ready (same constraint as
 *     `powerMonitor`; see `power.ts`). The production call site in
 *     `index.ts::app.whenReady().then(...)` honors this.
 *   - `unregisterGlobalShortcuts()` runs in the `will-quit` path so a
 *   quit that hangs in backend teardown never leaves the accelerator
 *   firing against a half-dead process.
 *
 * Failure policy: registration is best-effort. The OS may refuse the
 * accelerator (taken by another application — Electron returns
 * `false` silently by design, since "operating systems don't want
 * applications to fight for global shortcuts"). A failed registration
 * logs a warning and the rest of the app continues to work; losing
 * the global dismiss hotkey is graceful degradation, not a hard
 * failure.
 *
 * Idempotency: both functions are guarded by a module-level flag, so
 * double register / double unregister (tests, defensive re-calls) are
 * safe no-ops and never stack duplicate callbacks.
 */
import { globalShortcut } from "electron";
import { dismissAndHideBubble } from "../ipc/bubble-handlers";
import { log } from "../logging";

/**
 * Single source of truth for the bubble-dismiss accelerator string.
 * The renderer's shortcuts catalog (`components/hotkey/shortcuts.ts`)
 * pins the user-facing display form ("Ctrl+Shift+D"); this constant is
 * the Electron accelerator form of the same binding.
 */
export const BUBBLE_DISMISS_ACCELERATOR = "CommandOrControl+Shift+D";

let _globalShortcutsRegistered = false;

/**
 * Test-only: reset the idempotency guard so a fresh test case can
 * re-invoke `registerGlobalShortcuts()`. Mirrors the
 * `_resetPowerMonitorHandlersForTest` convention in `power.ts`.
 */
export function _resetGlobalShortcutsForTest(): void {
	_globalShortcutsRegistered = false;
}

/**
 * Register the bubble-dismiss global shortcut. Idempotent — see the
 * module-level flag above. Non-fatal on failure: an accelerator taken
 * by another app (register returns `false`) or a broken
 * `globalShortcut` module (test mocks, unusual Electron builds) only
 * warns; dismissal stays available through the bubble's own '×' button.
 */
export function registerGlobalShortcuts(): void {
	if (_globalShortcutsRegistered) return;
	_globalShortcutsRegistered = true;

	try {
		const registered = globalShortcut.register(
			BUBBLE_DISMISS_ACCELERATOR,
			() => {
				try {
					dismissAndHideBubble();
				} catch (e) {
					log.warn("[shortcuts] dismiss handler failed:", e);
				}
			},
		);
		if (!registered) {
			log.warn(
				`[shortcuts] global accelerator ${BUBBLE_DISMISS_ACCELERATOR} could not be registered (already taken by another application?) — non-fatal`,
			);
		}
	} catch (e) {
		log.warn("[shortcuts] registerGlobalShortcuts failed (non-fatal):", e);
	}
}

/**
 * Unregister the bubble-dismiss global shortcut. Idempotent and
 * best-effort — called from the `will-quit` path, where any error must
 * not block shutdown.
 */
export function unregisterGlobalShortcuts(): void {
	if (!_globalShortcutsRegistered) return;
	_globalShortcutsRegistered = false;
	try {
		globalShortcut.unregister(BUBBLE_DISMISS_ACCELERATOR);
	} catch (e) {
		log.warn("[shortcuts] unregisterGlobalShortcuts failed:", e);
	}
}
