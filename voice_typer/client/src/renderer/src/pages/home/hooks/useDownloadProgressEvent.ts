// useDownloadProgressEvent — model-download progress bar state for the
// Home page, extracted from Home.tsx so the page file stays a thin
// composition root.
//
// Subscribes to `download_progress` events emitted while any model
// download is in flight (whichever page started it — the Home bar
// exists to make an in-flight download visible wherever the user is).
// The wire payload is `{ model, progress (0-100 int), status, +optional
// downloaded_bytes, total_bytes, speed_bytes_per_sec, eta_seconds,
// paused, resumed, queue_position }` (server `push_progress`,
// voice_typer/server/service/_download_helpers.py). NOTE the field is
// `progress`, NOT `percent` — a `percent` read never matched a real
// event, so the bar could never fill; that read is fixed here together
// with the lifecycle below.
//
// Lifecycle: the bar must render exactly while a download is genuinely
// in flight (active transfer OR waiting in the pending-download queue)
// and clear when all downloads finish. The backend ships NO dedicated
// download-complete event — the whole lifecycle travels on
// `download_progress` — so terminal events are recognized by wire
// contract:
//   - `progress === 100` — the ONLY value any success path pushes (all
//     intermediate events cap at 95: the poll loop, the segmented
//     fast-lane callback, and the pause/resume transition percentages);
//   - terminal `status` markers — see TERMINAL_STATUS_MARKERS below
//     (the full terminal catalog of the server download paths; these
//     status strings are already displayed verbatim by the Models
//     page's DownloadProgressBar, so this consumes the existing
//     contract rather than adding one).
// Queued models (events carrying a 1-based `queue_position`) keep the
// bar up while the queue drains, and a queued download cancelled from
// the Models page pushes NO event at all — the terminal-armed drain
// timer (TERMINAL_DRAIN_MS) is the safety net that clears those ghost
// entries once the stream goes quiet.
//
// The percentage also resets to null whenever the recording state
// leaves "loading" (the state the bar was originally built for) so a
// stale bar can never linger across a model-load state change; the
// next progress event (~1 Hz while a transfer runs) re-seeds it.

import { useCallback, useEffect, useRef, useState } from "react";
import { usePythonEvent } from "@/hooks/usePython";
import type { RecordingState } from "@/types/ipc";

/**
 * Terminal `download_progress` status markers (matched
 * case-insensitively as substrings). Every terminal push from
 * voice_typer/server/service/model/_downloads.py +
 * _download_helpers.py carries one of these; no intermediate status
 * does. Unmatched future terminal strings degrade to the drain timer /
 * recording-state reset — never worse than the pre-fix behavior.
 */
const TERMINAL_STATUS_MARKERS = [
	"complete", // "Download of {model} complete" / "Parakeet download complete"
	"already cached", // "{model} already cached" / "Qwen model already cached"
	"cancelled", // "Download cancelled"
	"failed", // "Download failed: {reason}" / retry-exhausted / integrity messages
	"not enough disk space", // parakeet disk_space_insufficient reason message
	"huggingface_hub is not installed", // missing-dependency failure message
] as const;

/**
 * Grace period a terminal event waits for a follow-up
 * `download_progress` push before the bar is cleared. The queue drain
 * (`download_model`'s `finally` → `_start_next_queued_download`) spawns
 * the next download's thread immediately and its first push ("Starting
 * download…") follows within milliseconds, so a genuine queue chain
 * cancels the timer; only a quiet stream (all done, or a queued model
 * silently cancelled from the Models page — no event exists for that)
 * lets it fire.
 */
export const TERMINAL_DRAIN_MS = 3000;

function isTerminalProgressEvent(data: Record<string, unknown>): boolean {
	// Success terminals are the ONLY 100% pushes on the wire —
	// intermediate events cap at 95 (see the module docstring).
	if (data.progress === 100) return true;
	const status = data.status;
	if (typeof status !== "string") return false;
	const lowered = status.toLowerCase();
	for (const marker of TERMINAL_STATUS_MARKERS) {
		if (lowered.includes(marker)) return true;
	}
	return false;
}

/**
 * Subscribe to `download_progress` and return the current percentage
 * (0-100), or `null` when no download bar should render. Call once at
 * the top level of Home.
 *
 * @param recordingState the store's recording state — the bar resets
 *   whenever it leaves "loading".
 */
export function useDownloadProgressEvent(
	recordingState: RecordingState,
): number | null {
	const [downloadPct, setDownloadPct] = useState<number | null>(null);
	// Models with an in-flight download (queued or transferring). The
	// bar renders while this set is non-empty; a plain ref (not state)
	// because the RENDERED signal is `downloadPct` alone.
	const activeModelsRef = useRef<Set<string>>(new Set());
	// Pending drain timer id armed by a terminal event (null = none).
	const drainTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

	// Both helpers are identity-stable: they touch only refs and the
	// setState dispatcher, so the effects below keep their documented
	// dep-driven re-run semantics.
	const cancelDrain = useCallback(() => {
		if (drainTimerRef.current !== null) {
			clearTimeout(drainTimerRef.current);
			drainTimerRef.current = null;
		}
	}, []);

	// Drop all activity and hide the bar. Shared by the terminal drain,
	// the recording-state reset, and the unmount cleanup.
	const clearAll = useCallback(() => {
		cancelDrain();
		activeModelsRef.current.clear();
		setDownloadPct(null);
	}, [cancelDrain]);

	usePythonEvent("download_progress", (data): (() => void) | undefined => {
		if (!data) return undefined;
		const model = data.model;
		if (typeof model !== "string" || model === "") return undefined;
		// Any live event cancels a pending drain — the stream is not
		// quiet after all (a queued download started, or a new download
		// began within the grace window).
		cancelDrain();

		const queuePosition = data.queue_position;
		if (typeof queuePosition === "number" && queuePosition > 0) {
			// Queued behind the active transfer — keep the bar up, but do
			// NOT take the percentage: queued events carry progress 0 and
			// would slam the live transfer's readout to zero.
			activeModelsRef.current.add(model);
			return undefined;
		}

		if (isTerminalProgressEvent(data)) {
			activeModelsRef.current.delete(model);
			if (activeModelsRef.current.size === 0) {
				// Last download finished — hide the bar now.
				setDownloadPct(null);
			}
			// Arm the drain window on EVERY terminal event: a genuine
			// queue chain pushes the next download's first event within
			// milliseconds (cancelling this timer), while a quiet window
			// clears ghost entries the stream never announces — a queued
			// model cancelled from the Models page pushes no event for
			// itself, so set-tracking alone would leave the bar up.
			drainTimerRef.current = setTimeout(() => {
				drainTimerRef.current = null;
				clearAll();
			}, TERMINAL_DRAIN_MS);
			return undefined;
		}

		// Active transfer (or a start/pause/resume transition event).
		// Percentages outside 0-100 are ignored (defensive against
		// malformed payloads) — transition-only events without a valid
		// percentage just mark the model active.
		activeModelsRef.current.add(model);
		const pct = data.progress;
		if (typeof pct === "number" && pct >= 0 && pct <= 100) {
			setDownloadPct(pct);
		}
		return undefined;
	});

	useEffect(() => {
		if (recordingState !== "loading") clearAll();
	}, [recordingState, clearAll]);

	// Unmount cleanup: a pending drain timer must not fire into an
	// unmounted component (and the activity set must not leak models).
	// `clearAll` is identity-stable, so this cleanup runs only on
	// unmount.
	useEffect(() => clearAll, [clearAll]);

	return downloadPct;
}
