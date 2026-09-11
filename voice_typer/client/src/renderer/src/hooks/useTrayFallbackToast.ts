// useTrayFallbackToast, surfaces the ``tray_fallback_notification``
// push event (the tray-unavailable degraded-mode banner).
//
// When the native system-tray icon cannot be created (headless build,
// Linux without a systray compositor, VOICE_TYPER_NO_TRAY, …) the
// backend queues tray notifications and periodically drains the queue
// with this event, the in-app banner is the ONLY remaining user-visible
// signal that tray features are degraded and that queued notifications
// went to the log instead of the screen. Before this hook the frames
// passed the host's event gate but landed on NO subscriber.
//
// PAYLOAD: the emitter nests ``title``/``message`` under ``data`` (the
// canonical envelope, root-level fields are stripped by the
// event-protocol layer, which is why an earlier Electron-era root-level
// shape delivered an empty payload). Both fields stay optional so the
// banner still renders the generic degraded-mode copy if a future
// emitter omits them.
//
// Dedupe: the drain loop can publish one frame per queued
// notification, so a 60s window collapses a backlog into ONE banner.
// The timestamp lives in `degradationToastStore` (Zustand, in its own
// module) so Vite HMR of this hook file does not reset it mid-session.

import { usePythonEvent } from "@/hooks/usePython";
import type { TranslateFn } from "@/i18n/translate-types";
import { useDegradationToastStore } from "@/stores/degradationToastStore";
import { capToastDescription } from "./capToastDescription";
import { SNACKBAR_DEFAULT_DURATION_MS, useSnackbar } from "./useSnackbar";

/** Sonner id (single replaceable surface). */
const TRAY_FALLBACK_TOAST_ID = "tray-fallback-notification";

/** Suppression window for back-to-back queued notifications (ms) —
 * matches the backend's 60s pending-notification drain cadence. */
const TRAY_FALLBACK_TOAST_COOLDOWN_MS = 60_000;

/**
 * Subscribe to ``tray_fallback_notification`` push events and show the
 * tray-unavailable banner. Call once at the top level of a component
 * (App wires it with the i18n `t` function).
 *
 * @param t i18n translate function (from useT).
 */
export function useTrayFallbackToast(t: TranslateFn): void {
	const { showSnack } = useSnackbar();

	usePythonEvent("tray_fallback_notification", (data) => {
		const payload = (data ?? {}) as { title?: unknown; message?: unknown };
		const title = typeof payload.title === "string" ? payload.title : undefined;
		const message =
			typeof payload.message === "string" ? payload.message : undefined;
		const description =
			title !== undefined && message !== undefined
				? capToastDescription(`${title}: ${message}`)
				: title !== undefined
					? capToastDescription(title)
					: message !== undefined
						? capToastDescription(message)
						: undefined;

		const now = Date.now();
		const store = useDegradationToastStore.getState();
		const last = store.trayFallbackShownAt;
		if (last !== null && now - last < TRAY_FALLBACK_TOAST_COOLDOWN_MS) {
			return undefined;
		}
		store.setTrayFallbackShownAt(now);
		store.setLastAnyToastShownAt(now);

		showSnack(t("degradation.trayUnavailable"), "warning", {
			id: TRAY_FALLBACK_TOAST_ID,
			description,
			duration: SNACKBAR_DEFAULT_DURATION_MS.error,
		});
		return undefined;
	});
}
