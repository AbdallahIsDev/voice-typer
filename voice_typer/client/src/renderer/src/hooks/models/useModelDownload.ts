/**
 * useModelDownload — download-progress slice of the Models page.
 *
 * Extracted from the former
 * `useModelLifecycle.ts` (995-line) monolith. This sub-hook owns the
 * download progress state machine and the three actions that drive it:
 *   • `downloadModel` — kicks off a model download + surfaces failures
 *     via a sonner toast with a "Retry" action button —
 *     `showSnack` has no action-button affordance so we bypass it for
 *     the retry-toast path. Failures are ALSO recorded in
 *     `failedDownload` so the inline `<DownloadProgressBar>` can show
 *     an in-place error UI + Retry button —
 *     previously the bar vanished on failure and the only recovery
 *     path was the 8-second ephemeral toast.
 *   • `retryDownload` — clears `failedDownload` and re-invokes
 *     `downloadModel`. Wired to the `<DownloadProgressBar>` Retry
 *     button so users can recover a failed download in place.
 *   • `handleTogglePause` / `handleCancelDownload` — pause/resume/cancel
 *     the in-flight download. Cancel ALSO clears `failedDownload` so
 *     the bar unmounts cleanly. With a model name (the queued-model
 *     Cancel affordance), the cancel targets THAT model's pending queue
 *     entry instead — the active transfer's state is left intact.
 *   • `resetProgress` — internal helper used by `downloadModel` and
 *     `handleCancelDownload` to clear local progress state.
 *   • The `download_progress` event subscription — pushes from the
 *     backend update `downloadProgress` / `downloadStatus` / byte
 *     counters / `speedBps` / `etaSeconds` / `isPaused`.
 *
 * The hook receives `setModels` (from `useModelConfig`) so `downloadModel`
 * can mark the just-downloaded model as `downloaded: true` in the local
 * model list, and `reconcileAfterDownload` (loadConfig) for the
 * post-download full reconcile.
 *
 * ── single-state consolidation ──────────────────────────────────
 *
 * Previously this hook used 9 separate `useState` calls
 * (`downloadingModel`, `downloadProgress`, `downloadStatus`, `isPaused`,
 * `downloadedBytes`, `totalBytes`, `speedBps`, `etaSeconds`,
 * `failedDownload`). Every `download_progress`
 * event invoked up to 8 of these setters (one per field in the event
 * payload). Although React 18 batches these into a single re-render, the
 * per-setter overhead (8 distinct state-entry lookups + 8 distinct
 * Object.is equality checks + 8 distinct subscriber notifications
 * internally) was wasteful on the high-frequency progress event path
 * (multiple events per second during a model download).
 *
 * The 10 fields are now consolidated into a single `useState<DownloadState>`
 * updated via functional `setState(prev => ({ ...prev, ...patch }))`. Each
 * `download_progress` event produces ONE setState call with a patch
 * object containing only the fields present in the event payload. The
 * return shape is preserved via destructuring at the return boundary so
 * consumer identity stays stable (no `state.downloadingModel` access
 * pattern leaks into consumers).
 */
import { useCallback, useRef, useState } from "react";
import { type PythonCall, usePythonEvent } from "@/hooks/usePython";
import type { ShowSnackOptions } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import { userFacingErrorMessage } from "@/lib/errors/userFacingErrorMessage";
import { formatErrorMessage, type ModelInfo } from "@/lib/utils/models";

// ── Types ─────────────────────────────────────────────────────────────

export interface FailedDownload {
	modelName: string;
	error: string;
}

interface UseModelDownloadArgs {
	call: PythonCall;
	showSnack: (
		message: string,
		kind: "success" | "error" | "warning" | "info",
		options?: ShowSnackOptions,
	) => void;
	setModels: React.Dispatch<React.SetStateAction<ModelInfo[]>>;
	/** Full reconcile after a successful download: re-fetches config +
	 * status so the Active badge reflects BACKEND truth (the backend
	 * does not auto-activate a downloaded model, so the renderer must
	 * not invent an Active state locally). */
	reconcileAfterDownload: () => Promise<void>;
}

export interface UseModelDownloadResult {
	downloadingModel: string | null;
	downloadProgress: number;
	downloadStatus: string;
	isPaused: boolean;
	downloadedBytes: number | null;
	totalBytes: number | null;
	speedBps: number | null;
	etaSeconds: number | null;
	/** When set, the in-flight download has failed. The
	 * `<DownloadProgressBar>` consumes this to render the inline error
	 * state + Retry button. The bar stays
	 * mounted because `downloadingModel` is NOT cleared on failure. */
	failedDownload: FailedDownload | null;
	downloadModel: (model: ModelInfo) => Promise<void>;
	retryDownload: (model: ModelInfo) => Promise<void>;
	handleTogglePause: () => Promise<void>;
	/** Cancel the ACTIVE download (no argument — legacy shape, wired
	 * to the progress bar's Cancel button), or cancel/remove a named
	 * model's pending download (queued-model Cancel affordance: a
	 * queued entry is removed without touching the active transfer). */
	handleCancelDownload: (modelName?: string) => Promise<void>;
}

// ── Consolidated download state ───────────────────────────────────────
//
// All 9 previously-separate useState fields live in ONE state object.
// Updates go through functional `setState(prev => ({ ...prev, ...patch }))`
// so each `download_progress` event produces exactly ONE setState call
// (down from up to 8). React 18 already batched the per-field setStates
// into a single re-render, but the consolidation still:
//   - eliminates 7 redundant state-entry lookups per event
//   - eliminates 7 redundant Object.is equality checks per event
//   - produces a single subscriber notification per event (down from 8)
//   - makes the atomicity guarantee explicit (all fields update together)
//
// `Partial<DownloadState>` is the patch shape used by event handlers —
// only fields present in the event payload are set, others are preserved
// via the `{ ...prev, ...patch }` spread.
interface DownloadState {
	downloadingModel: string | null;
	downloadProgress: number;
	downloadStatus: string;
	isPaused: boolean;
	downloadedBytes: number | null;
	totalBytes: number | null;
	speedBps: number | null;
	etaSeconds: number | null;
	failedDownload: FailedDownload | null;
}

const INITIAL_DOWNLOAD_STATE: DownloadState = {
	downloadingModel: null,
	downloadProgress: 0,
	downloadStatus: "",
	isPaused: false,
	downloadedBytes: null,
	totalBytes: null,
	speedBps: null,
	etaSeconds: null,
	failedDownload: null,
};

/** Zero the progress-related fields (preserving `downloadingModel`,
 * `failedDownload`). Pure so the claim-time
 * updater inside `downloadModel` can reuse it — an updater must not
 * call `setState` (which the `resetProgress` callback does). This is
 * the SAME field set `resetProgress` clears, kept in one place. */
function withResetProgress(prev: DownloadState): DownloadState {
	return {
		...prev,
		downloadProgress: 0,
		downloadStatus: "",
		downloadedBytes: null,
		totalBytes: null,
		speedBps: null,
		etaSeconds: null,
		isPaused: false,
	};
}

// ── Hook ──────────────────────────────────────────────────────────────

export function useModelDownload({
	call,
	showSnack,
	setModels,
	reconcileAfterDownload,
}: UseModelDownloadArgs): UseModelDownloadResult {
	// Consolidated download-progress state — previously 9 separate
	// useState calls. Each `download_progress` event now produces ONE
	// setState via the functional-update form below.
	const [state, setState] = useState<DownloadState>(INITIAL_DOWNLOAD_STATE);

	// Stale-resolution guard: `download_model` is a long-running promise
	// that keeps resolving AFTER the user cancels (or after a failed
	// attempt when the user immediately retries / starts another
	// download). Without a generation token, the stale resolution's
	// state writes (resetProgress / failedDownload / bar clear) would
	// corrupt the NEW download's state — zeroing its progress bar,
	// unmounting it, or showing a stale error toast mid-download.
	// `downloadModel` captures the generation at start; every state
	// write after an await is gated on still being the current run.
	// Cancel bumps the generation so the cancelled promise's eventual
	// resolution is always treated as stale.
	const downloadRunRef = useRef(0);
	// The run that currently OWNS the single progress-bar slot (the
	// last run that CLAIMED `downloadingModel` — see the claim guard
	// in `downloadModel`). The stale-resolution guard honours BOTH the
	// newest click AND the slot owner: a download request that arrives
	// while another model transfers resolves as QUEUED — it bumps the
	// click generation but never claims the slot, so the ACTIVE
	// transfer's still-pending promise must stay current (its eventual
	// success/failure resolution processes normally instead of being
	// silently dropped as "superseded"). Nulled whenever the slot is
	// released (success / cancel / queued-release).
	const barOwnerRunRef = useRef<number | null>(null);

	// ── download_progress event subscription ────────────────────────
	//
	// Build a patch object from the event payload, then issue ONE
	// setState with `{ ...prev, ...patch }`. Previously this handler
	// called up to 8 separate setters (`setDownloadProgress`,
	// `setDownloadStatus`, `setDownloadedBytes`, `setTotalBytes`,
	// `setSpeedBps`, `setEtaSeconds`, `setIsPaused`), each updating
	// an independent useState. React 18 batched them into one
	// re-render, but the per-setter overhead (state-entry lookup +
	// Object.is check + subscriber notification) ran 8 times per
	// event. The consolidated form runs the lookup + check once.
	usePythonEvent(
		"download_progress",
		useCallback(
			(data: Record<string, unknown> | undefined): (() => void) | undefined => {
				if (!data) return undefined;
				const patch: Partial<DownloadState> = {};
				if (typeof data.progress === "number")
					patch.downloadProgress = data.progress;
				if (typeof data.status === "string") patch.downloadStatus = data.status;
				if (typeof data.downloaded_bytes === "number")
					patch.downloadedBytes = data.downloaded_bytes;
				if (typeof data.total_bytes === "number")
					patch.totalBytes = data.total_bytes;
				// Speed/ETA: set when present; cleared ONLY on a state
				// transition (pause/resume/status change) — the backend
				// also pushes transition-only events (e.g. a lone
				// `paused: true`) whose absent speed/ETA fields mean
				// "not re-measured", not "reset to zero". Clearing on
				// absence made every partial event wipe the live
				// speed/ETA readout, contradicting the patch contract
				// above (only fields present in the event are set).
				const isTransition =
					typeof data.status === "string" ||
					typeof data.paused === "boolean" ||
					data.resumed === true;
				if (typeof data.speed_bytes_per_sec === "number") {
					patch.speedBps = data.speed_bytes_per_sec;
				} else if (data.speed_bytes_per_sec == null && isTransition) {
					patch.speedBps = null;
				}
				if (typeof data.eta_seconds === "number") {
					patch.etaSeconds = data.eta_seconds;
				} else if (data.eta_seconds == null && isTransition) {
					patch.etaSeconds = null;
				}
				if (typeof data.paused === "boolean") patch.isPaused = data.paused;
				if (typeof data.resumed === "boolean" && data.resumed)
					patch.isPaused = false;
				// Only fire setState if the patch actually contains
				// updates — avoids a no-op state transition.
				if (Object.keys(patch).length > 0) {
					// Bail out if no field actually changed value.
					// The original per-`useState` pattern relied on
					// React's `Object.is` bailout (e.g.
					// `setSpeedBps(null)` was a no-op when speedBps
					// was already null). The consolidated form
					// creates a new state object on every call, which
					// would defeat that bailout — so we explicitly
					// compare each patched field against `prev` and
					// return `prev` (same reference) when nothing
					// changed. React's `Object.is` check then skips
					// the re-render, matching the original behaviour.
					setState((prev) => {
						let changed = false;
						for (const key of Object.keys(patch) as (keyof DownloadState)[]) {
							if (!Object.is(prev[key], (patch as DownloadState)[key])) {
								changed = true;
								break;
							}
						}
						return changed ? { ...prev, ...patch } : prev;
					});
				}
				return undefined;
			},
			[],
		),
	);

	const resetProgress = useCallback(() => {
		// Reset only the progress-related fields — preserve
		// `downloadingModel` and `failedDownload` (these are managed by
		// the action callbacks below and would be clobbered if we spread
		// `INITIAL_DOWNLOAD_STATE` here).
		setState(withResetProgress);
	}, []);

	//Action: downloadModel (retry on failure) ────────────
	//
	// On failure: keep `downloadingModel` set so the
	// `<DownloadProgressBar>` stays mounted, and record the failure in
	// `failedDownload` so the bar can render the inline error state +
	//Retry button. The toast with the Retry
	//action button is preserved as a secondary affordance.
	// On success: clear `downloadingModel` (unmount the bar) and
	// `failedDownload` (clear any stale failure for a re-download).
	const downloadModel = useCallback(
		async (model: ModelInfo) => {
			// Claim the download generation — any earlier in-flight
			// `download_model` promise now resolves stale (see the guard
			// below) and must not touch state.
			const runId = ++downloadRunRef.current;
			// True while THIS run is still the current download OR the
			// owner of the progress-bar slot. Checked after every await
			// so a cancelled / superseded promise can no longer clobber
			// the live download's state (progress bar, error UI,
			// toasts). The slot-owner arm keeps the ACTIVE transfer's
			// promise current even after a QUEUED request bumps the
			// generation (queued runs never claim the slot — see the
			// claim guard below).
			const isCurrent = () =>
				downloadRunRef.current === runId || barOwnerRunRef.current === runId;

			// Claim the SINGLE progress-bar slot. The claim is
			// conditional (a functional update reads the live slot
			// state — the closure's `state` is stale by design):
			//   • Another model is ACTIVELY transferring (slot
			//     occupied, no recorded failure) → do NOT claim and do
			//     NOT zero the live bar's progress. The backend QUEUES
			//     this request; the live transfer keeps the bar and
			//     its progress events keep updating it.
			//   • This model is already the transferring owner →
			//     duplicate click (the backend answers "already
			//     active") — leave the live bar untouched.
			//   • Slot free (or its owner's transfer FAILED — the
			//     failure branch keeps the slot mounted for the inline
			//     error UI) → claim it and zero the progress fields
			//     for the new attempt.
			// The queued resolution below then never has to "give the
			// bar back" — it was never taken.
			setState((prev) => {
				if (
					prev.downloadingModel != null &&
					prev.downloadingModel !== model.name &&
					prev.failedDownload == null
				) {
					// Queued-bound click — the live transfer keeps
					// the bar AND its progress state untouched.
					return prev;
				}
				if (
					prev.downloadingModel === model.name &&
					prev.failedDownload == null
				) {
					// Duplicate click on the transferring model.
					return prev;
				}
				// Fresh claim (free slot, or re-claim after a
				// failure for this model).
				barOwnerRunRef.current = runId;
				return {
					...withResetProgress(prev),
					downloadingModel: model.name,
					failedDownload: null,
				};
			});
			try {
				const result = await call<{
					success: boolean;
					error?: string;
					message?: string;
					cancelled?: boolean;
					/** Set when the request was accepted into the pending
					 * download queue instead of starting immediately (a
					 * gateable transfer is already in flight). Not a
					 * failure — the request auto-starts when the active
					 * transfer exits. */
					queued?: boolean;
					/** Set when the backend refused to start because the
					 * model is ALREADY the download in flight (a re-click
					 * of the active model). Not a failure — the live
					 * download owns the bar; keep it. */
					download_already_active?: boolean;
				}>("download_model", { model: model.name });
				if (!isCurrent()) {
					// A cancel or a newer download superseded this run —
					// the state now belongs to the current run, so this
					// stale resolution must be a no-op.
					return;
				}
				if (result.queued) {
					// QUEUED — the backend accepted this request into the
					// pending FIFO queue; it auto-starts when the active
					// transfer exits. NOT a success: the model is not on
					// disk, so there is no `downloaded: true` marking and
					// no reconcile. The active download's state stays
					// intact (the claim guard above never took the bar);
					// the queued model's position renders from the
					// queue-position events via the queue UI.
					showSnack(
						result.message ||
							t("models.snack.downloadQueued", { name: model.name }),
						"info",
					);
					// Release the slot ONLY if this run transiently
					// claimed it (renderer reload mid-transfer: the slot
					// was free at click time even though the backend gate
					// was armed). A queued request must not keep the bar.
					if (barOwnerRunRef.current === runId) {
						barOwnerRunRef.current = null;
						setState((prev) =>
							prev.downloadingModel === model.name
								? { ...prev, downloadingModel: null }
								: prev,
						);
					}
					return;
				}
				if (result.download_already_active) {
					// The requested model IS the download already in
					// flight (a re-click of the active model). This
					// attempt never started a second transfer — surface
					// the state and LEAVE the live bar + progress
					// untouched (they belong to this very model).
					showSnack(
						result.error ||
							t("models.snack.downloadAlreadyActiveName", {
								name: model.name,
							}),
						"warning",
					);
					return;
				}
				if (result.success) {
					setModels((prev) =>
						prev.map((m) =>
							m.name === model.name
								? // downloaded: true only — the ACTIVE badge is
									// NOT set here: the backend does not
									// auto-activate a downloaded model, so an
									// optimistic isActive here showed a phantom
									// "Active" badge while dictation still used
									// the previous model. `reconcileAfterDownload`
									// (below) re-applies the backend's truth.
									{ ...m, downloaded: true, isActive: false }
								: m,
						),
					);
					showSnack(
						result.message ||
							t("models.snack.downloaded", { name: model.name }),
						"success",
					);
					// Success → unmount the bar + clear any stale
					// failure (and release the slot ownership).
					barOwnerRunRef.current = null;
					setState((prev) => ({
						...prev,
						downloadingModel: null,
						failedDownload: null,
					}));
					// Reconcile with backend truth: re-fetches get_config +
					// get_model_status so `downloaded` / `isActive` match
					// what the backend will actually use (MDL-9 contract).
					await reconcileAfterDownload();
				} else if (result.cancelled) {
					// User-initiated cancel: the cancel path
					// (handleCancelDownload) already surfaced the
					// "cancelled" snackbar and cleared state. The
					// pending download_model resolves after the
					// cancel IPC completes — treat the cancelled
					// resolution as a clean stop (unmount the bar,
					// no failure toast).
					barOwnerRunRef.current = null;
					setState((prev) => ({
						...prev,
						downloadingModel: null,
						failedDownload: null,
					}));
					resetProgress();
				} else {
					// Failure → keep the bar mounted, record the failure so
					// the inline error UI + Retry button render.
					const message =
						result.error ||
						t("models.snack.downloadFailedName", { name: model.name });
					setState((prev) => ({
						...prev,
						failedDownload: { modelName: model.name, error: message },
					}));
					//surface the failure with a Retry action button.
					// `showSnack` supports the action option, so the failure
					// toast now flows through the canonical snackbar system
					// (duration comes from the error-type default).
					showSnack(message, "error", {
						action: {
							label: t("microphone.retry"),
							onClick: () => {
								void downloadModel(model);
							},
						},
					});
				}
			} catch (err) {
				if (!isCurrent()) return;
				const message = t("models.snack.downloadFailed", {
					// Known codes map to curated localized copy; anything
					// else keeps the real formatted reason (never a
					// generic placeholder that hides what happened).
					error: userFacingErrorMessage(err, t, formatErrorMessage(err)),
				});
				setState((prev) => ({
					...prev,
					failedDownload: { modelName: model.name, error: message },
				}));
				//same retry affordance on thrown errors.
				showSnack(message, "error", {
					action: {
						label: t("microphone.retry"),
						onClick: () => {
							void downloadModel(model);
						},
					},
				});
			}
			// NOTE: no `finally { setState(prev => ({ ...prev, downloadingModel: null })) }`
			// here — the failure branch must keep `downloadingModel` set
			// so the bar stays mounted. The success branch clears it
			// explicitly.
		},
		[call, resetProgress, showSnack, setModels, reconcileAfterDownload],
	);

	//Action: retryDownload ───────────────────
	//
	// Wired to the `<DownloadProgressBar>` Retry button. Clears the
	// failure state and re-invokes `downloadModel`. `downloadModel`
	// itself also clears `failedDownload` at the start, but we clear
	// it here too so the bar's UI flips back to the progress state
	// immediately (before the next IPC round-trip resolves).
	const retryDownload = useCallback(
		async (model: ModelInfo) => {
			setState((prev) => ({ ...prev, failedDownload: null }));
			await downloadModel(model);
		},
		[downloadModel],
	);

	// ── Action: handleTogglePause / handleCancelDownload ────────────
	//
	// `state.isPaused` is in the dep array so the closure captures the
	// fresh value (mirrors the original code's `[call, isPaused, showSnack]`
	// deps). The IPC call (`pause_model_download` vs.
	// `resume_model_download`) is chosen based on the closure value.
	const handleTogglePause = useCallback(async () => {
		// Capture the pre-toggle value so the failure revert restores THIS
		// attempt's starting state instead of blindly re-flipping: a
		// `download_progress` event that legitimately set `isPaused` (or
		// cleared it) during the failed IPC's await would otherwise be
		// inverted by the catch-path re-flip.
		const wasPaused = state.isPaused;
		setState((prev) => ({ ...prev, isPaused: !prev.isPaused }));
		try {
			if (wasPaused) {
				await call("resume_model_download");
			} else {
				await call("pause_model_download");
			}
		} catch (err) {
			setState((prev) => ({ ...prev, isPaused: wasPaused }));
			const reason = userFacingErrorMessage(err, t, formatErrorMessage(err));
			showSnack(
				state.isPaused
					? t("models.snack.resumeFailed", { error: reason })
					: t("models.snack.pauseFailed", { error: reason }),
				"error",
			);
		}
	}, [call, state.isPaused, showSnack]);

	const handleCancelDownload = useCallback(
		async (modelName?: string) => {
			// With a model name (queued-model Cancel affordance): cancel
			// THAT model's pending download. When the model sits in the
			// queue it is removed WITHOUT touching the active transfer —
			// the active bar, progress state, and failure state stay
			// intact. (Race safety: if the named model IS the active
			// transfer — it auto-started between render and click — the
			// service cancels the transfer and the legacy active-cancel
			// semantics below run.)
			// Without a model name (progress-bar Cancel): the legacy
			// shape — cancel the ACTIVE transfer.
			let cancelledActiveTransfer = false;
			try {
				if (modelName) {
					const result = await call<{
						cancelled?: boolean;
						removed_from_queue?: boolean;
					}>("cancel_model_download", { model: modelName });
					if (result?.removed_from_queue) {
						// Queue removal — the active transfer keeps running.
						showSnack(
							t("models.snack.queuedCancelled", { name: modelName }),
							"info",
						);
					} else if (result?.cancelled) {
						// The named model WAS the active transfer (race) —
						// fall through to the legacy active-cancel handling.
						cancelledActiveTransfer = true;
						showSnack(t("models.snack.cancelled"), "warning");
					} else {
						// Neither queued nor active (stale queued chip /
						// double click) — benign no-op, say so honestly.
						showSnack(
							t("models.snack.cancelNoopName", { name: modelName }),
							"info",
						);
					}
				} else {
					await call("cancel_model_download");
					cancelledActiveTransfer = true;
					showSnack(t("models.snack.cancelled"), "warning");
				}
			} catch (err) {
				cancelledActiveTransfer = !modelName;
				showSnack(
					t("models.snack.cancelFailed", {
						error: userFacingErrorMessage(err, t, formatErrorMessage(err)),
					}),
					"error",
				);
			} finally {
				if (cancelledActiveTransfer) {
					// Always clear local download state when the ACTIVE
					// transfer was cancelled — whether the IPC succeeded
					// or failed, the user has signalled intent to cancel
					// it. The bar unmounts (`downloadingModel = null`),
					// the inline error UI is cleared (`failedDownload =
					// null`), and progress counters reset. The backend may
					// still be downloading, but the renderer's view
					// reflects the user's intent and the next
					// download_progress event (if any) will re-establish
					// state.
					//
					// Bump the generation so the still-pending
					// `download_model` promise's eventual resolution
					// (cancelled / failed / even success) is treated as
					// stale and cannot clobber a download the user starts
					// right after cancelling.
					downloadRunRef.current += 1;
					barOwnerRunRef.current = null;
					setState((prev) => ({
						...prev,
						downloadingModel: null,
						failedDownload: null,
					}));
					resetProgress();
				}
				// A queued-model cancellation (queue removal or no-op)
				// intentionally leaves the active download's state alone.
			}
		},
		[call, showSnack, resetProgress],
	);

	// Destructure at the return boundary so consumer identity stays
	// stable — consumers continue to receive `downloadingModel` /
	// `downloadProgress` / etc. as top-level fields (no `state.X`
	// access pattern leaks into the call sites).
	const {
		downloadingModel,
		downloadProgress,
		downloadStatus,
		isPaused,
		downloadedBytes,
		totalBytes,
		speedBps,
		etaSeconds,
		failedDownload,
	} = state;

	return {
		downloadingModel,
		downloadProgress,
		downloadStatus,
		isPaused,
		downloadedBytes,
		totalBytes,
		speedBps,
		etaSeconds,
		failedDownload,
		downloadModel,
		retryDownload,
		handleTogglePause,
		handleCancelDownload,
	};
}
