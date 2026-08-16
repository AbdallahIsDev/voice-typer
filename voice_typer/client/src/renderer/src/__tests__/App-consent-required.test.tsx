/**
 * vitest suite — App.tsx `consent_required` push subscription.
 *
 * The backend publishes a `consent_required` push event when a
 * consent-gated action is refused: dictation start without
 * ``voice_biometric_consent`` (recording_lifecycle.py — the path for
 * entry points the renderer can't gate client-side: F2 hotkey, tray
 * click action, sandboxed bubble window), cloud-provider consents,
 * the LLM-polish consent, the offline-pack consent. App.tsx
 * subscribes and opens the UNIFIED point-of-use consent dialog
 * (Allow → persists the consent → retries the refused action) instead
 * of the old toast + Settings navigation.
 *
 * This test mocks:
 *   - `usePythonEvent` — captures the registered `consent_required`
 *     handler so the test can invoke it directly with synthetic
 *     payloads.
 *   - `useConnection` — pins `connectionStatus` to "connected" so App
 *     renders the active page (the handler is app-level, not
 *     page-level, so the page under render doesn't matter).
 *   - All child pages + window chrome — trivial stubs so the App
 *     render graph stays isolated.
 *
 * Assertions:
 *   1. Invoking the captured handler with
 *      `{ consent_field: "voice_biometric_consent" }` opens the
 *      consent gate (store request carries the field + body key) with
 *      a dictation retry (Allow → toggle_dictation).
 *   2. Same for a cloud-provider consent field (cloud_groq_consent).
 *   3. Invoking the handler with the HuggingFace shape
 *      (`{ provider, model }` — no `consent_field`) does NOT open the
 *      gate: that shape is handled by the model-download flow.
 */
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ── Hoisted mock state ────────────────────────────────────────────────
// `capturedHandlerRef` captures the last `consent_required` handler
// registered by App via usePythonEvent so the test can invoke it
// directly.
const { mockCall, mockPythonEvent, capturedHandlerRef, mockNavigate } =
	vi.hoisted(() => ({
		mockCall: vi.fn(),
		mockPythonEvent: vi.fn(
			(type: string, handler: (data?: unknown) => unknown) => {
				// Capture only the consent_required handler — App registers
				// several usePythonEvent subscriptions (navigate,
				// paste_failed, download_progress, consent_required).
				if (type === "consent_required") {
					capturedHandlerRef.current = handler;
				}
			},
		),
		capturedHandlerRef: {
			current: null as ((data?: unknown) => unknown) | null,
		},
		mockNavigate: vi.fn(),
	}));

// App destructures the full navigation API; mock it so the test can
// assert the consent deep-link navigate call (``("settings",
// { consentField })``) without triggering a real route change.
const stable = vi.hoisted(() => ({
	handleRetryConnection: vi.fn(),
	handleThemeChange: vi.fn(),
	reloadThemeFromConfig: vi.fn(),
	setTextSize: vi.fn(),
	replace: vi.fn(),
	goBack: vi.fn(),
	goForward: vi.fn(),
}));

vi.mock("@/hooks/useNavigation", () => ({
	useNavigation: () => ({
		currentPage: "home" as const,
		navigate: mockNavigate,
		replace: stable.replace,
		goBack: stable.goBack,
		goForward: stable.goForward,
		canGoBack: false,
		canGoForward: false,
	}),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
	usePythonEvent: mockPythonEvent,
}));

vi.mock("@/hooks/useConnection", () => ({
	useConnection: () => ({
		recordingState: "idle" as const,
		connectionStatus: "connected" as const,
		lastError: null,
		handleRetryConnection: stable.handleRetryConnection,
	}),
}));

vi.mock("@/hooks/useTheme", () => ({
	useTheme: () => ({
		themeMode: "system" as const,
		handleThemeChange: stable.handleThemeChange,
		reloadThemeFromConfig: stable.reloadThemeFromConfig,
		textSize: 14,
		setTextSize: stable.setTextSize,
	}),
}));

vi.mock("@/hooks/useSoundFeedback", () => ({
	useSoundFeedback: () => {},
}));

vi.mock("@/components/feedback/ErrorBoundary", () => ({
	ErrorBoundary: ({ children }: { children: React.ReactNode }) => (
		<>{children}</>
	),
}));

vi.mock("@/components/ui/sonner", () => ({
	Toaster: () => null,
}));

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: () => <span data-testid="hugeicon" />,
}));

vi.mock("@hugeicons/core-free-icons", async () => {
	const { createHugeiconsMock } = await import(
		"@/__tests__/helpers/hugeicons-mock"
	);
	return createHugeiconsMock();
});

vi.mock("@/components/help/HelpOverlay", () => ({
	HelpOverlay: () => null,
}));

vi.mock("sonner", () => ({
	toast: {
		success: vi.fn(),
		error: vi.fn(),
		warning: vi.fn(),
		info: vi.fn(),
		dismiss: vi.fn(),
	},
	Toaster: () => null,
}));

vi.mock("next-themes", () => ({
	useTheme: () => ({ theme: "light" as const }),
}));

// ── Mock window-chrome + pages so App renders without the bridge ─────
vi.mock("@/components/layout/Sidebar", () => ({
	Sidebar: () => <nav data-testid="sidebar" />,
}));

vi.mock("@/components/layout/TitleBar", () => ({
	TitleBar: () => <div data-testid="titlebar" />,
}));

vi.mock("@/components/layout/ConnectionStatusScreen", () => ({
	ConnectionStatusScreen: () => <div data-testid="connection-status" />,
}));

vi.mock("@/pages/Home", () => ({
	default: () => <div data-testid="home-page">Home</div>,
}));

import { useConsentGateStore } from "@/lib/consentGate";
import { useAppStore } from "@/stores/appStore";
import type { VoiceTyperConfig } from "@/types/config";
import { makeConfig } from "./helpers/fixtures";

describe("App — consent_required push handler (unified point-of-use consent gate)", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		cleanup();
		mockCall.mockReset();
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(makeConfig());
			return Promise.resolve({});
		});
		mockPythonEvent.mockClear();
		mockNavigate.mockClear();
		capturedHandlerRef.current = null;
		localStorage.clear();
		useConsentGateStore.setState({ request: null });
		useAppStore.setState({
			connectionStatus: "connected",
			recordingState: "idle",
			lastError: null,
			config: makeConfig() as VoiceTyperConfig,
		});
	});

	afterEach(() => {
		cleanup();
	});

	it("opens the consent gate with a dictation retry for voice_biometric_consent", async () => {
		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(document.querySelector("[data-testid='home-page']")).toBeTruthy();
		});
		expect(capturedHandlerRef.current).not.toBeNull();

		// Backend publishes the dictation-refusal event (the F2 hotkey /
		// tray / bubble path the renderer can't gate client-side).
		capturedHandlerRef.current?.({
			consent_field: "voice_biometric_consent",
		});

		const req = useConsentGateStore.getState().request;
		expect(req).toEqual(
			expect.objectContaining({
				consentField: "voice_biometric_consent",
				bodyKey: "consentDialog.field.voice_biometric_consent",
				onAllow: expect.any(Function),
			}),
		);

		// The retry (Allow in the dialog) re-invokes dictation start —
		// the user never leaves the flow to dig through Settings.
		await act(async () => {
			await req?.onAllow?.();
		});
		expect(mockCall).toHaveBeenCalledWith("toggle_dictation");
	});

	it("opens the consent gate for a cloud-provider consent field (cloud_groq_consent)", async () => {
		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(document.querySelector("[data-testid='home-page']")).toBeTruthy();
		});
		expect(capturedHandlerRef.current).not.toBeNull();

		capturedHandlerRef.current?.({
			consent_field: "cloud_groq_consent",
		});

		const req = useConsentGateStore.getState().request;
		expect(req).toEqual(
			expect.objectContaining({
				consentField: "cloud_groq_consent",
				bodyKey: "consentDialog.field.cloud_groq_consent",
				onAllow: expect.any(Function),
			}),
		);
	});

	it("does NOT open the consent gate for the HuggingFace shape (provider/model, no consent_field)", async () => {
		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(document.querySelector("[data-testid='home-page']")).toBeTruthy();
		});
		expect(capturedHandlerRef.current).not.toBeNull();

		// The HuggingFace consent event (service/model.py) carries
		// provider/model — the model-download flow handles it, so the
		// app-level handler must ignore it (no consent_field).
		capturedHandlerRef.current?.({
			provider: "huggingface",
			model: "some-model",
			message: "HuggingFace consent required before downloading model.",
		});

		expect(useConsentGateStore.getState().request).toBeNull();
	});
});
