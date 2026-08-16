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

// Shared stable-mocks preamble (see helpers/stableMocks.tsx): the
// assertable singletons + the usePython mock factory binding.
import { pythonMock, resetStableMocks, stableMocks } from "@/__tests__/helpers/stableMocks";

const { mockCall, mockPythonEvent } = stableMocks;

vi.mock("@/hooks/usePython", () => pythonMock());

import { useConnection } from "@/hooks/useConnection";
import { _resetNavigationForTest, useNavigation } from "@/hooks/useNavigation";
import { useAppStore } from "@/stores/appStore";

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
		resetStableMocks();
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
		// Shared store: re-read the freshly-seeded localStorage into
		// the module-level nav store before mounting the harness.
		_resetNavigationForTest();

		// Step 2: mock the IPC handlers useConnection + Onboarding will call.
		// get_config returns a config with onboarding_completed=false.
		// onboarding_is_first_run returns is_first_run=true.
		mockCall.mockImplementation((type: string) => {
			switch (type) {
				case "get_config":
					return Promise.resolve({
						onboarding_completed: false,
						hotkey: "<f2>",
						model_size: "tiny",
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
		// Shared store: re-read the freshly-seeded localStorage into
		// the module-level nav store before mounting the harness.
		_resetNavigationForTest();

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

	describe("status_change — error message surfacing", () => {
		beforeEach(() => {
			resetStableMocks();
			vi.resetModules();
			useAppStore.setState({ recordingState: "idle", lastError: null });
		});

		it("sets lastError from data.message on the error state (so the Home status line shows the reason)", () => {
			render(<Harness />);

			// Capture the status_change subscription registered by
			// useConnection during mount.
			const statusChangeCall = mockPythonEvent.mock.calls.find(
				(c) => c[0] === "status_change",
			);
			expect(statusChangeCall).toBeTruthy();
			if (!statusChangeCall) {
				throw new Error("expected a status_change subscription");
			}
			const handler = statusChangeCall[1] as (data: {
				status: string;
				message?: string;
			}) => void;

			// Backend pushed the error state + the tray-tooltip reason.
			const reason =
				"No models are available. Open the models page to download a model.";
			handler({ status: "error", message: reason });
			expect(useAppStore.getState().recordingState).toBe("error");
			expect(useAppStore.getState().lastError).toBe(reason);

			// A subsequent non-error transition clears lastError.
			handler({ status: "idle" });
			expect(useAppStore.getState().recordingState).toBe("idle");
			expect(useAppStore.getState().lastError).toBeNull();
		});

		it("keeps lastError null when an error status_change carries no message", () => {
			render(<Harness />);
			const statusChangeCall = mockPythonEvent.mock.calls.find(
				(c) => c[0] === "status_change",
			);
			if (!statusChangeCall) {
				throw new Error("expected a status_change subscription");
			}
			const handler = statusChangeCall[1] as (data: {
				status: string;
				message?: string;
			}) => void;

			handler({ status: "error" });
			expect(useAppStore.getState().recordingState).toBe("error");
			expect(useAppStore.getState().lastError).toBeNull();
		});
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
		// Shared store: re-read the freshly-seeded localStorage into
		// the module-level nav store before mounting the harness.
		_resetNavigationForTest();

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
