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
 */
import { useCallback, useState } from "react";
import { toast } from "sonner";
import { usePythonEvent } from "@/hooks/usePython";
import { t } from "@/i18n/i18n";
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

// ── Hook ──────────────────────────────────────────────────────────────

export function useModelDownload({
	call,
	showSnack,
	setModels,
	refreshModelStatus,
}: UseModelDownloadArgs): UseModelDownloadResult {
	const [downloadingModel, setDownloadingModel] = useState<string | null>(null);
	const [downloadProgress, setDownloadProgress] = useState(0);
	const [downloadStatus, setDownloadStatus] = useState("");
	const [isPaused, setIsPaused] = useState(false);
	const [downloadedBytes, setDownloadedBytes] = useState<number | null>(null);
	const [totalBytes, setTotalBytes] = useState<number | null>(null);
	const [speedBps, setSpeedBps] = useState<number | null>(null);
	const [etaSeconds, setEtaSeconds] = useState<number | null>(null);
	const [failedDownload, setFailedDownload] = useState<FailedDownload | null>(
		null,
	);
	const [installingDepsModel, setInstallingDepsModel] = useState<string | null>(
		null,
	);

	// ── download_progress event subscription ────────────────────────
	usePythonEvent(
		"download_progress",
		useCallback(
			(data: Record<string, unknown> | undefined): (() => void) | undefined => {
				if (!data) return undefined;
				if (typeof data.progress === "number")
					setDownloadProgress(data.progress);
				if (typeof data.status === "string") setDownloadStatus(data.status);
				if (typeof data.downloaded_bytes === "number")
					setDownloadedBytes(data.downloaded_bytes);
				if (typeof data.total_bytes === "number")
					setTotalBytes(data.total_bytes);
				if (typeof data.speed_bytes_per_sec === "number") {
					setSpeedBps(data.speed_bytes_per_sec);
				} else if (data.speed_bytes_per_sec == null) {
					setSpeedBps(null);
				}
				if (typeof data.eta_seconds === "number") {
					setEtaSeconds(data.eta_seconds);
				} else if (data.eta_seconds == null) {
					setEtaSeconds(null);
				}
				if (typeof data.paused === "boolean") setIsPaused(data.paused);
				if (typeof data.resumed === "boolean" && data.resumed)
					setIsPaused(false);
				return undefined;
			},
			[],
		),
	);

	const resetProgress = useCallback(() => {
		setDownloadProgress(0);
		setDownloadStatus("");
		setDownloadedBytes(null);
		setTotalBytes(null);
		setSpeedBps(null);
		setEtaSeconds(null);
		setIsPaused(false);
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
			setDownloadingModel(model.name);
			setFailedDownload(null);
			resetProgress();
			try {
				const result = await call<{
					success: boolean;
					error?: string;
					message?: string;
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
					setDownloadingModel(null);
					setFailedDownload(null);
				} else {
					// Failure → keep the bar mounted, record the failure so
					// the inline error UI + Retry button render.
					const message =
						result.error ||
						t("models.snack.downloadFailedName", { name: model.name });
					setFailedDownload({ modelName: model.name, error: message });
					//surface the failure with a Retry action button.
					// `showSnack` doesn't support action buttons, so we go
					// through sonner's `toast.error` directly — the global
					// Toaster in App.tsx renders it identically.
					toast.error(message, {
						duration: 8000,
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
					error: formatErrorMessage(err),
				});
				setFailedDownload({ modelName: model.name, error: message });
				//same retry affordance on thrown errors.
				toast.error(message, {
					duration: 8000,
					action: {
						label: t("microphone.retry"),
						onClick: () => {
							void downloadModel(model);
						},
					},
				});
			}
			// NOTE: no `finally { setDownloadingModel(null) }` here — the
			// failure branch must keep `downloadingModel` set so the bar
			// stays mounted. The success branch clears it explicitly.
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
			setFailedDownload(null);
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
			setInstallingDepsModel(model.name);
			try {
				const result = await call<{ success: boolean; error?: string }>(
					"install_parakeet_deps",
					{ model: model.name },
				);
				if (result?.success) {
					//previously the success branch reused the
					// ``parakeetDepsRequired`` ("Dependencies required
					// for Parakeet. Download first.") key — which is
					// the FAILURE / manual-hint message, not a success
					// confirmation. Use the dedicated ``depsInstalled``
					// success key instead so users see the right
					// message after a successful install.
					showSnack(t("models.snack.depsInstalled"), "success");
					await refreshModelStatus();
				} else {
					// Backend doesn't actually install — surface the
					// manual-install hint (existing i18n key).
					showSnack(t("models.snack.parakeetDepsRequired"), "warning");
				}
			} catch {
				// IPC unavailable — fall back to the manual hint.
				showSnack(t("models.snack.parakeetDepsRequired"), "warning");
			} finally {
				setInstallingDepsModel(null);
			}
		},
		[refreshModelStatus, showSnack, call],
	);

	// ── Action: handleTogglePause / handleCancelDownload ────────────
	const handleTogglePause = useCallback(async () => {
		setIsPaused((prev) => !prev);
		try {
			if (isPaused) {
				await call("resume_model_download");
			} else {
				await call("pause_model_download");
			}
		} catch (err) {
			setIsPaused((prev) => !prev);
			showSnack(
				isPaused
					? t("models.snack.resumeFailed", { error: formatErrorMessage(err) })
					: t("models.snack.pauseFailed", { error: formatErrorMessage(err) }),
				"error",
			);
		}
	}, [call, isPaused, showSnack]);

	const handleCancelDownload = useCallback(async () => {
		try {
			await call("cancel_model_download");
			showSnack(t("models.snack.cancelled"), "warning");
		} catch (err) {
			showSnack(
				t("models.snack.cancelFailed", { error: formatErrorMessage(err) }),
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
			setDownloadingModel(null);
			setFailedDownload(null);
			resetProgress();
		}
	}, [call, showSnack, resetProgress]);

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
