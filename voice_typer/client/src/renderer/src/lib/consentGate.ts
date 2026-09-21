// src/renderer/src/lib/consentGate.ts
// Unified point-of-use consent gate ("Allow? Yes/No") for EVERY
// consent-gated feature. Any flow that hits a `client.consent_required`
// envelope (or a backend `consent_required` push event) opens a single
// in-app dialog that can grant the consent in place, no Settings
// archaeology, and optionally retries the action that was blocked.
// The dialog itself lives in `components/consent/ConsentGateDialog.tsx`;
// this module owns the store + the consent-field → i18n-key mapping so
// callers don't each re-derive the message text (E7/DRY).
// Consent fields are pinned on the backend side by
// `config_validators/__init__.py` (all bool, SEC-002 allowlisted for
// set_config) and mirrored in `types/config.ts`. The i18n bodies below
// reuse the existing per-consent strings where possible; new keys live
// under `consentDialog.*` in all 8 locale files.

import { create } from "zustand";

export interface ConsentGateRequest {
	consentField: string;
	bodyKey: string;
	bodyParams?: Record<string, string>;
	onAllow?: () => void | Promise<void>;
}

interface ConsentGateState {
	request: ConsentGateRequest | null;
	open: (request: ConsentGateRequest) => void;
	close: () => void;
}

export const useConsentGateStore = create<ConsentGateState>((set) => ({
	request: null,
	open: (request) => set({ request }),
	close: () => set({ request: null }),
}));

/** Convenience hook-free accessor for non-React callers. */
export function openConsentGate(request: ConsentGateRequest): void {
	useConsentGateStore.getState().open(request);
}

export function consentBodyKey(consentField: string): string {
	const key = `consentDialog.field.${consentField}`;
	// No default fallback needed at the type level, the i18n layer
	// returns the key itself for an unknown field, which is
	// acceptable for a never-expected value; every known field has a
	// pinned entry in all 8 locales (C-I18N-1).
	return key;
}

export const CONSENT_FIELD_NAMES = [
	"voice_biometric_consent",
	"huggingface_consent",
	"cloud_openai_consent",
	"cloud_groq_consent",
	"cloud_deepgram_consent",
	"llm_polish_consent",
] as const;

export type ConsentFieldName = (typeof CONSENT_FIELD_NAMES)[number];

export const DICTATION_RETRY_CONSENT_FIELDS: readonly ConsentFieldName[] =
	CONSENT_FIELD_NAMES.filter(
		(field) =>
			field === "voice_biometric_consent" ||
			(field.startsWith("cloud_") && field.endsWith("_consent")),
	);

/** True when `field` is a consent field the unified gate can grant. */
export function isConsentField(field: string): field is ConsentFieldName {
	return (CONSENT_FIELD_NAMES as readonly string[]).includes(field);
}
