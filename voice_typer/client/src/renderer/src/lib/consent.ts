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

/** The Config consent-field whose `False` value gates voice-biometric
 *  capture (dictation, level monitor, mic test) under GDPR Art. 9. */
export const VOICE_BIOMETRIC_CONSENT_FIELD = "voice_biometric_consent";

/** The `client.consent_required` error code (IPC validation code). */
export const CONSENT_REQUIRED_CODE = "client.consent_required";
