// src/renderer/src/lib/consent.ts
//
// Renderer-side mirror of the consent-field names carried by the
// backend's structured `client.consent_required` envelope (see
// `server/handlers/_base.py` `ConsentRequiredError.to_dict()` and
// `server/recording_lifecycle.py` `VOICE_BIOMETRIC_CONSENT_FIELD`).
//
// The renderer branches on these literals in several places (the Home
// dictation gate, the App `consent_required` push handler, the
// mic-test / level-monitor consent snackbar, and the navigate
// deep-link target). A single constant prevents drift between them —
// the value is pinned on the backend side by
// `tests/test_recording_controller_lifecycle_fixes.py` and on the
// renderer side by the consent behavior tests.

import type { ShowSnackOptions, SnackbarType } from "@/hooks/useSnackbar";

/** The Config consent-field whose `False` value gates voice-biometric
 *  capture (dictation, level monitor, mic test) under GDPR Art. 9. */
export const VOICE_BIOMETRIC_CONSENT_FIELD = "voice_biometric_consent";

/** The `client.consent_required` error code (IPC validation code). */
export const CONSENT_REQUIRED_CODE = "client.consent_required";

/** Snackbar toaster signature (mirrors the session hook's type). */
type ShowSnack = (
	message: string,
	type?: SnackbarType,
	options?: ShowSnackOptions,
) => void;

/** ``t()`` signature — matches the i18n hook / module import. */
type TFunction = (key: string, params?: Record<string, string>) => string;

/**
 * Shared consent-required snackbar: ``microphone.consentRequired``
 * message with the ``microphone.consentRequiredAction`` deep-link
 * action. Used by BOTH the mic-test path
 * (``useMicrophoneTestSession.showConsentSnack``) and the level-monitor
 * race path (``useMicrophoneTest``'s ``onConsentRequired`` callback) —
 * a single definition so the toast shape can't drift between the two
 * consumers.
 *
 * @param onOpen Called when the action button is clicked. The caller
 *   closes over the consent field (e.g. ``navigate("settings",
 *   { consentField })``). When omitted, the snackbar shows WITHOUT an
 *   action button (consumers without a deep-link wiring still surface
 *   the consent message).
 */
export function showConsentRequiredSnack(
	showSnack: ShowSnack,
	t: TFunction,
	onOpen?: () => void,
): void {
	showSnack(t("microphone.consentRequired"), "warning", {
		action: onOpen
			? {
					label: t("microphone.consentRequiredAction"),
					onClick: onOpen,
				}
			: undefined,
	});
}
