// Bridge-ready subscription via useSyncExternalStore.
//
// `usePythonEvent` previously returned early from its `useEffect` when
// `window.python` was undefined at mount, and the effect's only
// dependency was `[type]` — so if `window.python` was installed later
// (e.g. by the Tauri bridge's auto-install on first import, or by the
// Electron preload under slow HMR), the subscription was never
// re-attempted and events were silently dropped.
//
// `useBridgeReady` polls `window.python` presence every 100ms until it
// appears, then notifies React via the `useSyncExternalStore` callback.
// Including `bridgeReady` in the effect's dependency array causes the
// effect to re-run when `window.python` becomes available, so the
// subscription is created lazily on first bridge availability.

import { useSyncExternalStore } from "react";

// Import `installTauriBridge` so `subscribeBridgeReady` can
// re-trigger the installer when the Tauri runtime appears AFTER the
// initial module-import-time auto-install ran (which no-op'd because
// the Tauri global wasn't yet present — a rare timing edge under
// Tauri v2 with `withGlobalTauri: true`). The installer itself is
// idempotent (no-ops when not in Tauri mode or when the namespaces
// are already installed), so the hook stays transport-agnostic:
// it never touches Tauri or Electron APIs directly, only
// `window.python` + the idempotent installer.
import { installTauriBridge } from "@/lib/tauri-bridge";

function subscribeBridgeReady(callback: () => void): () => void {
	// Short-circuit when the bridge is already installed at
	// subscribe time. `useSyncExternalStore` calls `subscribe` once per
	// component instance, so without this guard each mounted consumer
	// (`usePythonEvent` callers) would spin up its own 100ms polling
	// interval even though `window.python` is already present. In the
	// normal production path the preload/bridge install runs before
	// React mounts, so this short-circuit eliminates all 12+ polling
	// intervals that would otherwise tick forever (the snapshot never
	// flips back to `false`, so `clearInterval` only fires on unmount).
	// Returning a no-op cleanup matches the contract: subscribers must
	// return an unsubscribe function.
	if (getBridgeReadySnapshot()) return () => {};
	// Poll every 100ms until window.python is available, then stop.
	// The interval self-clears on first detection to avoid leaking a
	// timer once the bridge is installed.
	//
	// If `window.python` is already set at subscribe time, the first
	// tick (≤100ms later) detects it and calls `callback()`. React
	// re-renders, `getSnapshot()` returns the same `true`, and the
	// effect (which already ran with `bridgeReady=true` on the
	// initial render) does not re-run — so the no-op re-render is
	// harmless.
	//
	// Also detect the Tauri runtime appearing AFTER the
	// initial module-import-time auto-install. The auto-install in
	// the tauri-bridge installer runs once at module load — if
	// the Tauri global isn't yet present (rare timing edge under
	// Tauri v2 with `withGlobalTauri: true`), the auto-install
	// no-ops and `window.python` is never installed. The previous
	// code polled `window.python` forever with NO mechanism to
	// re-trigger the installer. We now re-invoke
	// `installTauriBridge()` (idempotent — no-ops if not in Tauri
	// mode or if already installed) on every tick, which installs
	// the three namespaces, and the next tick's
	// `window.python` check then succeeds and notifies React.
	const interval = setInterval(() => {
		if (typeof window.python !== "undefined") {
			callback();
			clearInterval(interval);
			return;
		}
		// The Tauri global appeared after the auto-install
		// no-op'd — re-trigger the installer. The installer is
		// idempotent: it no-ops again if the runtime isn't fully
		// ready yet (e.g. the global is partial) and the next
		// tick retries.
		try {
			installTauriBridge();
		} catch (err) {
			// Defensive: a partially-mocked global
			// (e.g. in tests) could throw inside a
			// namespace installer. Surface the error
			// so it's debuggable instead of silently
			// looping forever.
			console.warn(
				"[renderer:usePython] installTauriBridge retry failed:",
				err,
			);
		}
	}, 100);
	return () => clearInterval(interval);
}

function getBridgeReadySnapshot(): boolean {
	return typeof window.python !== "undefined";
}

function getBridgeReadyServerSnapshot(): boolean {
	// During SSR (no `window`), the bridge is never ready. Vitest's
	// jsdom env always has `window`, so this only fires in true SSR.
	return false;
}

/**
 * Returns `true` once `window.python` is installed (by the Electron
 * preload script or by `installTauriBridge()`). Re-render-safe via
 * `useSyncExternalStore`: the snapshot is a stable boolean.
 *
 * Used by `usePythonEvent` to re-attempt the event subscription
 * when the bridge becomes available after mount.
 */
export function useBridgeReady(): boolean {
	return useSyncExternalStore(
		subscribeBridgeReady,
		getBridgeReadySnapshot,
		getBridgeReadyServerSnapshot,
	);
}
