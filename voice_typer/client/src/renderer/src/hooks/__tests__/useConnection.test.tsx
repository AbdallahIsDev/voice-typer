/**
 * Tests for useConnection — onboarding first-run auto-route (F1 / b-review Finding 5).
 *
 * Regression: previously the first-run check was gated on
 * `currentPage === "home"`, so a user who closed the app mid-onboarding
 * while on "settings" (or any non-home page) would land back on that page
 * on next launch (useNavigation restores the persisted page from
 * localStorage on mount) and the wizard would be silently skipped.
 *
 * After the fix, useConnection checks first-run unconditionally on the
 * initial connection probe and force-navigates to "onboarding" if
 * `is_first_run` is true, regardless of the persisted page.
 *
 * We use a minimal harness component that wires useNavigation + useConnection
 * together (mirroring App.tsx) so we don't need to mock the entire page
 * graph. The navigation state is asserted via the rendered page label.
 */
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useConnection } from "@/hooks/useConnection";
import { useNavigation } from "@/hooks/useNavigation";

// Hoist the mock call/event handlers so they're available inside the
// vi.mock factory (which is hoisted to the top of the file by vitest
// and runs before any other code).
const { mockCall, mockPythonEvent } = vi.hoisted(() => ({
	mockCall: vi.fn(),
	mockPythonEvent: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
	usePythonEvent: mockPythonEvent,
}));

// Stub localStorage so useNavigation's persisted-nav-state restore
// (and the beforeEach `clear()` below) doesn't blow up in the jsdom
// environment. Same pattern as useTheme-flush-pending-save.test.tsx
// — jsdom 29 with an opaque origin doesn't expose `localStorage`.
const lsStub: Record<string, string> = {};
const lsMock = {
	getItem: (k: string) => lsStub[k] ?? null,
	setItem: (k: string, v: string) => {
		lsStub[k] = v;
	},
	removeItem: (k: string) => {
		delete lsStub[k];
	},
	clear: () => {
		for (const k of Object.keys(lsStub)) delete lsStub[k];
	},
};
Object.defineProperty(window, "localStorage", {
	value: lsMock,
	configurable: true,
});

// Stub the Zustand store-backed setters useConnection depends on. We
// import the real appStore and let it manage state — it's already
// isolated per-test via its own slice, so no extra mocking needed.

/**
 * Minimal harness that mirrors what App.tsx does for routing + first-run:
 *   1. useNavigation restores the persisted page from localStorage.
 *   2. useConnection probes the backend on mount and (after the fix)
 *      force-navigates to "onboarding" if is_first_run is true.
 *   3. The current page is rendered as a text label so tests can assert.
 */
function Harness() {
	const { currentPage, navigate } = useNavigation();
	useConnection({
		call: (async (type: string, _data?: Record<string, unknown>) =>
			mockCall(type)) as unknown as <T = unknown>(
			type: string,
			data?: Record<string, unknown>,
		) => Promise<T>,
		currentPage,
		navigate,
	});
	return <div data-testid="current-page">{currentPage}</div>;
}

describe("useConnection — F1: first-run auto-route ignores persisted page", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockPythonEvent.mockReset();
		localStorage.clear();
		// Reset the module registry so useNavigation's
		// loadNavState runs fresh on each test (it caches the
		// initial parse in a useState initializer).
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("navigates to onboarding when is_first_run=true even if persisted page is settings", async () => {
		// Step 1: pre-seed localStorage so useNavigation restores
		// currentPage="settings" on mount (mirrors a user who
		// navigated to settings mid-onboarding then closed the app).
		localStorage.setItem(
			"vt_nav_state",
			JSON.stringify({
				page: "settings",
				history: ["home", "settings"],
				index: 1,
			}),
		);

		// Step 2: mock the IPC handlers useConnection + Onboarding will call.
		// get_config returns a config with onboarding_completed=false.
		// onboarding_is_first_run returns is_first_run=true.
		mockCall.mockImplementation((type: string) => {
			switch (type) {
				case "get_config":
					return Promise.resolve({
						onboarding_completed: false,
						hotkey: "<f2>",
						model_size: "small.en",
						microphone: "",
					});
				case "get_status":
					return Promise.resolve({ status: "idle" });
				case "onboarding_is_first_run":
					return Promise.resolve({ is_first_run: true });
				default:
					return Promise.resolve({});
			}
		});

		// Step 3: render the harness. useNavigation restores
		// "settings" from localStorage on mount; useConnection
		// probes the backend and should fire navigate("onboarding").
		const { getByTestId } = render(<Harness />);

		// Initially the persisted page is rendered.
		expect(getByTestId("current-page").textContent).toBe("settings");

		// After the connection probe resolves, the first-run
		// check should fire navigate("onboarding") regardless of
		// the persisted page.
		await waitFor(() => {
			expect(getByTestId("current-page").textContent).toBe("onboarding");
		});

		// Sanity: onboarding_is_first_run was actually called.
		expect(mockCall).toHaveBeenCalledWith("onboarding_is_first_run");
	});

	it("does NOT navigate to onboarding when is_first_run=false", async () => {
		// Persisted page = settings; backend reports first_run=false.
		localStorage.setItem(
			"vt_nav_state",
			JSON.stringify({
				page: "settings",
				history: ["home", "settings"],
				index: 1,
			}),
		);

		mockCall.mockImplementation((type: string) => {
			switch (type) {
				case "get_config":
					return Promise.resolve({
						onboarding_completed: true,
					});
				case "get_status":
					return Promise.resolve({ status: "idle" });
				case "onboarding_is_first_run":
					return Promise.resolve({ is_first_run: false });
				default:
					return Promise.resolve({});
			}
		});

		const { getByTestId } = render(<Harness />);
		expect(getByTestId("current-page").textContent).toBe("settings");

		// Give the async effect a chance to run; the page should
		// stay "settings" because is_first_run is false. We wait for
		// the first-run probe to be invoked (positive signal that the
		// async connection chain has progressed), then assert no
		// navigation fired.
		await waitFor(() => {
			expect(mockCall).toHaveBeenCalledWith("onboarding_is_first_run");
		});
		expect(getByTestId("current-page").textContent).toBe("settings");
	});

	it("navigates to onboarding when is_first_run=true and persisted page is home", async () => {
		// Baseline case: persisted page is "home" (the original
		// happy path that already worked before the fix). This
		// guards against regressions where the fix accidentally
		// breaks the home-page case.
		localStorage.setItem(
			"vt_nav_state",
			JSON.stringify({
				page: "home",
				history: ["home"],
				index: 0,
			}),
		);

		mockCall.mockImplementation((type: string) => {
			switch (type) {
				case "get_config":
					return Promise.resolve({
						onboarding_completed: false,
					});
				case "get_status":
					return Promise.resolve({ status: "idle" });
				case "onboarding_is_first_run":
					return Promise.resolve({ is_first_run: true });
				default:
					return Promise.resolve({});
			}
		});

		const { getByTestId } = render(<Harness />);
		await waitFor(() => {
			expect(getByTestId("current-page").textContent).toBe("onboarding");
		});
	});
});
