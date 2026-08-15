// useOfflinePackDownload — silent-mode offline-pack readiness hook.
//
// Models the offline-pack lifecycle introduced by the installer split
// (see `upload/plan-offline-pack-split.md` §7.4 + §8.4 + §8.10 + §4.9).
// The slim-core sidecar downloads + verifies + launches the pack worker
// in the background after the user grants the existing HuggingFace-style
// consent (§8.4 — the pack download phones home to GitHub Releases, so
// it MUST be consent-gated exactly like model downloads). The renderer
// never shows a progress bar (§4.8 — "no progress bar in the main UI"),
// only a small "Preparing offline engine…" line in the mic-test /
// transcription areas when the user attempts offline transcription
// before the pack is ready (§4.9 — local whisper / Parakeet degrade
// silently to "silent download starts, 'Preparing…' line, then works").
//
// ── Subscribed push events (§7.4 — 11 of the 13 new events) ──────────
//
// The hook subscribes to the 11 pack/worker lifecycle push events.
// The other 2 events in §7.4 (`transcribe_offline` request and
// `transcribe_offline_result` push) are per-transcription events, not
// pack-lifecycle events, so they don't belong here.
//
//   1.  `offline_pack_download_started`   — download kicked off (after consent)
//   2.  `offline_pack_download_progress`  — byte counter (silent — no UI surface)
//   3.  `offline_pack_download_completed` — bytes landed; verify step begins
//   4.  `offline_pack_download_failed`    — network/disk/proxy failure
//   5.  `offline_pack_verified`           — checksum verified; worker can start
//   6.  `offline_pack_missing`            — existence check found nothing (§8.10)
//   7.  `offline_pack_corrupt`            — checksum mismatch (§8.2)
//   8.  `offline_pack_ready`              — worker started + prewarmed (terminal)
//   9.  `worker_started`          — worker process up (not yet prewarmed)
//   10. `worker_crashed`          — worker exited unexpectedly
//   11. `worker_unloaded`         — slim-core asked worker to unload (RAM)
//
// NOTE on type-safety: the 11 event names are NOT yet in the
// `PythonPushEvent` union in `types/ipc/push_events.ts` — that file is
// owned by Sub-agent 8. We pass the event names as plain `string`
// literals, which falls through to the second overload of
// `usePythonEvent` (the forward-compat overload that accepts any
// string). A dev-time `console.warn` from `KNOWN_EVENT_TYPES` will
// surface until Sub-agent 8 adds the 11 literals to the union AND to
// `KNOWN_EVENT_TYPES` in `hooks/usePython.ts`. The runtime behaviour is
// correct either way — `usePythonEvent`'s dispatcher fans out by
// `event.type` regardless of union membership.
//
// ── Transport-agnostic ───────────────────────────────────────────────
//
// The hook only depends on `usePythonEvent`, which goes through the
// module-level dispatcher that subscribes to `window.python.onEvent`.
// The `window.python` namespace is installed by EITHER:
//   - the Electron preload script (`src/preload/index.ts`), OR
//   - the Tauri bridge auto-installer (`lib/tauri-bridge/install.ts`)
// at module-load time. `usePythonEvent`'s `useBridgeReady` hook polls
// `window.python` presence and re-subscribes when the bridge comes
// online late (Tauri timing edge), so this hook works identically
// under both runtimes. We do NOT touch Tauri or Electron APIs
// directly — see the contract documented at the top of
// `hooks/usePython.ts`.
//
// ── State machine ────────────────────────────────────────────────────
//
// `status` is the lifecycle stage of the pack+worker. `isReady` is
// `true` ONLY when `status === "ready"` (pack verified + worker
// started + prewarmed — the `offline_pack_ready` event). Every other state
// means offline transcription will either queue, fail, or degrade.
//
// Transitions (event → new status):
//   offline_pack_download_started  → "downloading"
//   offline_pack_download_progress → "downloading" (no-op if already)
//   offline_pack_download_completed → "verifying"
//   offline_pack_download_failed   → "failed"   (records `error`)
//   offline_pack_verified          → "worker-starting" (if not already "ready")
//   offline_pack_missing           → "missing"
//   offline_pack_corrupt           → "corrupt"  (records `error`)
//   offline_pack_ready             → "ready"    (clears `error`)
//   worker_started         → "worker-starting" (if not already "ready")
//   worker_crashed         → "worker-crashed" (records `error`)
//   worker_unloaded        → "worker-unloaded" (only from "ready";
//                            otherwise leave the existing status —
//                            `worker_unloaded` only fires when the
//                            slim core actively asks a RUNNING worker
//                            to unload, which can only happen after
//                            `offline_pack_ready`.)
//
// `error` is cleared on every successful transition (`offline_pack_ready`).
// Failure events overwrite `error` with the message from the event
// payload (`data.error` / `data.reason`) if present, otherwise leave
// the existing error in place (a transient progress event shouldn't
// wipe a recorded failure message).

import { useCallback, useState } from "react";
import { usePythonEvent } from "@/hooks/usePython";

// ── Types ─────────────────────────────────────────────────────────────

export type OfflinePackStatus =
	| "idle" // initial — no events received yet
	| "downloading" // offline_pack_download_started / offline_pack_download_progress
	| "verifying" // offline_pack_download_completed → about to verify
	| "ready" // offline_pack_ready (worker started + prewarmed)
	| "failed" // offline_pack_download_failed
	| "missing" // offline_pack_missing (deleted by AV/cleaner — §8.10)
	| "corrupt" // offline_pack_corrupt (checksum mismatch — §8.2)
	| "worker-starting" // worker_started but offline_pack_ready hasn't fired
	| "worker-crashed" // worker_crashed
	| "worker-unloaded"; // worker_unloaded (slim-core low-RAM unload)

export interface UseOfflinePackDownloadResult {
	/** Current lifecycle stage. See the state-machine comment above. */
	status: OfflinePackStatus;
	/** Latest error message from a failure / crash / corruption event.
	 *  Cleared on `offline_pack_ready`. `null` when no error has been recorded
	 *  (or when the latest event was a success). */
	error: string | null;
	/** `true` ONLY when `status === "ready"`. Consumers gate the
	 *  "Preparing offline engine…" UI on `!isReady` (plus a "user has
	 *  attempted offline transcription" flag they own). */
	isReady: boolean;
}

// ── Helpers ───────────────────────────────────────────────────────────

/** Extracts the first string value found at any of the given keys in
 *  the event payload. Different emitters use different field names
 *  (`error` vs `reason` vs `message`); this picks the first one present
 *  so failure surfaces don't drop the message based on a naming
 *  convention mismatch. */
function pickString(
	data: Record<string, unknown> | undefined,
	keys: string[],
): string | null {
	if (!data) return null;
	for (const key of keys) {
		const value = data[key];
		if (typeof value === "string" && value.length > 0) return value;
	}
	return null;
}

// ── Hook ──────────────────────────────────────────────────────────────

export function useOfflinePackDownload(): UseOfflinePackDownloadResult {
	const [status, setStatus] = useState<OfflinePackStatus>("idle");
	const [error, setError] = useState<string | null>(null);

	// ── offline_pack_download_started → "downloading" ─────────────────────────
	usePythonEvent(
		"offline_pack_download_started",
		useCallback((): (() => void) | undefined => {
			setStatus("downloading");
			setError(null);
			return undefined;
		}, []),
	);

	// ── offline_pack_download_progress → "downloading" (silent, no UI surface)
	//
	// §7.4 calls this event "silent — no UI". We still subscribe so the
	// status machine reflects "actively downloading" even if
	// `offline_pack_download_started` was missed (e.g. the renderer mounted
	// after the download already began). The handler is a no-op when
	// the status is already "downloading".
	usePythonEvent(
		"offline_pack_download_progress",
		useCallback((): (() => void) | undefined => {
			setStatus((prev) => (prev === "downloading" ? prev : "downloading"));
			return undefined;
		}, []),
	);

	// ── offline_pack_download_completed → "verifying" ────────────────────────
	usePythonEvent(
		"offline_pack_download_completed",
		useCallback((): (() => void) | undefined => {
			setStatus("verifying");
			return undefined;
		}, []),
	);

	// ── offline_pack_download_failed → "failed" (record error) ───────────────
	usePythonEvent(
		"offline_pack_download_failed",
		useCallback((data?: Record<string, unknown>): (() => void) | undefined => {
			setStatus("failed");
			const msg = pickString(data, ["error", "message", "reason"]);
			if (msg !== null) setError(msg);
			return undefined;
		}, []),
	);

	// ── offline_pack_verified → "worker-starting" (pack OK, worker not yet up)
	//
	// `offline_pack_ready` is the terminal event; `offline_pack_verified` is an
	// intermediate "checksum OK, worker about to start" signal. We
	// transition to "worker-starting" unless we're already at "ready"
	// (a late `offline_pack_verified` after `offline_pack_ready` shouldn't downgrade).
	usePythonEvent(
		"offline_pack_verified",
		useCallback((): (() => void) | undefined => {
			setStatus((prev) => (prev === "ready" ? prev : "worker-starting"));
			return undefined;
		}, []),
	);

	// ── offline_pack_missing → "missing" (§8.10 — deleted by cleaner/AV) ─────
	//
	// The slim-core launch-time existence check found no pack dir.
	// A silent re-download is queued; we surface the status so the
	// "Preparing…" banner can show the right copy if the user attempts
	// offline transcription in the meantime.
	usePythonEvent(
		"offline_pack_missing",
		useCallback((): (() => void) | undefined => {
			setStatus("missing");
			return undefined;
		}, []),
	);

	// ── offline_pack_corrupt → "corrupt" (§8.2 — checksum mismatch) ──────────
	usePythonEvent(
		"offline_pack_corrupt",
		useCallback((data?: Record<string, unknown>): (() => void) | undefined => {
			setStatus("corrupt");
			const msg = pickString(data, ["reason", "error", "message"]);
			if (msg !== null) setError(msg);
			return undefined;
		}, []),
	);

	// ── offline_pack_ready → "ready" (terminal — clears error) ───────────────
	usePythonEvent(
		"offline_pack_ready",
		useCallback((): (() => void) | undefined => {
			setStatus("ready");
			setError(null);
			return undefined;
		}, []),
	);

	// ── worker_started → "worker-starting" (worker up, not prewarmed)
	usePythonEvent(
		"worker_started",
		useCallback((): (() => void) | undefined => {
			setStatus((prev) => (prev === "ready" ? prev : "worker-starting"));
			return undefined;
		}, []),
	);

	// ── worker_crashed → "worker-crashed" (record reason) ────────────
	usePythonEvent(
		"worker_crashed",
		useCallback((data?: Record<string, unknown>): (() => void) | undefined => {
			setStatus("worker-crashed");
			const msg = pickString(data, ["reason", "error", "message"]);
			if (msg !== null) setError(msg);
			return undefined;
		}, []),
	);

	// ── worker_unloaded → "worker-unloaded" (only from "ready") ──────
	//
	// §7.3 says the slim core can unload the worker under RAM pressure
	// and restart it on next transcription. `worker_unloaded` only
	// fires when the worker was actually running (i.e. after
	// `offline_pack_ready`), so we only transition from "ready" →
	// "worker-unloaded". From any other state the event is unexpected
	// and we leave the existing status alone (defensive — avoids
	// wiping a "failed" / "missing" / "corrupt" status on a stray
	// late-arriving `worker_unloaded`).
	usePythonEvent(
		"worker_unloaded",
		useCallback((): (() => void) | undefined => {
			setStatus((prev) => (prev === "ready" ? "worker-unloaded" : prev));
			return undefined;
		}, []),
	);

	const isReady = status === "ready";

	return { status, error, isReady };
}
