// useDeviceLostToast — surfaces backend ``device_lost`` push events.
//
// The active microphone can disappear mid-session (USB unplug,
// Bluetooth power-off, driver reset). The backend detects the loss in
// BOTH subsystems that hold mic streams — the mic-test level monitor
// (`level_monitor/monitoring.py`) and the dictation recorder
// (`mic_lifecycle_hooks.py`) — retries a few times, then publishes
// ``device_lost``. Without this hook the event reached the renderer and
// was dropped: dictation silently stopped working and the Microphone
// page kept showing a dead meter with no explanation.
//
// This hook is the SINGLE consumer of the event. It:
//   1. records the loss in `deviceLostStore` (the Microphone page
//      reads it to pause the meter + show its recovery banner), and
//   2. raises one actionable global toast naming what broke and what
//      to do (reconnect the mic / pick another one), with an
//      "Open Microphone" action jumping straight to the page.
//
// Dedupe: a short GLOBAL window (10s) collapses rapid re-emissions
// (both subsystems can detect the same physical loss) into ONE visible
// notification. A fixed sonner ``id`` replaces any in-flight toast.

import { usePythonEvent } from "@/hooks/usePython";
import { useDeviceLostStore } from "@/stores/deviceLostStore";
import { SNACKBAR_DEFAULT_DURATION_MS, useSnackbar } from "./useSnackbar";

/** Minimal `t` function type matching i18n.t's signature. */
type TFn = (key: string, params?: Record<string, string>) => string;

/**
 * Global dedupe window (ms): a second ``device_lost`` event within this
 * window is recorded but does NOT re-toast. Slightly longer than the
 * toast duration so a dismiss + immediate re-fire doesn't re-nag within
 * the same notification cycle.
 */
const DEVICE_LOST_TOAST_DEDUPE_MS = 10_000;

/**
 * Subscribe to ``device_lost`` push events; show the recovery toast +
 * flip the shared device-lost state for the Microphone page. Call once
 * at the top level of a component (App); the subscription lives for the
 * component's lifetime.
 *
 * @param t i18n translate function (from useT).
 * @param onOpenMicrophone callback that navigates to the Microphone page
 *   (App wires ``() => navigate("microphone")``).
 */
export function useDeviceLostToast(t: TFn, onOpenMicrophone: () => void): void {
	const { showSnack } = useSnackbar();
	usePythonEvent("device_lost", (data): (() => void) | undefined => {
		const payload = (data ?? {}) as { source?: unknown };
		const source =
			typeof payload.source === "string" ? payload.source : "unknown";

		const now = Date.now();
		const store = useDeviceLostStore.getState();
		store.markLost(source);

		const lastShown = store.lastToastShownAt;
		if (lastShown !== null && now - lastShown < DEVICE_LOST_TOAST_DEDUPE_MS) {
			return undefined;
		}
		store.setLastToastShownAt(now);

		showSnack(t("degradation.deviceLost"), "warning", {
			id: "device-lost",
			description: t("degradation.deviceLostHint"),
			duration: SNACKBAR_DEFAULT_DURATION_MS.error,
			action: {
				label: t("nav.microphone"),
				onClick: onOpenMicrophone,
			},
		});
		return undefined;
	});
}
