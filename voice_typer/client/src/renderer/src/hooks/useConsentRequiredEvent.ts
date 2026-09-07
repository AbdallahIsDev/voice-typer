/**
 * useConsentRequiredEvent — unified point-of-use consent gate subscriber.
 *
 * Extracted from App.tsx (the entry component stays pure wiring) using
 * the same extraction pattern as the ``use*Toast`` hooks. Behaviour is
 * byte-identical to the original inline
 * ``usePythonEvent("consent_required", ...)`` block.
 *
 * The backend publishes this event when a consent-gated action is
 * refused: dictation start without ``voice_biometric_consent``
 * (recording_lifecycle.py — the path for entry points the renderer
 * can't gate client-side: F2 hotkey, tray click action, sandboxed
 * bubble window), cloud-provider consents, the LLM-polish consent
 * (enhancement_steps.py), the offline-pack consent (update_check.py),
 * etc. Every consent field opens the SAME in-app dialog — "Allow?
 * [Allow / Cancel]" — with the exact toggle deep-link as the secondary
 * action. Dictation refusals are retried after granting (Allow →
 * toggle_dictation), so the user never leaves the flow to dig through
 * Settings. The HuggingFace ``{provider, model}`` shape (no
 * consent_field) is handled by the model-download flow, not here.
 *
 * The dictation-retry field set comes from
 * ``lib/consentGate.ts``'s ``DICTATION_RETRY_CONSENT_FIELDS`` (derived
 * from the canonical consent-field registry) — previously a parallel
 * inlined list here meant a newly added cloud provider would silently
 * lose the retry behavior.
 */

import type { PythonCall } from "@/hooks/usePython";
import { usePythonEvent } from "@/hooks/usePython";
import {
	consentBodyKey,
	DICTATION_RETRY_CONSENT_FIELDS,
	isConsentField,
	openConsentGate,
} from "@/lib/consentGate";

/** Dependencies wired by the App entry component. */
export interface UseConsentRequiredEventOptions {
	/** IPC ``call`` (from ``usePython``) — used to retry dictation on Allow. */
	call: PythonCall;
}

/**
 * Subscribe to ``consent_required`` push events and open the unified
 * point-of-use consent gate. Call once at the top level of the App
 * component; the subscription lives for the component's lifetime.
 */
export function useConsentRequiredEvent({
	call,
}: UseConsentRequiredEventOptions): void {
	usePythonEvent("consent_required", (data): (() => void) | undefined => {
		const payload = (data ?? {}) as {
			consent_field?: string;
		};
		const field = payload.consent_field;
		if (!field || !isConsentField(field)) {
			return undefined;
		}
		// Dictation-start refusals can be retried after granting: the
		// dialog's Allow handler re-invokes toggle_dictation (start is
		// the only consent-gated direction). Other consent gates have
		// no re-runnable action from here — granting the consent is
		// enough; the user retries the action themselves.
		const dictationField = DICTATION_RETRY_CONSENT_FIELDS.includes(field);
		openConsentGate({
			consentField: field,
			bodyKey: consentBodyKey(field),
			onAllow: dictationField ? () => call("toggle_dictation") : undefined,
		});
		return undefined;
	});
}
