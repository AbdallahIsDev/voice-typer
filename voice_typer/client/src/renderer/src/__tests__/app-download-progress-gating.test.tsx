/**
 *  vitest suite — App.tsx `download_progress` subscription gating.
 *
 * `connectingProgress` is ONLY consumed by `<ConnectionStatusScreen>`,
 * which App renders exclusively when `connectionStatus !== "connected"`.
 * Updating `connectingProgress` while connected is wasted work — it
 * triggers an App re-render for a state value nobody reads. The fix
 * mirrors `connectionStatus` into a ref and short-circuits the
 * `usePythonEvent("download_progress", ...)` handler when connected.
 *
 * This test mocks:
 *   - `usePythonEvent` — captures the registered handler so the test
 *     can invoke it directly with a synthetic `download_progress`
 *     payload.
 *   - `useConnection` — controls `connectionStatus` so the test can
 *     flip between "connecting" and "connected" between renders.
 *   - `<ConnectionStatusScreen>` — exposes `connectingProgress` via a
 *     `data-connecting-progress` attribute so the test can observe
 *     whether the state update fired.
 *   - All child pages + window chrome — trivial stubs so the App
 *     render graph stays isolated.
 *
 * Assertions:
 *   1. When `connectionStatus === "connecting"`, invoking the captured
 *      `download_progress` handler with `{ progress: 50 }` updates
 *      `connectingProgress` to 50 (visible in the mocked
 *      ConnectionStatusScreen's data attribute).
 *   2. When `connectionStatus === "connected"`, invoking the same
 *      handler with `{ progress: 75 }` does NOT update
 *      `connectingProgress` — it stays at its previous value because
 *      the handler short-circuits. The mocked ConnectionStatusScreen
 *      is also unmounted (App renders the active page instead).
 */
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ── Hoisted mock state ────────────────────────────────────────────────
// `connectionStatusRef` lets the test flip the mocked useConnection's
// returned `connectionStatus` between renders without re-defining the
// mock factory. `capturedHandlerRef` captures the last
// `download_progress` handler registered by App via usePythonEvent so
// the test can invoke it directly.
const { mockCall, mockPythonEvent, connectionStatusRef, capturedHandlerRef } =
	vi.hoisted(() => ({
		mockCall: vi.fn(),
		mockPythonEvent: vi.fn(
			(type: string, handler: (data?: unknown) => unknown) => {
				// Capture only the download_progress handler — App registers
				// several usePythonEvent subscriptions (navigate, paste_failed,
				// download_progress) and we only care about the latter.
				if (type === "download_progress") {
					capturedHandlerRef.current = handler;
				}
			},
		),
		connectionStatusRef: { current: "connecting" as string },
		capturedHandlerRef: {
			current: null as ((data?: unknown) => unknown) | null,
		},
	}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
	usePythonEvent: mockPythonEvent,
}));

vi.mock("@/hooks/useConnection", () => ({
	useConnection: () => ({
		recordingState: "idle" as const,
		connectionStatus: connectionStatusRef.current,
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

//HelpOverlay pulls in Dialog + many icons + i18n keys. Stub it to a
// no-op so the App render graph stays focused on the
// download_progress gating behaviour under test.
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

//ConnectionStatusScreen mock: exposes `connectingProgress` via a
// data attribute so the test can assert whether the state update
// fired. App only renders this component when
// `connectionStatus !== "connected"`.
vi.mock("@/components/layout/ConnectionStatusScreen", () => ({
	ConnectionStatusScreen: ({
		connectingProgress,
	}: {
		connectingProgress: number | null;
		status: string;
		lastError: string | null;
		onRetry: () => void;
	}) => (
		<div
			data-testid="connection-status"
			data-connecting-progress={String(connectingProgress)}
		/>
	),
}));

vi.mock("@/pages/Home", () => ({
	default: () => <div data-testid="home-page">Home</div>,
}));

import { useAppStore } from "@/stores/appStore";
import type { VoiceTyperConfig } from "@/types/config";
import { makeConfig } from "./helpers/fixtures";

describe("App — download_progress subscription gating", () => {
	beforeEach(() => {
		cleanup();
		mockCall.mockReset();
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(makeConfig());
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});
		mockPythonEvent.mockClear();
		capturedHandlerRef.current = null;
		connectionStatusRef.current = "connecting";
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

	it("download_progress handler updates connectingProgress when connectionStatus === 'connecting'", async () => {
		// Initial mount: status is "connecting" so App renders
		// ConnectionStatusScreen with connectingProgress=null.
		connectionStatusRef.current = "connecting";
		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			const el = document.querySelector("[data-testid='connection-status']");
			expect(el).toBeTruthy();
		});

		// The download_progress handler should have been registered.
		expect(capturedHandlerRef.current).not.toBeNull();

		// Invoke the handler with a synthetic progress payload.
		capturedHandlerRef.current?.({ progress: 50 });

		// connectingProgress should have flipped from null to 50.
		await waitFor(() => {
			const el = document.querySelector("[data-testid='connection-status']");
			expect(el?.getAttribute("data-connecting-progress")).toBe("50");
		});
	});

	it("download_progress handler is a NO-OP when connectionStatus === 'connected'", async () => {
		// First mount as "connecting" and seed connectingProgress=42.
		connectionStatusRef.current = "connecting";
		const { default: App } = await import("@/App");
		const { rerender } = render(<App />);

		await waitFor(() => {
			expect(
				document.querySelector("[data-testid='connection-status']"),
			).toBeTruthy();
		});

		capturedHandlerRef.current?.({ progress: 42 });
		await waitFor(() => {
			const el = document.querySelector("[data-testid='connection-status']");
			expect(el?.getAttribute("data-connecting-progress")).toBe("42");
		});

		// Now flip to "connected". App re-renders and unmounts
		// ConnectionStatusScreen (replaces it with the active page).
		connectionStatusRef.current = "connected";
		rerender(<App />);

		await waitFor(() => {
			expect(
				document.querySelector("[data-testid='connection-status']"),
			).toBeNull();
			expect(document.querySelector("[data-testid='home-page']")).toBeTruthy();
		});

		// Invoke the download_progress handler with a new value. The
		// handler's connectionStatusRef check should short-circuit and
		// NOT call setConnectingProgress.
		capturedHandlerRef.current?.({ progress: 99 });

		// Flip back to "connecting" and verify connectingProgress is
		// null — cleared by App's leave-connecting effect — and NOT 99.
		// This proves both behaviors: the { progress: 99 } event was a
		// no-op while connected, and a stale percentage does not
		// survive a disconnect/reconnect flap (App.tsx clears
		// connectingProgress whenever connectionStatus !==
		// "connecting").
		connectionStatusRef.current = "connecting";
		rerender(<App />);

		await waitFor(() => {
			const el = document.querySelector("[data-testid='connection-status']");
			expect(el).toBeTruthy();
			// null (cleared), not 99 — the { progress: 99 } event was
			// ignored while connected.
			expect(el?.getAttribute("data-connecting-progress")).toBe("null");
		});
	});

	it("usePythonEvent('download_progress', ...) is always registered (no conditional hook calls)", async () => {
		// The subscription itself is always registered (rules-of-hooks
		// forbids conditionally calling usePythonEvent); the GATING
		// happens inside the handler via a connectionStatus ref. This
		// test confirms the registration count is NON-ZERO in both
		// states so we don't accidentally introduce a conditional hook
		// call (which would crash App at runtime under React's
		// rules-of-hooks lint/check).
		connectionStatusRef.current = "connecting";
		const { default: App } = await import("@/App");
		const { rerender } = render(<App />);

		await waitFor(() => {
			expect(
				document.querySelector("[data-testid='connection-status']"),
			).toBeTruthy();
		});

		const initialCount = mockPythonEvent.mock.calls.filter(
			([type]) => type === "download_progress",
		).length;
		// At least one registration happened on mount (App may have
		// re-rendered for unrelated reasons — useConnection internal
		// state, setLocale on mount, etc. — so the count can be > 1;
		// the key assertion is "the hook IS being called", not "exactly
		// once").
		expect(initialCount).toBeGreaterThanOrEqual(1);

		// Re-render with a different connectionStatus — the hook should
		// STILL be called (proving we didn't introduce a conditional
		// `if (status !== 'connected') usePythonEvent(...)` anti-pattern).
		connectionStatusRef.current = "connected";
		rerender(<App />);

		await waitFor(() => {
			expect(document.querySelector("[data-testid='home-page']")).toBeTruthy();
		});

		const afterCount = mockPythonEvent.mock.calls.filter(
			([type]) => type === "download_progress",
		).length;
		expect(afterCount).toBeGreaterThan(initialCount);
	});
});
