/**
 * useModelDownload — download-progress slice of the Models page.
 *
 *  (Phase 4.5 spaghetti split): extracted from the former
 * `useModelLifecycle.ts` (995-line) monolith. This sub-hook owns the
 * download progress state machine and the three actions that drive it:
 *   • `downloadModel` — kicks off a model download + surfaces failures
 *     via a sonner toast with a "Retry" action button ( —
 *     `showSnack` has no action-button affordance so we bypass it for
 *     the retry-toast path). Failures are ALSO recorded in
 *     `failedDownload` so the inline `<DownloadProgressBar>` can show
 *     an in-place error UI + Retry button ( priority #3) —
 *     previously the bar vanished on failure and the only recovery
 *     path was the 8-second ephemeral toast.
 *   • `retryDownload` — clears `failedDownload` and re-invokes
 *     `downloadModel`. Wired to the `<DownloadProgressBar>` Retry
 *     button so users can recover a failed download in place.
 *   • `installDeps` — fires the optional `install_parakeet_deps` IPC
 *     and falls back to the manual-install hint when the IPC is
 *     unavailable ( fix #7). Tracks `installingDepsModel` so
 *     the `<ModelCardActions>` Download Deps button can show
 *     `aria-busy` + a "Downloading…" label swap ().
 *   • `handleTogglePause` / `handleCancelDownload` — pause/resume/cancel
 *     the in-flight download. Cancel ALSO clears `failedDownload` so
 *     the bar unmounts cleanly.
 *   • `resetProgress` — internal helper used by `downloadModel` and
 *     `handleCancelDownload` to clear local progress state.
 *   • The `download_progress` event subscription — pushes from the
 *     backend update `downloadProgress` / `downloadStatus` / byte
 *     counters / `speedBps` / `etaSeconds` / `isPaused`.
 *
 * The hook receives `setModels` (from `useModelConfig`) so `downloadModel`
 * can mark the just-downloaded model as `downloaded: true` in the local
 * model list, and `refreshModelStatus` so `installDeps` can reconcile
 * the deps-installed state.
 *
 * ── single-state consolidation ──────────────────────────────────
 *
 * Previously this hook used 10 separate `useState` calls
 * (`downloadingModel`, `downloadProgress`, `downloadStatus`, `isPaused`,
 * `downloadedBytes`, `totalBytes`, `speedBps`, `etaSeconds`,
 * `failedDownload`, `installingDepsModel`). Every `download_progress`
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
import { useCallback, useState } from "react";
import { usePythonEvent } from "@/hooks/usePython";
import type { ShowSnackOptions } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import { userFacingErrorMessage } from "@/lib/errors/userFacingErrorMessage";
import { formatErrorMessage, type ModelInfo } from "@/lib/utils/models";

// ── Types ─────────────────────────────────────────────────────────────

type CallFn = <T>(cmd: string, data?: Record<string, unknown>) => Promise<T>;

export interface FailedDownload {
	modelName: string;
	error: string;
}

interface UseModelDownloadArgs {
	call: CallFn;
	showSnack: (
		message: string,
		kind: "success" | "error" | "warning" | "info",
		options?: ShowSnackOptions,
	) => void;
	setModels: React.Dispatch<React.SetStateAction<ModelInfo[]>>;
	refreshModelStatus: () => Promise<void>;
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
	 * state + Retry button ( / priority #3). The bar stays
	 * mounted because `downloadingModel` is NOT cleared on failure. */
	failedDownload: FailedDownload | null;
	/** Name of the model currently installing dependencies (drives the
	 * `isInstallingDepsThis` prop on `<ModelCardActions>` so the
	 * Download Deps button can show `aria-busy` + a "Downloading…"
	 * label swap — ). */
	installingDepsModel: string | null;
	downloadModel: (model: ModelInfo) => Promise<void>;
	retryDownload: (model: ModelInfo) => Promise<void>;
	installDeps: (model: ModelInfo) => Promise<void>;
	handleTogglePause: () => Promise<void>;
	handleCancelDownload: () => Promise<void>;
}

// ── Consolidated download state ───────────────────────────────────────
//
// All 10 previously-separate useState fields live in ONE state object.
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
	installingDepsModel: string | null;
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
	installingDepsModel: null,
};

// ── Hook ──────────────────────────────────────────────────────────────

export function useModelDownload({
	call,
	showSnack,
	setModels,
	refreshModelStatus,
}: UseModelDownloadArgs): UseModelDownloadResult {
	// Consolidated download-progress state — previously 10 separate
	// useState calls. Each `download_progress` event now produces ONE
	// setState via the functional-update form below.
	const [state, setState] = useState<DownloadState>(INITIAL_DOWNLOAD_STATE);

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
				if (typeof data.speed_bytes_per_sec === "number") {
					patch.speedBps = data.speed_bytes_per_sec;
				} else if (data.speed_bytes_per_sec == null) {
					patch.speedBps = null;
				}
				if (typeof data.eta_seconds === "number") {
					patch.etaSeconds = data.eta_seconds;
				} else if (data.eta_seconds == null) {
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
		// `downloadingModel`, `failedDownload`, and
		// `installingDepsModel` (these are managed by the action
		// callbacks below and would be clobbered if we spread
		// `INITIAL_DOWNLOAD_STATE` here).
		setState((prev) => ({
			...prev,
			downloadProgress: 0,
			downloadStatus: "",
			downloadedBytes: null,
			totalBytes: null,
			speedBps: null,
			etaSeconds: null,
			isPaused: false,
		}));
	}, []);

	//Action: downloadModel ( retry on failure) ────────────
	//
	// On failure: keep `downloadingModel` set so the
	// `<DownloadProgressBar>` stays mounted, and record the failure in
	// `failedDownload` so the bar can render the inline error state +
	//Retry button ( priority #3). The toast with the Retry
	//action button () is preserved as a secondary affordance.
	// On success: clear `downloadingModel` (unmount the bar) and
	// `failedDownload` (clear any stale failure for a re-download).
	const downloadModel = useCallback(
		async (model: ModelInfo) => {
			setState((prev) => ({
				...prev,
				downloadingModel: model.name,
				failedDownload: null,
			}));
			resetProgress();
			try {
				const result = await call<{
					success: boolean;
					error?: string;
					message?: string;
					cancelled?: boolean;
				}>("download_model", { model: model.name });
				if (result.success) {
					setModels((prev) => {
						const anyActive = prev.some((m) => m.isActive);
						return prev.map((m) =>
							m.name === model.name
								? { ...m, downloaded: true, isActive: !anyActive }
								: m,
						);
					});
					showSnack(
						result.message ||
							t("models.snack.downloaded", { name: model.name }),
						"success",
					);
					// Success → unmount the bar + clear any stale failure.
					setState((prev) => ({
						...prev,
						downloadingModel: null,
						failedDownload: null,
					}));
				} else if (result.cancelled) {
					// User-initiated cancel: the cancel path
					// (handleCancelDownload) already surfaced the
					// "cancelled" snackbar and cleared state. The
					// pending download_model resolves after the
					// cancel IPC completes — treat the cancelled
					// resolution as a clean stop (unmount the bar,
					// no failure toast).
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
		[call, resetProgress, showSnack, setModels],
	);

	//Action: retryDownload ( priority #3) ───────────────────
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

	//Action: installDeps ( fix #7) ────────────────────────
	//
	// Triggered by the "Download Deps" button on dep-gated models
	// (currently Parakeet). The backend may or may not expose an
	// `install_parakeet_deps` IPC — if it doesn't, we fall back to the
	// existing instruction snackbar so the user knows how to proceed
	// manually. Tracks `installingDepsModel` so the button can show
	//`aria-busy` + a "Downloading…" label swap ().
	const installDeps = useCallback(
		async (model: ModelInfo) => {
			setState((prev) => ({ ...prev, installingDepsModel: model.name }));
			try {
				const result = await call<{ success: boolean; error?: string }>(
					"install_parakeet_deps",
					{ model: model.name },
				);
				if (result?.success) {
					// Success → the dedicated ``depsInstalled`` key (the
					// manual-hint key below is a failure-path message).
					showSnack(t("models.snack.depsInstalled"), "success");
					await refreshModelStatus();
				} else {
					// Backend doesn't actually install — surface the
					// manual-install hint (generic {name} key so it also
					// reads correctly for Qwen, which gates on qwen_asr).
					showSnack(
						t("models.snack.depsRequiredName", { name: model.name }),
						"warning",
					);
				}
			} catch {
				// IPC unavailable — fall back to the manual hint.
				showSnack(
					t("models.snack.depsRequiredName", { name: model.name }),
					"warning",
				);
			} finally {
				setState((prev) => ({ ...prev, installingDepsModel: null }));
			}
		},
		[refreshModelStatus, showSnack, call],
	);

	// ── Action: handleTogglePause / handleCancelDownload ────────────
	//
	// `state.isPaused` is in the dep array so the closure captures the
	// fresh value (mirrors the original code's `[call, isPaused, showSnack]`
	// deps). The IPC call (`pause_model_download` vs.
	// `resume_model_download`) is chosen based on the closure value.
	const handleTogglePause = useCallback(async () => {
		setState((prev) => ({ ...prev, isPaused: !prev.isPaused }));
		try {
			if (state.isPaused) {
				await call("resume_model_download");
			} else {
				await call("pause_model_download");
			}
		} catch (err) {
			setState((prev) => ({ ...prev, isPaused: !prev.isPaused }));
			const reason = userFacingErrorMessage(err, t, formatErrorMessage(err));
			showSnack(
				state.isPaused
					? t("models.snack.resumeFailed", { error: reason })
					: t("models.snack.pauseFailed", { error: reason }),
				"error",
			);
		}
	}, [call, state.isPaused, showSnack]);

	const handleCancelDownload = useCallback(async () => {
		try {
			await call("cancel_model_download");
			showSnack(t("models.snack.cancelled"), "warning");
		} catch (err) {
			showSnack(
				t("models.snack.cancelFailed", {
					error: userFacingErrorMessage(err, t, formatErrorMessage(err)),
				}),
				"error",
			);
		} finally {
			// Always clear local download state on cancel — whether the
			// IPC succeeded or failed, the user has signalled intent to
			// cancel. The bar unmounts (`downloadingModel = null`),
			// the inline error UI is cleared (`failedDownload = null`),
			// and progress counters reset. The backend may still be
			// downloading, but the renderer's view reflects the user's
			// intent and the next download_progress event (if any)
			// will re-establish state.
			setState((prev) => ({
				...prev,
				downloadingModel: null,
				failedDownload: null,
			}));
			resetProgress();
		}
	}, [call, showSnack, resetProgress]);

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
		installingDepsModel,
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
		installingDepsModel,
		downloadModel,
		retryDownload,
		installDeps,
		handleTogglePause,
		handleCancelDownload,
	};
}
