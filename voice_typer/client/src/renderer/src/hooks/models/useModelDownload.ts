/**
 * useModelDownload — download-progress slice of the Models page.
 *
 * DT-34 (Phase 4.5 spaghetti split): extracted from the former
 * `useModelLifecycle.ts` (995-line) monolith. This sub-hook owns the
 * download progress state machine and the three actions that drive it:
 *   • `downloadModel` — kicks off a model download + surfaces failures
 *     via a sonner toast with a "Retry" action button (PVT-032 —
 *     `showSnack` has no action-button affordance so we bypass it for
 *     the retry-toast path).
 *   • `installDeps` — fires the optional `install_parakeet_deps` IPC
 *     and falls back to the manual-install hint when the IPC is
 *     unavailable (PVT-003 fix #7).
 *   • `handleTogglePause` / `handleCancelDownload` — pause/resume/cancel
 *     the in-flight download.
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
	downloadModel: (model: ModelInfo) => Promise<void>;
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

	// ── Action: downloadModel (PVT-032 retry on failure) ────────────
	const downloadModel = useCallback(
		async (model: ModelInfo) => {
			setDownloadingModel(model.name);
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
				} else {
					// PVT-032: surface the failure with a Retry action button.
					// `showSnack` doesn't support action buttons, so we go
					// through sonner's `toast.error` directly — the global
					// Toaster in App.tsx renders it identically.
					const message =
						result.error ||
						t("models.snack.downloadFailedName", { name: model.name });
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
				// PVT-032: same retry affordance on thrown errors.
				toast.error(message, {
					duration: 8000,
					action: {
						label: t("microphone.retry"),
						onClick: () => {
							void downloadModel(model);
						},
					},
				});
			} finally {
				setDownloadingModel(null);
			}
		},
		[call, resetProgress, showSnack, setModels],
	);

	// ── Action: installDeps (PVT-003 fix #7) ────────────────────────
	//
	// Triggered by the "Download Deps" button on dep-gated models
	// (currently Parakeet). The backend may or may not expose an
	// `install_parakeet_deps` IPC — if it doesn't, we fall back to the
	// existing instruction snackbar so the user knows how to proceed
	// manually.
	const installDeps = useCallback(
		async (model: ModelInfo) => {
			try {
				const result = await call<{ success: boolean; error?: string }>(
					"install_parakeet_deps",
					{ model: model.name },
				);
				if (result?.success) {
					// BG-49: previously the success branch reused the
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
			// reset local download state immediately on
			// success so the model card stops showing the
			// progress bar / pause button / cancel button
			// without waiting for the backend's terminal
			// download_progress event (which can race with
			// the cancel ack or be missed entirely if the WS
			// frame is dropped).
			setDownloadingModel(null);
			resetProgress();
		} catch (err) {
			showSnack(
				t("models.snack.cancelFailed", { error: formatErrorMessage(err) }),
				"error",
			);
			// even if IPC failed, the user has
			// signalled intent to cancel — clear the local
			// download state so the card UI doesn't stay
			// stuck mid-download. The backend may still be
			// downloading, but the renderer's view reflects
			// the user's intent and the next download_progress
			// event (if any) will re-establish state.
			setDownloadingModel(null);
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
		downloadModel,
		installDeps,
		handleTogglePause,
		handleCancelDownload,
	};
}
