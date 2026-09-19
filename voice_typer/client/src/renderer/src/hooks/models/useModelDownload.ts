/**
 * useModelDownload, download-progress slice of the Models page.
 *
 */
import { useCallback, useRef, useState } from "react";
import { type PythonCall, usePythonEvent } from "@/hooks/usePython";
import type { ShowSnackOptions } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import { userFacingErrorMessage } from "@/lib/errors/userFacingErrorMessage";
import { formatErrorMessage, type ModelInfo } from "@/lib/utils/models";
import {
	applyDownloadPatch,
	buildDownloadProgressPatch,
	type DownloadState,
	type FailedDownload,
	INITIAL_DOWNLOAD_STATE,
	withResetProgress,
} from "./downloadState";

export type { DownloadState, FailedDownload };

interface UseModelDownloadArgs {
	call: PythonCall;
	showSnack: (
		message: string,
		kind: "success" | "error" | "warning" | "info",
		options?: ShowSnackOptions,
	) => void;
	setModels: React.Dispatch<React.SetStateAction<ModelInfo[]>>;
	/**
	 * Full reconcile after a successful download: re-fetches config +
	 * status so the Active badge reflects BACKEND truth (the backend
	 */
	reconcileAfterDownload: () => Promise<void>;
	/**
	 * Fired once per successful download (after the reconcile) so the
	 * composer can auto-select the just-downloaded model. The model is
	 */
	onDownloaded?: (model: ModelInfo) => void;
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
	/**
	 * When set, the in-flight download has failed. The
	 * `<DownloadProgressBar>` consumes this to render the inline error
	 */
	failedDownload: FailedDownload | null;
	downloadModel: (model: ModelInfo) => Promise<void>;
	retryDownload: (model: ModelInfo) => Promise<void>;
	handleTogglePause: () => Promise<void>;
	/**
	 * Cancel the ACTIVE download (no argument, legacy shape, wired
	 * to the progress bar's Cancel button), or cancel/remove a named
	 */
	handleCancelDownload: (modelName?: string) => Promise<void>;
}

export function useModelDownload({
	call,
	showSnack,
	setModels,
	reconcileAfterDownload,
	onDownloaded,
}: UseModelDownloadArgs): UseModelDownloadResult {
	const [state, setState] = useState<DownloadState>(INITIAL_DOWNLOAD_STATE);

	const downloadRunRef = useRef(0);
	// transfer's still-pending promise must stay current (its eventual
	const barOwnerRunRef = useRef<number | null>(null);

	usePythonEvent(
		"download_progress",
		useCallback(
			(data: Record<string, unknown> | undefined): (() => void) | undefined => {
				const patch = buildDownloadProgressPatch(data);
				if (patch) {
					setState((prev) => applyDownloadPatch(prev, patch));
				}
				return undefined;
			},
			[],
		),
	);

	const resetProgress = useCallback(() => {
		setState(withResetProgress);
	}, []);

	const downloadModel = useCallback(
		async (model: ModelInfo) => {
			// below) and must not touch state.
			const runId = ++downloadRunRef.current;
			const isCurrent = () =>
				downloadRunRef.current === runId || barOwnerRunRef.current === runId;

			//     occupied, no recorded failure) → do NOT claim and do
			setState((prev) => {
				if (
					prev.downloadingModel != null &&
					prev.downloadingModel !== model.name &&
					prev.failedDownload == null
				) {
					return prev;
				}
				if (
					prev.downloadingModel === model.name &&
					prev.failedDownload == null
				) {
					return prev;
				}
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
					/**
					 * Set when the request was accepted into the pending
					 * download queue instead of starting immediately (a
					 */
					queued?: boolean;
					/**
					 * Set when the backend refused to start because the
					 * model is ALREADY the download in flight (a re-click
					 */
					download_already_active?: boolean;
				}>("download_model", { model: model.name });
				if (!isCurrent()) {
					// stale resolution must be a no-op.
					return;
				}
				if (result.queued) {
					showSnack(
						result.message ||
							t("models.snack.downloadQueued", { name: model.name }),
						"info",
					);
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
								? // downloaded: true only, the ACTIVE badge is
									{ ...m, downloaded: true, isActive: false }
								: m,
						),
					);
					showSnack(
						result.message ||
							t("models.snack.downloaded", { name: model.name }),
						"success",
					);
					barOwnerRunRef.current = null;
					setState((prev) => ({
						...prev,
						downloadingModel: null,
						failedDownload: null,
					}));
					await reconcileAfterDownload();
					onDownloaded?.({ ...model, downloaded: true });
				} else if (result.cancelled) {
					barOwnerRunRef.current = null;
					setState((prev) => ({
						...prev,
						downloadingModel: null,
						failedDownload: null,
					}));
					resetProgress();
				} else {
					const message =
						result.error ||
						t("models.snack.downloadFailedName", { name: model.name });
					setState((prev) => ({
						...prev,
						failedDownload: { modelName: model.name, error: message },
					}));
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
					error: userFacingErrorMessage(err, t, formatErrorMessage(err)),
				});
				setState((prev) => ({
					...prev,
					failedDownload: { modelName: model.name, error: message },
				}));
				showSnack(message, "error", {
					action: {
						label: t("microphone.retry"),
						onClick: () => {
							void downloadModel(model);
						},
					},
				});
			}
			// here, the failure branch must keep `downloadingModel` set
		},
		[
			call,
			resetProgress,
			showSnack,
			setModels,
			reconcileAfterDownload,
			onDownloaded,
		],
	);

	const retryDownload = useCallback(
		async (model: ModelInfo) => {
			setState((prev) => ({ ...prev, failedDownload: null }));
			await downloadModel(model);
		},
		[downloadModel],
	);

	const handleTogglePause = useCallback(async () => {
		const wasPaused = state.isPaused;
		setState((prev) => ({
			...prev,
			isPaused: !prev.isPaused,
			...(wasPaused ? {} : { speedBps: null, etaSeconds: null }),
		}));
		try {
			if (wasPaused) {
				const result = await call<{ resumed?: boolean }>(
					"resume_model_download",
				);
				if (result?.resumed === false) {
					setState((prev) => ({ ...prev, isPaused: wasPaused }));
					showSnack(t("models.snack.resumeNoop"), "info");
				}
			} else {
				const result = await call<{ paused?: boolean }>("pause_model_download");
				if (result?.paused === false) {
					setState((prev) => ({ ...prev, isPaused: wasPaused }));
					showSnack(t("models.snack.pauseNoop"), "info");
				}
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
			let cancelledActiveTransfer = false;
			try {
				if (modelName) {
					const result = await call<{
						cancelled?: boolean;
						removed_from_queue?: boolean;
					}>("cancel_model_download", { model: modelName });
					if (result?.removed_from_queue) {
						showSnack(
							t("models.snack.queuedCancelled", { name: modelName }),
							"info",
						);
					} else if (result?.cancelled) {
						cancelledActiveTransfer = true;
						showSnack(t("models.snack.cancelled"), "warning");
					} else {
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
					downloadRunRef.current += 1;
					barOwnerRunRef.current = null;
					setState((prev) => ({
						...prev,
						downloadingModel: null,
						failedDownload: null,
					}));
					resetProgress();
				}
			}
		},
		[call, showSnack, resetProgress],
	);

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
