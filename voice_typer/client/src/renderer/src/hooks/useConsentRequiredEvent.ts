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
	call: PythonCall;
}

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
		// no re-runnable action from here, granting the consent is
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
