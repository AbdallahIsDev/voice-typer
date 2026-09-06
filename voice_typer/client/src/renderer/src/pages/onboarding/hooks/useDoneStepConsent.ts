import { useCallback, useEffect, useState } from "react";
import { useLatestRef } from "@/hooks/useLatestRef";
import { usePython } from "@/hooks/usePython";
import { DONE_STEP_NAME } from "../lib/constants";

/**
 * Done-step consent gate, extracted from Onboarding.tsx.
 *
 * Owns the voice_biometric_consent gate on the Done step:
 *
 *  - a one-shot ``get_config`` probe that pre-accepts the checkbox for a
 *    user who already consented via Settings → Privacy,
 *  - ``handleConsentToggle``, which persists the flag immediately (so it
 *    is set even if the user closes the window without clicking Get
 *    Started) and reverts the checkbox when the write fails.
 *
 * Backend contract: recording is refused without this flag
 * (recording_controller), so the Get Started button stays disabled
 * until the user accepts. Only ``voice_biometric_consent`` is persisted
 * here — the HuggingFace consent for model downloads is granted
 * explicitly on the Model step (the app never downloads a model
 * automatically, so there is no hidden download to consent to).
 *
 * @param stepName The current wizard step name; the probe fires only
 *                 when the wizard reaches the Done step.
 */
export function useDoneStepConsent(stepName: string | undefined): {
	consentAccepted: boolean;
	consentPersisting: boolean;
	handleConsentToggle: (nextChecked: boolean) => void;
} {
	const { call } = usePython();
	// callRef mirror (Home.tsx pattern): the consent-probe effect below
	// must not depend on the `call` identity — a test mock handing out a
	// fresh `call` per render would re-fire the get_config probe on every
	// render (OOM loop class). ``callRef.current`` is read instead.
	const callRef = useLatestRef(call);
	const [consentAccepted, setConsentAccepted] = useState(false);
	const [consentPersisting, setConsentPersisting] = useState(false);

	useEffect(() => {
		if (stepName !== DONE_STEP_NAME) return;
		let cancelled = false;
		(async () => {
			try {
				const cfg = await callRef.current<{
					voice_biometric_consent?: boolean;
				}>("get_config");
				if (cancelled) return;
				if (cfg?.voice_biometric_consent === true) {
					setConsentAccepted(true);
				}
			} catch (e) {
				// Older backend without the flag — leave
				// consent unaccepted so the user is
				// prompted to grant it.
				console.warn(
					"[renderer:Onboarding] get_config consent probe failed:",
					e,
				);
			}
		})();
		return () => {
			cancelled = true;
		};
	}, [callRef, stepName]);

	const handleConsentToggle = useCallback(
		(nextChecked: boolean) => {
			setConsentAccepted(nextChecked);
			// Persist immediately so the flag is set even if the user
			// closes the window without clicking Get Started.
			setConsentPersisting(true);
			call("set_config", {
				voice_biometric_consent: nextChecked,
			})
				.catch((e) => {
					console.error("[renderer:Onboarding] set_config consent failed:", e);
					// Revert on failure so the UI doesn't
					// claim consent was granted when it
					// wasn't persisted.
					setConsentAccepted(!nextChecked);
				})
				.finally(() => setConsentPersisting(false));
		},
		[call],
	);

	return { consentAccepted, consentPersisting, handleConsentToggle };
}
