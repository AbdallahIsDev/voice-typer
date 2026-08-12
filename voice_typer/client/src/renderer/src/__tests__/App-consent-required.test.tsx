/**
 * vitest suite — App.tsx `consent_required` push subscription.
 *
 * The backend (recording_lifecycle.py) publishes a `consent_required`
 * push event when dictation start is refused for missing
 * ``voice_biometric_consent`` — the path for entry points the renderer
 * can't gate client-side (F2 hotkey, tray click action, sandboxed
 * bubble window). App.tsx subscribes and surfaces an in-app consent
 * prompt + Settings → Privacy deep-link instead of the silent
 * tray-only refusal.
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
 *      `{ consent_field: "voice_biometric_consent" }` surfaces the
 *      consent toast (warning) with an action whose label is the
 *      "Open Privacy settings" translation.
 *   2. Invoking the handler with the HuggingFace shape
 *      (`{ provider, model }` — no `consent_field`) does NOT fire the
 *      toast: that shape is handled by the model-download flow.
 */
import { cleanup, render, waitFor } from "@testing-library/react";
import { toast } from "sonner";
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
vi.mock("@/hooks/useNavigation", () => ({
	useNavigation: () => ({
		currentPage: "home" as const,
		navigate: mockNavigate,
		replace: vi.fn(),
		goBack: vi.fn(),
		goForward: vi.fn(),
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
		handleRetryConnection: vi.fn(),
	}),
}));

vi.mock("@/hooks/useTheme", () => ({
	useTheme: () => ({
		themeMode: "system" as const,
		handleThemeChange: vi.fn(),
		reloadThemeFromConfig: vi.fn(),
		textSize: 14,
		setTextSize: vi.fn(),
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

import { useAppStore } from "@/stores/appStore";
import type { VoiceTyperConfig } from "@/types/config";
import { makeConfig } from "./helpers/fixtures";

// The exact English values from en.json — the test asserts the toast
// text the user actually sees (the real i18n resolver is NOT mocked).
const CONSENT_BODY =
	"Voice biometric consent is required to start recording.\nEnable it in Settings > Privacy > Voice Biometric Consent.";
const CONSENT_ACTION_LABEL = "Open Privacy settings";

describe("App — consent_required push handler (GDPR Art. 9 dictation gate)", () => {
	beforeEach(() => {
		cleanup();
		mockCall.mockReset();
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(makeConfig());
			return Promise.resolve({});
		});
		mockPythonEvent.mockClear();
		mockNavigate.mockClear();
		vi.mocked(toast.warning).mockClear();
		capturedHandlerRef.current = null;
		localStorage.clear();
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

	it("surfaces the biometric consent toast with a Settings deep-link when consent_field is voice_biometric_consent", async () => {
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

		expect(toast.warning).toHaveBeenCalledWith(
			CONSENT_BODY,
			expect.objectContaining({
				action: expect.objectContaining({
					label: CONSENT_ACTION_LABEL,
					onClick: expect.any(Function),
				}),
			}),
		);

		// Clicking the action deep-links with the consent field so
		// Settings jumps to the EXACT Voice Biometric toggle.
		const toastOptions = vi.mocked(toast.warning).mock.calls[0]?.[1] as {
			action?: { onClick: () => void };
		};
		toastOptions.action?.onClick();
		expect(mockNavigate).toHaveBeenCalledWith("settings", {
			consentField: "voice_biometric_consent",
		});
	});

	it("does NOT fire the consent toast for the HuggingFace shape (provider/model, no consent_field)", async () => {
		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(document.querySelector("[data-testid='home-page']")).toBeTruthy();
		});
		expect(capturedHandlerRef.current).not.toBeNull();

		// The HuggingFace consent event (service/model.py) carries
		// provider/model — the model-download flow handles it, so the
		// app-level biometric handler must ignore it.
		capturedHandlerRef.current?.({
			provider: "huggingface",
			model: "some-model",
			message: "HuggingFace consent required before downloading model.",
		});

		expect(toast.warning).not.toHaveBeenCalled();
	});
});
