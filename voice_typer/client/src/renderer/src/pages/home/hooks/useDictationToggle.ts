// useDictationToggle — the mic button's dictation toggle with the
// GDPR point-of-use consent gate, extracted from Home.tsx so the page
// file stays a thin composition root. Behaviour is preserved
// statement-for-statement — the gate's order of operations
// (attempt-flag BEFORE the consent check) is privacy-contract surface
// and must not be reordered.
//
// Owns:
//
//   - `hasAttemptedDictation` — set the moment the user presses the
//     dictation toggle. The "Preparing offline engine…" banner (gated
//     on `!packReady && hasAttemptedDictation` in the page root) may
//     only surface after an actual attempt. It is set BEFORE the
//     consent gate so the banner appears even if consent is missing —
//     the user has still pressed the mic button, which counts as an
//     "attempted offline transcription".
//   - `handleToggle` — the GDPR Art. 9 gate: the backend refuses to
//     start recording without `voice_biometric_consent` — but the
//     refusal is silent over IPC (`toggle_dictation` returns `ack` and
//     only the tray notification fires). Gate client-side so the user
//     gets the unified point-of-use consent dialog (Allow → persists
//     the consent → starts recording) instead of a dead button. The
//     backend gate (recording_lifecycle.py) remains the enforcement
//     backstop for hotkey/tray-triggered dictation.
//   - `toggling` — the in-flight flag that shows the spinner overlay
//     inside the mic button.

import { useCallback, useState } from "react";
import { toast } from "sonner";
import type { PythonCall } from "@/hooks/usePython";
import { t } from "@/i18n/i18n";
import { VOICE_BIOMETRIC_CONSENT_FIELD } from "@/lib/consent";
import { consentBodyKey, openConsentGate } from "@/lib/consentGate";
import type { VoiceTyperConfig } from "@/types/config";

/**
 * Own the consent-gated dictation toggle. Call once at the top level
 * of Home.
 *
 * @param call the Python bridge `call` function (from `usePython()`).
 * @param cfg the page's config snapshot — the gate reads
 *   `voice_biometric_consent` from it; `null` (config not loaded yet)
 *   skips the gate exactly as before (the backend backstop still
 *   enforces consent for hotkey/tray dictation).
 */
export function useDictationToggle(
	call: PythonCall,
	cfg: VoiceTyperConfig | null,
) {
	const [toggling, setToggling] = useState(false);
	const [hasAttemptedDictation, setHasAttemptedDictation] = useState(false);

	const handleToggle = useCallback(async () => {
		// Mark that the user has attempted dictation so the
		// "Preparing offline engine…" banner (gated on
		// `!packReady && hasAttemptedDictation`) can surface if the
		// runtime pack isn't ready yet. Set BEFORE the consent gate
		// so the banner appears even if consent is missing — the
		// user has still pressed the mic button, which counts as an
		// "attempted offline transcription".
		setHasAttemptedDictation(true);
		// GDPR Art. 9 gate: the backend refuses to start recording without
		// ``voice_biometric_consent`` — but the refusal is silent over
		// IPC (``toggle_dictation`` returns ``ack`` and only the tray
		// notification fires). Gate client-side so the user gets the
		// unified point-of-use consent dialog (Allow → persists the
		// consent → starts recording) instead of a dead button. The
		// backend gate (recording_lifecycle.py) remains the enforcement
		// backstop for hotkey/tray-triggered dictation.
		if (cfg && !cfg.voice_biometric_consent) {
			openConsentGate({
				consentField: VOICE_BIOMETRIC_CONSENT_FIELD,
				bodyKey: consentBodyKey(VOICE_BIOMETRIC_CONSENT_FIELD),
				// Retry after granting: start dictation (the button press
				// that was blocked).
				onAllow: () => call("toggle_dictation"),
			});
			return;
		}
		setToggling(true);
		try {
			await call("toggle_dictation");
		} catch (err) {
			console.error("[renderer:Home] Toggle dictation failed:", err);
			toast.error(t("home.toggleFailed"));
		} finally {
			setToggling(false);
		}
		// ``t`` is a stable module-level import — not a render-scoped
		// value, so it must NOT be listed as a dep (biome
		// useExhaustiveDependencies flags it as unnecessary).
	}, [call, cfg]);

	return { handleToggle, toggling, hasAttemptedDictation };
}
