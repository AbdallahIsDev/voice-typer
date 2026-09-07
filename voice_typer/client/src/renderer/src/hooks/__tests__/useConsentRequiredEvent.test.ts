/**
 * Tests for useConsentRequiredEvent (extracted from App.tsx).
 *
 * Contract: subscribe to the backend ``consent_required`` push event
 * and open the unified point-of-use consent gate — with a dictation
 * retry (Allow → ``toggle_dictation``) for the registry-derived
 * dictation-retry field set, and no retry for gates that have nothing
 * to re-run. Unknown fields and the HuggingFace provider/model shape
 * must NOT open the gate.
 */
import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useConsentRequiredEvent } from "@/hooks/useConsentRequiredEvent";
import {
	CONSENT_FIELD_NAMES,
	DICTATION_RETRY_CONSENT_FIELDS,
	useConsentGateStore,
} from "@/lib/consentGate";

// Capture the handler usePythonEvent registers so we can fire it.
const registered = new Map<string, (data?: unknown) => unknown>();
const mockCall = vi.fn();

vi.mock("@/hooks/usePython", () => ({
	usePythonEvent: (type: string, handler: (data?: unknown) => unknown) => {
		registered.set(type, handler);
	},
}));

/** Return the consent_required handler (asserting it was registered). */
function getHandler(): (data?: unknown) => unknown {
	const handler = registered.get("consent_required");
	if (!handler) throw new Error("consent_required handler was not registered");
	return handler;
}

beforeEach(() => {
	registered.clear();
	vi.clearAllMocks();
	useConsentGateStore.setState({ request: null });
});

afterEach(() => {
	useConsentGateStore.setState({ request: null });
});

describe("useConsentRequiredEvent", () => {
	it("registers a handler for the consent_required event", () => {
		renderHook(() => useConsentRequiredEvent({ call: mockCall }));
		expect(registered.has("consent_required")).toBe(true);
	});

	it("opens the consent gate with a dictation retry for voice_biometric_consent", async () => {
		renderHook(() => useConsentRequiredEvent({ call: mockCall }));
		getHandler()({ consent_field: "voice_biometric_consent" });

		const req = useConsentGateStore.getState().request;
		expect(req).toEqual(
			expect.objectContaining({
				consentField: "voice_biometric_consent",
				bodyKey: "consentDialog.field.voice_biometric_consent",
				onAllow: expect.any(Function),
			}),
		);

		// Allow retries the refused dictation start.
		await req?.onAllow?.();
		expect(mockCall).toHaveBeenCalledWith("toggle_dictation");
	});

	it.each([
		"cloud_openai_consent",
		"cloud_groq_consent",
		"cloud_deepgram_consent",
	])("opens the gate with a dictation retry for %s", async (field) => {
		renderHook(() => useConsentRequiredEvent({ call: mockCall }));
		getHandler()({ consent_field: field });

		const req = useConsentGateStore.getState().request;
		expect(req?.consentField).toBe(field);
		expect(req?.bodyKey).toBe(`consentDialog.field.${field}`);
		await req?.onAllow?.();
		expect(mockCall).toHaveBeenCalledWith("toggle_dictation");
	});

	it.each([
		"llm_polish_consent",
		"offline_pack_consent",
		"huggingface_consent",
	])("opens the gate WITHOUT a retry for %s", (field) => {
		renderHook(() => useConsentRequiredEvent({ call: mockCall }));
		getHandler()({ consent_field: field });

		const req = useConsentGateStore.getState().request;
		expect(req?.consentField).toBe(field);
		// No re-runnable action from here — granting is enough.
		expect(req?.onAllow).toBeUndefined();
	});

	it("does NOT open the gate for an unknown consent field", () => {
		renderHook(() => useConsentRequiredEvent({ call: mockCall }));
		getHandler()({ consent_field: "not_a_real_consent_field" });
		expect(useConsentGateStore.getState().request).toBeNull();
	});

	it("does NOT open the gate for the HuggingFace provider/model shape", () => {
		renderHook(() => useConsentRequiredEvent({ call: mockCall }));
		getHandler()({
			provider: "huggingface",
			model: "some-model",
			message: "HuggingFace consent required before downloading model.",
		});
		expect(useConsentGateStore.getState().request).toBeNull();
	});

	it("ignores null/undefined payloads", () => {
		renderHook(() => useConsentRequiredEvent({ call: mockCall }));
		getHandler()(undefined);
		getHandler()(null);
		expect(useConsentGateStore.getState().request).toBeNull();
	});
});

describe("DICTATION_RETRY_CONSENT_FIELDS (registry-derived single source)", () => {
	it("contains exactly the dictation-retry fields of the canonical registry", () => {
		expect([...DICTATION_RETRY_CONSENT_FIELDS]).toEqual([
			"voice_biometric_consent",
			"cloud_openai_consent",
			"cloud_groq_consent",
			"cloud_deepgram_consent",
		]);
	});

	it("is derived from CONSENT_FIELD_NAMES — every entry is a known consent field", () => {
		for (const field of DICTATION_RETRY_CONSENT_FIELDS) {
			expect((CONSENT_FIELD_NAMES as readonly string[]).includes(field)).toBe(
				true,
			);
		}
	});
});
