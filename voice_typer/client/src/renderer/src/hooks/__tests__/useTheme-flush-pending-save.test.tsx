/**
 * Tests for useTheme — focused on the `flushPendingThemeSave` path
 * that runs on unmount / beforeunload.
 *
 * The change under test: `flushPendingThemeSave` previously used
 * `try { void call(...) } catch (e) { console.warn(...) }` which
 * only catches SYNCHRONOUS throws from `call()` itself. If `call`
 * returned a Promise that later rejected (the common case — IPC
 * rejection on backend unavailable), the rejection was unhandled.
 * The fix uses `void call(...).catch((e) => console.warn(...))` so
 * Promise rejections are caught.
 *
 * Strategy: render a Probe that uses `useTheme`, invoke a setter
 * (`handleThemeChange`) to schedule a debounced save, then unmount
 * WITHOUT waiting for the debounce. The unmount cleanup calls
 * `flushPendingThemeSave`, which immediately fires `call("set_config",
 * pending)`. We make the mock `call` return a rejected Promise and
 * verify `console.warn` is invoked (proof the `.catch` ran) and no
 * unhandled rejection surfaces.
 */
import { act, cleanup, render } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Capture every call to the Python bridge so we can assert on the
// forwarded payload AND control resolution/rejection.
const callMock = vi.fn();
const usePythonEventMock = vi.fn();

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({
		call: callMock,
		status: "connected",
		connectionStatus: "connected",
	}),
	usePythonEvent: usePythonEventMock,
}));

vi.mock("@/i18n/i18n", () => ({
	t: (key: string) => key,
}));

vi.mock("@/lib/sound-manager", () => ({
	setSoundFeedbackEnabled: vi.fn(),
}));

vi.mock("@/themes", () => ({
	applyThemeVars: vi.fn(),
	deriveCustomVars: vi.fn(() => ({})),
	THEMES: [{ id: "default", name: "Default" }],
}));

vi.mock("@/stores/appStore", () => ({
	useAppStore: Object.assign(
		vi.fn(() => ({
			mergeConfig: vi.fn(),
		})),
		{
			getState: () => ({ mergeConfig: vi.fn() }),
			setState: vi.fn(),
		},
	),
}));

// Stub localStorage so the hook's cache-sync effect doesn't blow up
// in the jsdom environment.
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

beforeEach(() => {
	callMock.mockReset();
	usePythonEventMock.mockReset();
	// Default: call resolves to an empty object (a get_config response).
	callMock.mockResolvedValue({});
	lsMock.clear();
});

afterEach(() => {
	cleanup();
});

async function renderProbe() {
	const { useTheme } = await import("@/hooks/useTheme");
	const captures: {
		handleThemeChange: (mode: "light" | "dark" | "system") => Promise<void>;
	} = {
		handleThemeChange: () => Promise.resolve(),
	};
	function Probe() {
		const hook = useTheme(
			callMock as unknown as Parameters<typeof useTheme>[0],
		);
		captures.handleThemeChange = hook.handleThemeChange;
		return null as unknown as ReactNode;
	}
	const utils = render(<Probe />);
	return { captures, ...utils };
}

describe("useTheme — flushPendingThemeSave rejection handling", () => {
	it("calls console.warn when the flush-time set_config Promise rejects", async () => {
		const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
		// Make `call` reject for the set_config invocation. The first
		// call is the mount-time `get_config` (resolves to {}); the
		// second call is the flush-time `set_config` (rejects).
		callMock
			.mockResolvedValueOnce({}) // get_config on mount
			.mockRejectedValueOnce(new Error("backend down")); // set_config on flush

		const { captures, unmount } = await renderProbe();

		// Schedule a debounced save by changing the theme mode. This
		// populates `pendingThemeUpdatesRef.current` with `{theme_mode:"dark"}`.
		await act(async () => {
			await captures.handleThemeChange("dark");
		});

		// Unmount WITHOUT waiting for the 300ms debounce to fire.
		// The cleanup function calls `flushPendingThemeSave`, which
		// clears the timer and immediately fires `call("set_config", pending)`.
		await act(async () => {
			unmount();
			// Flush microtasks so the .catch handler runs.
			await Promise.resolve();
			await Promise.resolve();
		});

		// Verify set_config was actually called (i.e. the flush path
		// executed, not just the debounce timer getting cleared).
		const setConfigCall = callMock.mock.calls.find(
			(c) => c[0] === "set_config",
		);
		expect(setConfigCall).toBeTruthy();
		// Verify the .catch handler ran — console.warn was called with
		// the expected prefix. Without the `.catch` wiring, the
		// rejection would have surfaced as an unhandled promise
		// rejection warning (which jsdom surfaces via
		// `process.on('unhandledRejection')` — not via console.warn).
		const flushWarn = warnSpy.mock.calls.find((c) =>
			String(c[0] ?? "").includes("[useTheme] set_config (flush) failed"),
		);
		expect(flushWarn).toBeTruthy();
		warnSpy.mockRestore();
	});

	it("does NOT emit an unhandled rejection when the flush set_config rejects", async () => {
		// jsdom surfaces unhandled Promise rejections via the
		// `unhandledrejection` window event. If `flushPendingThemeSave`
		// used `void call(...)` without `.catch`, the rejection would
		// surface here. The fix installs a `.catch` handler, so no
		// `unhandledrejection` event should fire.
		const unhandledSpy = vi.fn();
		window.addEventListener("unhandledrejection", unhandledSpy);

		callMock
			.mockResolvedValueOnce({}) // get_config on mount
			.mockRejectedValueOnce(new Error("backend down")); // set_config on flush

		// Silence the expected console.warn so the test output stays clean.
		const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

		const { captures, unmount } = await renderProbe();

		await act(async () => {
			await captures.handleThemeChange("dark");
		});

		await act(async () => {
			unmount();
			// Flush multiple microtask ticks to let any unhandled
			// rejection propagate.
			await Promise.resolve();
			await Promise.resolve();
			await Promise.resolve();
		});

		expect(unhandledSpy).not.toHaveBeenCalled();
		window.removeEventListener("unhandledrejection", unhandledSpy);
		warnSpy.mockRestore();
	});
});
