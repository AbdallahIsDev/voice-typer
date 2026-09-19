import { useEffect, useRef } from "react";
import type { TranslateFn } from "@/i18n/translate-types";
import type { ConnectionStatus } from "@/stores/appStore";
import { isRecoveringStatus } from "@/stores/appStore";
import { SNACKBAR_DEFAULT_DURATION_MS, useSnackbar } from "./useSnackbar";

interface UseConnectionToastsArgs {
	/** Current backend connection status (from useConnection / appStore). */
	connectionStatus: ConnectionStatus;
	/** Reload theme from backend config (from useTheme). */
	reloadThemeFromConfig: () => void;
	/** i18n translate function (from useT). */
	t: TranslateFn;
}

export function useConnectionToasts({
	connectionStatus,
	reloadThemeFromConfig,
	t,
}: UseConnectionToastsArgs) {
	const { showSnack } = useSnackbar();
	// Tracks the previous connection status across renders so each toast
	// fires exactly once per transition (not on every re-render). The
	// initial mount path (prev === connectionStatus === "connecting")
	// doesn't fire a toast, only state CHANGES do.
	const prevConnectionRef = useRef<ConnectionStatus>(connectionStatus);

	useEffect(() => {
		const prev = prevConnectionRef.current;
		prevConnectionRef.current = connectionStatus;
		if (prev !== "connected" && connectionStatus === "connected") {
			reloadThemeFromConfig();
		}

		//surface connection-state transitions as toasts
		// so the user gets immediate visual feedback when the backend
		// feedback was the connecting/disconnected/restarting swap
		// inside the main content area, which a user looking at the
		// Home mic button could easily miss. Toasts reuse existing
		// i18n keys (`app.lostConnection`, `app.restartingBackend`,
		// `about.connected`) so no new translation keys are required.
		// Transitions are tracked via the `prev` ref so each toast
		// fires exactly once per transition (not on every re-render).
		// The initial mount path (prev === connectionStatus ===
		// "connecting") doesn't fire a toast, only state CHANGES do.
		// Stable per-transition-type ``id``: sonner replaces an
		// existing toast when the same ``id`` is re-fired. Without an
		// ``id``, a backend flap (connecting → disconnected →
		// restarting → connected → disconnected → …) would stack a
		// fresh toast per transition, the user would see a pile of
		// overlapping toasts and couldn't read the sequence. With the
		// stable ids below, the LATEST transition of each type
		// REPLACES any in-flight toast of the same type, so at most
		// one disconnected / one restarting / one connected toast is
		// ever on screen, and the description text is always the most
		// recent transition's.
		if (prev !== connectionStatus) {
			if (connectionStatus === "disconnected") {
				showSnack(t("app.lostConnection"), "error", {
					id: "conn-disconnected",
					description: t("app.lostConnectionHint"),
					duration: SNACKBAR_DEFAULT_DURATION_MS.warning,
				});
			} else if (isRecoveringStatus(connectionStatus)) {
				showSnack(t("app.restartingBackend"), "warning", {
					id: "conn-restarting",
					description: t("app.restartingHint"),
					duration: SNACKBAR_DEFAULT_DURATION_MS.info,
				});
			} else if (connectionStatus === "connected" && prev !== "connecting") {
				// Don't toast on the initial connect (prev ===
				// "connecting"), the user just launched the app
				// and doesn't need a "Connected!" toast. Only
				// surface RECOVERIES from a disconnected/restarting
				// state.
				showSnack(t("about.connected"), "success", {
					id: "conn-connected",
				});
			}
		}
	}, [connectionStatus, reloadThemeFromConfig, t, showSnack]);

	return prevConnectionRef;
}
