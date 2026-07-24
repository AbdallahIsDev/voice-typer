/**
 * useConnectionToasts — surfaces backend connection-state transitions as
 * sonner toasts AND triggers a theme reload when the backend recovers.
 *
 * Extracted from App.tsx (BG-27, Phase 4.5 spaghetti split) to keep
 * App.tsx a pure layout shell. Behaviour is byte-identical to the original
 * inline effect:
 *
 *   - On every `connectionStatus` change, update `prevConnectionRef` and:
 *       - if transitioning INTO "connected" from any non-connected state,
 *         call `reloadThemeFromConfig()` so theme prefs the user changed
 *         during the outage are applied;
 *       - if transitioning INTO "disconnected", fire `toast.error(
 *         app.lostConnection)` with the hint as the description;
 *       - if transitioning INTO "restarting", fire `toast.warning(
 *         app.restartingBackend)` with the hint as the description;
 *       - if transitioning INTO "connected" from any state OTHER than
 *         "connecting" (i.e. a RECOVERY, not the initial connect), fire
 *         `toast.success(about.connected)`. The initial-connect path is
 *         suppressed so the user doesn't get a "Connected!" toast on
 *         every app launch.
 *
 * The hook returns the `prevConnectionRef` so the caller's aria-live
 * region can read the previous value (needed to announce RECOVERIES only,
 * not the initial connecting → connected transition).
 *
 * PVT-fix-20: original behaviour — toasts reuse existing i18n keys
 * (`app.lostConnection`, `app.restartingBackend`, `about.connected`) so no
 * new translation keys are required.
 */
import { useEffect, useRef } from "react";
import { toast } from "sonner";
import type { ConnectionStatus } from "@/stores/appStore";

/** Minimal `t` function type matching i18n.t's signature. */
type TFn = (key: string, params?: Record<string, string>) => string;

interface UseConnectionToastsArgs {
	/** Current backend connection status (from useConnection / appStore). */
	connectionStatus: ConnectionStatus;
	/** Reload theme from backend config (from useTheme). */
	reloadThemeFromConfig: () => void;
	/** i18n translate function (from useT). */
	t: TFn;
}

export function useConnectionToasts({
	connectionStatus,
	reloadThemeFromConfig,
	t,
}: UseConnectionToastsArgs) {
	// Tracks the previous connection status across renders so each toast
	// fires exactly once per transition (not on every re-render). The
	// initial mount path (prev === connectionStatus === "connecting")
	// doesn't fire a toast — only state CHANGES do.
	const prevConnectionRef = useRef<ConnectionStatus>(connectionStatus);

	useEffect(() => {
		const prev = prevConnectionRef.current;
		prevConnectionRef.current = connectionStatus;
		if (prev !== "connected" && connectionStatus === "connected") {
			reloadThemeFromConfig();
		}

		// PVT-fix-20: surface connection-state transitions as toasts
		// so the user gets immediate visual feedback when the backend
		// drops out, restarts, or recovers — previously the only
		// feedback was the connecting/disconnected/restarting swap
		// inside the main content area, which a user looking at the
		// Home mic button could easily miss. Toasts reuse existing
		// i18n keys (`app.lostConnection`, `app.restartingBackend`,
		// `about.connected`) so no new translation keys are required.
		//
		// Transitions are tracked via the `prev` ref so each toast
		// fires exactly once per transition (not on every re-render).
		// The initial mount path (prev === connectionStatus ===
		// "connecting") doesn't fire a toast — only state CHANGES do.
		if (prev !== connectionStatus) {
			if (connectionStatus === "disconnected") {
				toast.error(t("app.lostConnection"), {
					description: t("app.lostConnectionHint"),
					duration: 6000,
				});
			} else if (connectionStatus === "restarting") {
				toast.warning(t("app.restartingBackend"), {
					description: t("app.restartingHint"),
					duration: 4000,
				});
			} else if (connectionStatus === "connected" && prev !== "connecting") {
				// Don't toast on the initial connect (prev ===
				// "connecting") — the user just launched the app
				// and doesn't need a "Connected!" toast. Only
				// surface RECOVERIES from a disconnected/restarting
				// state.
				toast.success(t("about.connected"), { duration: 3000 });
			}
		}
	}, [connectionStatus, reloadThemeFromConfig, t]);

	return prevConnectionRef;
}
