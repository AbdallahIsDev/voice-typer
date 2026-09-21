import { useCallback, useEffect, useRef } from "react";
import { useShallow } from "zustand/react/shallow";
import { useLatestRef } from "@/hooks/useLatestRef";
import { usePythonEvent } from "@/hooks/usePython";
import { useT } from "@/i18n/i18n";
import { useAppStore } from "@/stores/appStore";
import type { VoiceTyperConfig } from "@/types/config";
import type { Page } from "@/types/ipc";
import {
	applyStatusWithReason,
	asRecordingState,
	BACKGROUND_RECONNECT_INTERVAL_MS,
	CONNECTION_PROBE_MAX_RETRIES,
	CONNECTION_PROBE_RETRY_DELAY_MS,
	HEALTH_CHECK_EVENT_GRACE_MS,
	HEALTH_CHECK_INTERVAL_MS,
	HEALTH_CHECK_MAX_RETRIES,
	HEALTH_CHECK_RETRY_DELAY_MS,
	MAX_BACKGROUND_RECONNECTS,
	RESPAWN_EXHAUSTED_CODE,
} from "./connectionStatus";

interface UseConnectionArgs {
	/**
	 * Python bridge `call` function (from usePython).
	 */
	call: <T = unknown>(
		type: string,
		data?: Record<string, unknown>,
	) => Promise<T>;
	/**
	 * @deprecated accepted + discarded; first-run check is unconditional.
	 */
	currentPage?: Page;
	/**
	 * Navigate callback (used to route to onboarding on first run).
	 */
	navigate: (page: Page) => void;
}

/**
 * Python-backend connection lifecycle + pushed recording state + TCP recovery.
 * Zustand appStore-backed. C-HOME-1: recordingState and lastError always
 */
export function useConnection({
	call,
	currentPage: _currentPage,
	navigate,
}: UseConnectionArgs) {
	const t = useT();
	const { setConnectionStatus, setRecordingState, setLastError, setConfig } =
		useAppStore(
			useShallow((s) => ({
				setConnectionStatus: s.setConnectionStatus,
				setRecordingState: s.setRecordingState,
				setLastError: s.setLastError,
				setConfig: s.setConfig,
			})),
		);
	const connectionStatus = useAppStore((s) => s.connectionStatus);
	const recordingState = useAppStore((s) => s.recordingState);
	const lastError = useAppStore((s) => s.lastError);

	const lastEventReceivedAtRef = useRef<number>(0);
	const markEventReceived = useCallback(() => {
		lastEventReceivedAtRef.current = Date.now();
	}, []);

	const callRef = useLatestRef(call);

	useEffect(() => {
		let retries = 0;
		const maxRetries = CONNECTION_PROBE_MAX_RETRIES;
		let timer: ReturnType<typeof setTimeout>;
		let cancelled = false;

		const checkConnection = async () => {
			if (cancelled) return;
			try {
				const cfg = await callRef.current<VoiceTyperConfig>("get_config");
				if (!cancelled) {
					setConnectionStatus("connected");
					setConfig(cfg);
					callRef
						.current<{ status?: string; message?: string }>("get_status")
						.then((s) => {
							if (!cancelled && s?.status) {
								const validated = asRecordingState(s.status);
								if (validated) {
									applyStatusWithReason(
										validated,
										typeof s.message === "string" ? s.message : null,
										setRecordingState,
										setLastError,
									);
								}
							}
						})
						.catch((err) =>
							console.warn("[renderer:useConnection] get_status failed:", err),
						);
					const pos = cfg?.bubble_position;
					if (pos === "bottom" || pos === "top") {
						window.bubble?.setPosition?.(pos);
					}
					const draggable = cfg?.bubble_draggable;
					if (typeof draggable === "boolean") {
						window.bubble?.setDraggable?.(draggable);
					}
					const behavior = cfg?.bubble_behavior;
					const showOnStartup = cfg?.bubble_show_on_startup;
					if (behavior === "always_visible" && showOnStartup !== false) {
						window.bubble?.show?.();
					}

					if (!cancelled) {
						try {
							const fr = await callRef.current<{ is_first_run: boolean }>(
								"onboarding_is_first_run",
							);
							if (!cancelled && fr?.is_first_run) {
								navigate("onboarding");
							}
						} catch (e) {
							console.warn(
								"[renderer:useConnection] onboarding_is_first_run probe failed:",
								e,
							);
						}
					}
				}
			} catch (err) {
				console.warn(
					`[renderer:useConnection] get_config connection probe failed (attempt ${retries + 1}/${maxRetries}):`,
					err,
				);
				retries++;
				if (!cancelled && retries < maxRetries) {
					timer = setTimeout(checkConnection, CONNECTION_PROBE_RETRY_DELAY_MS);
				} else if (!cancelled) {
					setConnectionStatus("disconnected");
				}
			}
		};

		checkConnection();

		return () => {
			cancelled = true;
			clearTimeout(timer);
		};
	}, [
		navigate,
		setConnectionStatus,
		setRecordingState,
		setLastError,
		setConfig,
		callRef,
	]);

	useEffect(() => {
		if (connectionStatus !== "connected") return;

		let cancelled = false;
		let retryTimer: ReturnType<typeof setTimeout> | undefined;
		let failureCount = 0;

		const probe = async (isRetry: boolean): Promise<void> => {
			const lastEventMs = lastEventReceivedAtRef.current;
			if (
				lastEventMs > 0 &&
				Date.now() - lastEventMs < HEALTH_CHECK_EVENT_GRACE_MS
			) {
				failureCount = 0;
				return;
			}
			try {
				await callRef.current("get_status");
				failureCount = 0;
			} catch {
				if (cancelled) return;
				if (isRetry) failureCount++;
				else failureCount = 1;
				if (failureCount > HEALTH_CHECK_MAX_RETRIES) {
					setConnectionStatus("disconnected");
				} else {
					retryTimer = setTimeout(() => {
						probe(true);
					}, HEALTH_CHECK_RETRY_DELAY_MS);
				}
			}
		};

		const interval = setInterval(() => probe(false), HEALTH_CHECK_INTERVAL_MS);

		return () => {
			cancelled = true;
			clearInterval(interval);
			if (retryTimer) clearTimeout(retryTimer);
		};
	}, [connectionStatus, setConnectionStatus, callRef]);

	useEffect(() => {
		if (connectionStatus !== "disconnected") return;

		let cancelled = false;
		let attempts = 0;
		let timer: ReturnType<typeof setTimeout> | undefined;

		const tryReconnect = async () => {
			if (cancelled) return;
			if (attempts >= MAX_BACKGROUND_RECONNECTS) return;
			attempts++;
			try {
				const cfg = await callRef.current<VoiceTyperConfig>("get_config");
				if (!cancelled) {
					setConnectionStatus("connected");
					setConfig(cfg);
					setLastError(null);
					// reconnect snapshot must hydrate pill + reason line
					callRef
						.current<{ status?: string; message?: string }>("get_status")
						.then((s) => {
							if (!cancelled && s?.status) {
								const validated = asRecordingState(s.status);
								if (validated) {
									applyStatusWithReason(
										validated,
										typeof s.message === "string" ? s.message : null,
										setRecordingState,
										setLastError,
									);
								}
							}
						})
						.catch((err) =>
							console.warn(
								"[renderer:useConnection] background-reconnect get_status failed:",
								err,
							),
						);
				}
			} catch {
				if (!cancelled && attempts < MAX_BACKGROUND_RECONNECTS) {
					timer = setTimeout(tryReconnect, BACKGROUND_RECONNECT_INTERVAL_MS);
				}
			}
		};

		timer = setTimeout(tryReconnect, BACKGROUND_RECONNECT_INTERVAL_MS);

		return () => {
			cancelled = true;
			if (timer) clearTimeout(timer);
		};
	}, [
		connectionStatus,
		setConnectionStatus,
		setConfig,
		setLastError,
		setRecordingState,
		callRef,
	]);

	usePythonEvent(
		"status_change",
		useCallback(
			(data): (() => void) | undefined => {
				markEventReceived();
				if (data?.status) {
					const validated = asRecordingState(data.status);
					if (validated) {
						// why recordingState + lastError must move together
						applyStatusWithReason(
							validated,
							data.message,
							setRecordingState,
							setLastError,
						);
					}
				}
				return undefined;
			},
			[markEventReceived, setRecordingState, setLastError],
		),
	);

	usePythonEvent(
		"error",
		useCallback(
			(data): (() => void) | undefined => {
				markEventReceived();
				if (
					typeof data?.message === "string" ||
					typeof data?.code === "string"
				) {
					// event with `code: "respawn_exhausted"`. The UI must
					if (data?.code === RESPAWN_EXHAUSTED_CODE) {
						setLastError(t("connection.respawnFailed"));
						setConnectionStatus("disconnected");
					} else if (typeof data?.message === "string") {
						setLastError(data.message);
						// error line must render. The respawn_exhausted branch
						setRecordingState("error");
					}
				}
				return undefined;
			},
			[
				markEventReceived,
				setLastError,
				setRecordingState,
				setConnectionStatus,
				t,
			],
		),
	);

	usePythonEvent(
		"reconnecting",
		useCallback((): (() => void) | undefined => {
			markEventReceived();
			setConnectionStatus("reconnecting");
			return undefined;
		}, [markEventReceived, setConnectionStatus]),
	);
	usePythonEvent(
		"reconnected",
		useCallback((): (() => void) | undefined => {
			markEventReceived();
			call<VoiceTyperConfig>("get_config")
				.then((cfg) => {
					setConfig(cfg);
					setConnectionStatus("connected");
					// Same status catch-up as the other connect paths: without it the
					// pill/description keep the pre-respawn state (C-HOME-1).
					call<{ status?: string; message?: string }>("get_status")
						.then((s) => {
							if (s?.status) {
								const validated = asRecordingState(s.status);
								if (validated) {
									applyStatusWithReason(
										validated,
										typeof s.message === "string" ? s.message : null,
										setRecordingState,
										setLastError,
									);
								}
							}
						})
						.catch((err) =>
							console.warn(
								"[renderer:useConnection] reconnected get_status failed:",
								err,
							),
						);
				})
				.catch(() => setConnectionStatus("disconnected"));
			return undefined;
		}, [
			markEventReceived,
			call,
			setConfig,
			setConnectionStatus,
			setRecordingState,
			setLastError,
		]),
	);

	usePythonEvent(
		"state_changed",
		useCallback(
			(data): (() => void) | undefined => {
				markEventReceived();
				setConnectionStatus("connected");

				const rawStatus = data?.status;
				const validated =
					typeof rawStatus === "string" ? asRecordingState(rawStatus) : null;
				if (validated) {
					applyStatusWithReason(
						validated,
						typeof data?.message === "string" ? data.message : null,
						setRecordingState,
						setLastError,
					);
				} else {
					setLastError(null);
				}
				return undefined;
			},
			[markEventReceived, setConnectionStatus, setLastError, setRecordingState],
		),
	);

	const handleRetryConnection = useCallback(async () => {
		setConnectionStatus("connecting");
		try {
			await call("get_config");
			setConnectionStatus("connected");
			return;
		} catch {}
		try {
			const res = await window.window_?.restartBackend?.();
			if (res?.ok) {
				setConnectionStatus("restarting");
				return;
			}
			setLastError(t("connection.restartBackendHint"));
			setConnectionStatus("disconnected");
		} catch (e) {
			console.warn(
				"[renderer:useConnection] restartBackend escalation failed:",
				e,
			);
			setLastError(t("connection.restartBackendHint"));
			setConnectionStatus("disconnected");
		}
	}, [call, setConnectionStatus, setLastError, t]);

	return {
		recordingState,
		connectionStatus,
		lastError,
		handleRetryConnection,
	};
}
