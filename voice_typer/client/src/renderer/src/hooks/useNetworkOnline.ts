// useNetworkOnline — network-is-back trigger for the auto-update flow.
//
// Implements the renderer-side half of plan-runtime-pack-split.md §10.1
// "Network-is-back trigger": when the browser fires the `online` event
// (navigator.onLine transitions false → true), the hook calls the Python
// IPC command `check_pack_update` so the slim core re-fetches the
// latest `pack-manifest.json` from GitHub Releases and (if a newer pack
// is available) restarts the background download.
//
// ── Why renderer-side (not Rust `tauri-plugin-network`) ───────────────
//
// The plan offers two options for the network-online trigger:
//   1. Tauri's `window.addEventListener('online')` in the renderer.
//   2. `tauri-plugin-network` on the Rust side.
//
// We pick option (1) because:
//   - Sub-agent 10 owns Rust spawn + the Rust file surface; touching
//     `src-tauri/` to add `tauri-plugin-network` would collide with
//     their changes.
//   - The `online` / `offline` browser events are stable, well-
//     documented, and fire on both Electron (Chromium) and Tauri v2
//     (WebView2 / WKWebView) — no platform-specific code needed.
//   - The renderer already has the `call` IPC bridge (via
//     `usePython()`); re-using it avoids a second transport.
//
// ── Subscribes to browser events, calls into usePackDownload's API ────
//
// The hook does NOT directly call `usePackDownload` (Sub-agent 9 owns
// that hook and it exposes only read state — `{ status, error, isReady }`).
// Instead, the hook consumes `usePackDownload`'s STATE indirectly:
//   - When `online` fires, the hook calls `call("check_pack_update", {})`.
//   - The Python side (`update_check.py`) re-fetches the manifest + may
//     trigger `pack.download_pack_with_resume`, which publishes
//     `pack_download_started` / `pack_download_progress` /
//     `pack_download_completed` events.
//   - `usePackDownload` (Sub-agent 9's hook) is ALREADY subscribed to
//     those events and updates its `status` accordingly.
//
// So the chain is:
//   `online` event → `useNetworkOnline` → IPC `check_pack_update` →
//   Python `update_check.check_pack_update()` → `pack.download_pack_with_resume()`
//   → event_bus publishes `pack_download_started` → `usePackDownload`
//   updates `status` → UI re-renders.
//
// This keeps the network-online trigger in its OWN file (Sub-agent 13's
// ownership) while delegating the pack-lifecycle state machine to
// `usePackDownload` (Sub-agent 9's ownership). No file collision.
//
// ── Forward-compat: the IPC command may not be registered yet ────────
//
// `check_pack_update` is exposed by `voice_typer/server/service/update_check.py`
// (`handle_check_pack_update_ipc`) but NOT auto-registered in
// `voice_typer/server/ipc/registry.py` (registry is a shared file —
// Sub-agent 7 owns the pack-related additions). Until the command is
// wired into the registry + the TS allowlist (`allowed-commands.ts`) +
// the Rust allowlist (`allowlist.rs`), the `call("check_pack_update")`
// will reject. The hook catches the rejection and logs at debug — the
// `isOnline` state still updates correctly, so UI consumers can react
// to network changes regardless of whether the Python side is wired.
//
// When Sub-agent 7 (or a future integration agent) registers the
// command, the hook starts working end-to-end without any change here.
//
// ── Transport-agnostic ───────────────────────────────────────────────
//
// Like `usePackDownload`, this hook depends only on `usePython` (which
// goes through the module-level dispatcher that subscribes to
// `window.python.onEvent`). The `window.python` namespace is installed
// by EITHER the Electron preload script OR the Tauri bridge auto-
// installer at module-load time. We do NOT touch Tauri or Electron
// APIs directly.

import { useCallback, useEffect, useRef, useState } from "react";
import { usePython } from "@/hooks/usePython";

// ── Types ─────────────────────────────────────────────────────────────

export interface UseNetworkOnlineResult {
	/** `true` when `navigator.onLine` is true (or SSR / no-window env
	 *  where we assume online). Updates on `online` / `offline` browser
	 *  events. */
	isOnline: boolean;
	/** Epoch ms (Date.now()) of the most recent `online` event, or
	 *  `null` if the network has never come online during this hook's
	 *  lifetime. Consumers can use this to dedupe re-checks (e.g. only
	 *  call `check_pack_update` once per transition). */
	lastOnlineAt: number | null;
	/** Manually trigger a re-check (calls the `check_pack_update` IPC
	 *  command). Exposed for Settings → "Check for updates now" buttons
	 *  and for tests. Returns the IPC result dict (or undefined on
	 *  error / bridge-not-ready). */
	triggerRecheck: () => Promise<Record<string, unknown> | undefined>;
	/** `true` while a re-check IPC call is in flight. Consumers can
	 *  show a spinner / disable the "Check now" button. */
	isChecking: boolean;
	/** The last error from a `triggerRecheck` call, or `null` if the
	 *  last call succeeded (or no call has been made yet). Cleared at
	 *  the start of each new call. */
	error: string | null;
}

// ── Helpers ───────────────────────────────────────────────────────────

/** Read the initial `navigator.onLine` state. Returns `true` when
 *  `navigator` is undefined (SSR / Node test env without jsdom) so the
 *  hook's default state is "online" — the safe default (a false
 *  "offline" would block UI that gates on `isOnline`). */
function readInitialOnline(): boolean {
	if (typeof navigator === "undefined") return true;
	// `navigator.onLine` is `true` by default in browsers; in jsdom it
	// is also `true` unless a test explicitly sets it.
	return navigator.onLine !== false;
}

// ── Hook ──────────────────────────────────────────────────────────────

export function useNetworkOnline(): UseNetworkOnlineResult {
	const [isOnline, setIsOnline] = useState<boolean>(readInitialOnline);
	const [lastOnlineAt, setLastOnlineAt] = useState<number | null>(null);
	const [isChecking, setIsChecking] = useState<boolean>(false);
	const [error, setError] = useState<string | null>(null);

	// Refs to avoid stale closures inside the event listeners.
	const isOnlineRef = useRef<boolean>(isOnline);
	isOnlineRef.current = isOnline;
	const callRef = useRef<
		((type: string, data?: Record<string, unknown>) => Promise<unknown>) | null
	>(null);

	const pythonApi = usePython();
	// `usePython` returns a stable `call` (wrapped in `useCallback`).
	// Stash it in a ref so the `online` event listener (added once on
	// mount) always invokes the latest `call` without re-subscribing.
	callRef.current = pythonApi.call as typeof callRef.current;

	// ── triggerRecheck ───────────────────────────────────────────────
	//
	// Calls the `check_pack_update` IPC command. The command is exposed
	// by `voice_typer/server/service/update_check.py` (function
	// `handle_check_pack_update_ipc`). If the command is not yet
	// registered in `ipc/registry.py`, the call rejects — we catch and
	// log at debug (the `isOnline` state still updates correctly).
	const triggerRecheck = useCallback(async (): Promise<
		Record<string, unknown> | undefined
	> => {
		const call = callRef.current;
		if (!call) {
			setError("Python bridge not available");
			return undefined;
		}
		setIsChecking(true);
		setError(null);
		try {
			const result = (await call("check_pack_update", {})) as Record<
				string,
				unknown
			>;
			return result;
		} catch (err) {
			// Forward-compat: the IPC command may not be registered yet.
			// Log at debug so a missing registration doesn't spam the
			// console, but surface the error in `state.error` so UI
			// consumers can show "Update check unavailable" if they want.
			const msg = err instanceof Error ? err.message : String(err);
			console.debug(
				"[useNetworkOnline] check_pack_update IPC call failed — is the command registered in ipc/registry.py?",
				err,
			);
			setError(msg);
			return undefined;
		} finally {
			setIsChecking(false);
		}
	}, []);

	// ── Subscribe to `online` / `offline` browser events ────────────
	//
	// Added once on mount; cleaned up on unmount. The listeners update
	// `isOnline` state; the `online` listener ALSO triggers a re-check
	// via `triggerRecheck` (but only on the false → true transition,
	// not on every `online` event — browsers can fire `online` multiple
	// times in quick succession during a flaky connection, and we
	// don't want to spam the IPC).
	useEffect(() => {
		if (typeof window === "undefined") return;

		const handleOnline = () => {
			const wasOnline = isOnlineRef.current;
			setIsOnline(true);
			setLastOnlineAt(Date.now());
			// Only trigger a re-check on the false → true transition.
			// A duplicate `online` event while already online is a no-op
			// (browsers fire these during connection flapping).
			if (!wasOnline) {
				// Fire-and-forget — the IPC result is handled by the
				// `usePackDownload` hook (via the `pack_download_started`
				// event the Python side publishes). We don't need to
				// await it here.
				void triggerRecheck();
			}
		};
		const handleOffline = () => {
			setIsOnline(false);
		};

		window.addEventListener("online", handleOnline);
		window.addEventListener("offline", handleOffline);
		return () => {
			window.removeEventListener("online", handleOnline);
			window.removeEventListener("offline", handleOffline);
		};
	}, [triggerRecheck]);

	return { isOnline, lastOnlineAt, triggerRecheck, isChecking, error };
}
