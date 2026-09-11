// useMicrophoneDisconnectedToast, surfaces the recorder-stream
// ``microphone_disconnected`` push event on the SAME recovery surface
// as ``device_lost``.
//
// The backend detects an active-microphone loss in TWO subsystems:
// the mic-test level monitor (publishes ``device_lost``) and the
// DICTATION recorder stream (mic_lifecycle_hooks publishes
// ``microphone_disconnected``, the fast OS-event unplug path and the
// disconnect-retry-exhaustion path). Before this hook the
// recorder-stream event was dropped at the host's event gate, so a mic
// unplugged mid-DICTATION produced no in-app recovery banner at all.
//
// This hook routes the recorder-stream loss into the exact surface
// `useDeviceLostToast` renders for the level-monitor loss:
//   - the shared `deviceLostStore` (Microphone-page meter pause +
//     recovery banner) is marked lost with a distinct source tag;
//   - the SAME toast copy + sonner id ("device-lost"), a loss detected
//     by both subsystems REPLACES the in-flight toast instead of
//     stacking two "microphone disconnected" toasts for one physical
//     unplug;
//   - the toast's 10s global dedupe window lives in the shared store
//     (`lastToastShownAt` + the exported
//     `DEVICE_LOST_TOAST_DEDUPE_MS` constant), so both events share
//     ONE suppression clock and ONE window, they cannot drift apart.

import { usePythonEvent } from "@/hooks/usePython";
import type { TranslateFn } from "@/i18n/translate-types";
import {
	DEVICE_LOST_TOAST_DEDUPE_MS,
	useDeviceLostStore,
} from "@/stores/deviceLostStore";
import { SNACKBAR_DEFAULT_DURATION_MS, useSnackbar } from "./useSnackbar";

/** Source tag recorded in the shared device-lost store for losses the
 *  RECORDER stream detected (the level-monitor paths use their own
 *  `stream_finished` / `zero_chunks` tags via `useDeviceLostToast`). */
const DEVICE_LOST_SOURCE = "recording_stream";

/**
 * Subscribe to ``microphone_disconnected`` push events; route them to
 * the shared device-lost recovery surface. Call once at the top level
 * of a component (App wires it with the i18n `t` function + a
 * navigate callback).
 *
 * @param t i18n translate function (from useT).
 * @param onOpenMicrophone callback that navigates to the Microphone
 *   page (App wires ``() => navigate("microphone")``).
 */
export function useMicrophoneDisconnectedToast(
	t: TranslateFn,
	onOpenMicrophone: () => void,
): void {
	const { showSnack } = useSnackbar();
	usePythonEvent("microphone_disconnected", () => {
		const now = Date.now();
		const store = useDeviceLostStore.getState();
		// Always record the loss (the Microphone page reads it to pause
		// its meter + show the recovery banner), even when the shared
		// dedupe window suppresses the toast.
		store.markLost(DEVICE_LOST_SOURCE);

		const lastShown = store.lastToastShownAt;
		if (lastShown !== null && now - lastShown < DEVICE_LOST_TOAST_DEDUPE_MS) {
			return undefined;
		}
		store.setLastToastShownAt(now);

		showSnack(t("degradation.deviceLost"), "warning", {
			// Same id as `useDeviceLostToast`, a physical unplug detected
			// by both subsystems replaces the in-flight toast rather
			// than stacking.
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
