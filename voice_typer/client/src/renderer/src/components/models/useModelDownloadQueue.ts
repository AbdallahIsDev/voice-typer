/**
 * useModelDownloadQueue — renderer-side queue state for model downloads.
 *
 * Derives the pending-download queue DIRECTLY from the backend's
 * `download_progress` events: an event carrying `queue_position` marks
 * its model as waiting in the FIFO queue (backend single source of
 * truth — the queue survives renderer navigation/reload and is shared
 * by every trigger, not just this page), and an event WITHOUT the field
 * means "not queued" (active transfer or terminal state), which clears
 * the model's entry.
 *
 * Why co-located with `components/models/` (not `hooks/models/`): this
 * is a self-contained slice of the queue UI — it adds NO new state to
 * the page-level download state machine (`hooks/models/useModelDownload`
 * is untouched) and is consumed only by the models components below.
 *
 * Hydration: on mount the hook snapshots the backend queue once via the
 * read-only `get_download_queue` command (backend-owned queue survives
 * navigation, so a remount mid-queue restores chips immediately); live
 * transitions keep flowing through `download_progress` events. A failed
 * or malformed snapshot is a silent no-op — the event path still works.
 */
import { useCallback, useEffect, useState } from "react";
import { usePython, usePythonEvent } from "@/hooks/usePython";

/** Map of model name → 1-based FIFO position in the download queue. */
export type ModelDownloadQueuePositions = Record<string, number>;

export function useModelDownloadQueue(): ModelDownloadQueuePositions {
	const [queuedPositions, setQueuedPositions] =
		useState<ModelDownloadQueuePositions>({});
	const { call } = usePython();

	// One-shot mount snapshot: restore chips immediately when remounting
	// mid-queue instead of waiting for the next queue transition event.
	useEffect(() => {
		let cancelled = false;
		call<{ queue?: unknown }>("get_download_queue")
			.then((res) => {
				if (cancelled) return;
				const queue = Array.isArray(res?.queue)
					? res.queue.filter(
							(model): model is string => typeof model === "string",
						)
					: [];
				if (queue.length === 0) return;
				setQueuedPositions((prev) => {
					const next = { ...prev };
					let changed = false;
					for (const [index, model] of queue.entries()) {
						if (next[model] !== index + 1) {
							next[model] = index + 1;
							changed = true;
						}
					}
					return changed ? next : prev;
				});
			})
			.catch(() => {
				// Snapshot is best-effort (bridge down, backend busy) —
				// the event subscription below still tracks live changes.
			});
		return () => {
			cancelled = true;
		};
	}, [call]);

	usePythonEvent(
		"download_progress",
		useCallback((data?: Record<string, unknown>) => {
			if (!data) return undefined;
			const model = typeof data.model === "string" ? data.model : null;
			if (!model) return undefined;
			const position =
				typeof data.queue_position === "number" ? data.queue_position : null;
			setQueuedPositions((prev) => {
				if (position !== null && position > 0) {
					if (prev[model] === position) return prev;
					return { ...prev, [model]: position };
				}
				// An event WITHOUT a (valid) queue_position means "not
				// queued" — active transfer or terminal state. Clear the
				// entry so the queued affordance unmounts.
				if (!(model in prev)) return prev;
				const next = { ...prev };
				delete next[model];
				return next;
			});
			return undefined;
		}, []),
	);

	return queuedPositions;
}
