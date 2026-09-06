/**
 * Tests for useDictationToggle (extracted from Home.tsx).
 *
 * Contract: the GDPR Art. 9 point-of-use consent gate for dictation —
 * the attempt flag is set BEFORE the gate so the "Preparing offline
 * engine…" banner logic in the page root sees every mic-button press;
 * without consent the unified consent gate opens (with an Allow retry
 * that starts dictation) and the IPC is NOT called; with consent (or a
 * not-yet-loaded config) the toggle IPC runs with the toggling spinner
 * flag around it.
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { toast } from "sonner";
import type { Mock } from "vitest";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useDictationToggle } from "@/pages/home/hooks/useDictationToggle";
import type { CallFn } from "@/pages/home/hooks/useFirstRecordingCelebration";
import type { VoiceTyperConfig } from "@/types/config";

// Bridge-call test double shaped as CallFn (no real bridge in unit tests).
const mockCall = vi.fn() as unknown as CallFn & Mock;
const openConsentGate = vi.fn();

vi.mock("@/i18n/i18n", () => ({
	t: (key: string) => key,
}));

vi.mock("sonner", () => ({
	toast: { error: vi.fn(), success: vi.fn() },
}));

vi.mock("@/lib/consentGate", () => ({
	openConsentGate: (request: unknown) => openConsentGate(request),
	consentBodyKey: (field: string) => `consentDialog.field.${field}`,
}));

function renderToggle(cfg: VoiceTyperConfig | null) {
	return renderHook(() => useDictationToggle(mockCall, cfg));
}

function makeConfig(
	overrides: Partial<VoiceTyperConfig> = {},
): VoiceTyperConfig {
	return {
		hotkey: "F2",
		voice_biometric_consent: true,
		...overrides,
	} as VoiceTyperConfig;
}

beforeEach(() => {
	vi.clearAllMocks();
	mockCall.mockResolvedValue(undefined);
});

describe("useDictationToggle", () => {
	it("starts idle with no attempt flag", () => {
		const { result } = renderToggle(makeConfig());
		expect(result.current.toggling).toBe(false);
		expect(result.current.hasAttemptedDictation).toBe(false);
	});

	it("opens the consent gate and skips the IPC when consent is off", async () => {
		const { result } = renderToggle(
			makeConfig({ voice_biometric_consent: false }),
		);
		await act(async () => {
			await result.current.handleToggle();
		});
		expect(openConsentGate).toHaveBeenCalledTimes(1);
		expect(openConsentGate).toHaveBeenCalledWith(
			expect.objectContaining({
				consentField: "voice_biometric_consent",
				bodyKey: "consentDialog.field.voice_biometric_consent",
				onAllow: expect.any(Function),
			}),
		);
		expect(mockCall).not.toHaveBeenCalled();
		expect(result.current.toggling).toBe(false);
		// The attempt flag is set BEFORE the gate (banner contract).
		expect(result.current.hasAttemptedDictation).toBe(true);
	});

	it("retry from the gate's onAllow starts dictation", async () => {
		const { result } = renderToggle(
			makeConfig({ voice_biometric_consent: false }),
		);
		await act(async () => {
			await result.current.handleToggle();
		});
		const request = openConsentGate.mock.calls[0]?.[0] as {
			onAllow: () => void;
		};
		await act(async () => {
			request.onAllow();
		});
		expect(mockCall).toHaveBeenCalledWith("toggle_dictation");
	});

	it("calls toggle_dictation normally when consent is granted", async () => {
		const { result } = renderToggle(makeConfig());
		expect(result.current.toggling).toBe(false);
		await act(async () => {
			await result.current.handleToggle();
		});
		expect(openConsentGate).not.toHaveBeenCalled();
		expect(mockCall).toHaveBeenCalledWith("toggle_dictation");
		await waitFor(() => {
			expect(result.current.toggling).toBe(false);
		});
		expect(result.current.hasAttemptedDictation).toBe(true);
	});

	it("skips the gate when the config snapshot has not loaded yet", async () => {
		const { result } = renderToggle(null);
		await act(async () => {
			await result.current.handleToggle();
		});
		expect(openConsentGate).not.toHaveBeenCalled();
		expect(mockCall).toHaveBeenCalledWith("toggle_dictation");
	});

	it("flips toggling while the IPC is in flight", async () => {
		let resolveToggle: () => void = () => {};
		mockCall.mockImplementation(
			() =>
				new Promise<void>((resolve) => {
					resolveToggle = resolve;
				}),
		);
		const { result } = renderToggle(makeConfig());
		let inFlight: Promise<void> = Promise.resolve();
		act(() => {
			inFlight = result.current.handleToggle();
		});
		await waitFor(() => {
			expect(result.current.toggling).toBe(true);
		});
		await act(async () => {
			resolveToggle();
			await inFlight;
		});
		expect(result.current.toggling).toBe(false);
	});

	it("toasts an error when the IPC fails and resets toggling", async () => {
		mockCall.mockRejectedValue(new Error("bridge down"));
		const { result } = renderToggle(makeConfig());
		await act(async () => {
			await result.current.handleToggle();
		});
		expect(toast.error).toHaveBeenCalledWith("home.toggleFailed");
		expect(result.current.toggling).toBe(false);
	});
});
