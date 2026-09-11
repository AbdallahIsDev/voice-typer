// useMicPermissionRevokedToast, surfaces the mid-recording
// ``microphone_permission_revoked`` push event.
//
// The OS can revoke microphone permission WHILE a dictation is running
// (e.g. the user flips the toggle in system privacy settings). The
// backend stops the stream and publishes this dedicated event so the
// renderer can show the DISTINCT "Mic permission revoked" banner —
// without it the user sees the generic silence-auto-stop toast
// ("no audio detected") and has no idea the real cause is a permission
// change (the recording genuinely produced silence because the OS cut
// the stream).
//
// The label reuses the already-localized bubble-mode key
// ``bubble.permissionRevokedLabel`` ("Mic permission revoked", present
// in every locale): the bubble's permission-revoked mode and this toast
// describe the SAME backend state, so a second copy of the string would
// drift (E7). The native OS toast (via the backend's tray
// ``notify_safety``) carries the same message; this in-app banner
// covers the case where OS notifications are disabled.
//
// Dedupe: a short window suppresses re-toasts from the device-health
// checker re-firing before the user acts. The timestamp lives in
// `degradationToastStore` (Zustand, in its own module) so Vite HMR of
// this hook file does not reset it mid-session.

import { usePythonEvent } from "@/hooks/usePython";
import type { TranslateFn } from "@/i18n/translate-types";
import { useDegradationToastStore } from "@/stores/degradationToastStore";
import { SNACKBAR_DEFAULT_DURATION_MS, useSnackbar } from "./useSnackbar";

/** Sonner id (single replaceable surface). */
const MIC_PERMISSION_REVOKED_TOAST_ID = "mic-permission-revoked";

/** Suppression window for back-to-back events (ms). */
const MIC_PERMISSION_REVOKED_TOAST_COOLDOWN_MS = 10_000;

/**
 * Subscribe to ``microphone_permission_revoked`` push events and show
 * the dedicated warning banner. Call once at the top level of a
 * component (App wires it with the i18n `t` function).
 *
 * @param t i18n translate function (from useT).
 */
export function useMicPermissionRevokedToast(t: TranslateFn): void {
	const { showSnack } = useSnackbar();

	usePythonEvent("microphone_permission_revoked", () => {
		const now = Date.now();
		const store = useDegradationToastStore.getState();
		const last = store.micPermissionRevokedAt;
		if (
			last !== null &&
			now - last < MIC_PERMISSION_REVOKED_TOAST_COOLDOWN_MS
		) {
			return undefined;
		}
		store.setMicPermissionRevokedAt(now);
		store.setLastAnyToastShownAt(now);

		showSnack(t("bubble.permissionRevokedLabel"), "warning", {
			id: MIC_PERMISSION_REVOKED_TOAST_ID,
			duration: SNACKBAR_DEFAULT_DURATION_MS.error,
		});
		return undefined;
	});
}
