/**
 * Tests for useTheme — focused on the singleton-store refactor that
 * eliminates the dual-instance IPC + listener duplication.
 *
 * Background
 * ----------
 * Previously: `useTheme` was called from BOTH `App.tsx` (always-mounted)
 * AND `Settings.tsx` (lazy-mounted when the user opens Settings). Each
 * call instantiated an INDEPENDENT React state (`themeMode`,
 * `themePreset`, `customTheme`, `textSize`) plus:
 *   - one `reloadThemeFromConfig` mount effect → 1 extra `get_config`
 *     IPC call per Settings open
 *   - one `config_changed` `usePythonEvent` subscription → 2
 *     subscriptions app-wide (each updated its OWN state)
 *   - one `beforeunload` flush listener → 2 listeners app-wide
 *
 * After refactor (mirrors `useNavigation.ts:126-202`):
 *   - Theme state lives in a module-level Zustand store (`useThemeStore`).
 *     Both callers READ from the same store via `useShallow`.
 *   - `reloadThemeFromConfig`, the `beforeunload` flush listener, and
 *     the debounced `scheduleThemeSave`/`flushPendingThemeSave` are
 *     module-level functions guarded by an `initOnce` flag
 *     (`themeInitStarted`). Only the FIRST `useTheme` caller actually
 *     runs them.
 *   - The `usePythonEvent("config_changed", ...)` call is kept inside
 *     the hook (rules-of-hooks), but the handler is a stable
 *     module-level singleton that updates the shared store.
 *
 * These tests verify:
 *   1. Two `useTheme` consumers share state — a setter call on one
 *      re-renders the other with the new value.
 *   2. The `reloadThemeFromConfig` side effect (the `get_config` IPC
 *      call) runs EXACTLY ONCE even when two consumers are mounted
 *      (was previously 2 calls — one per consumer).
 *   3. The `beforeunload` flush listener is installed EXACTLY ONCE
 *      even when two consumers are mounted.
 *   4. The `usePythonEvent("config_changed", ...)` subscription
 *      updates the shared store — both consumers see the new value.
 */
import { act, cleanup, render } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// `vi.hoisted` guarantees the mock fns are initialized BEFORE the
// vi.mock factory is invoked (which happens at import-resolution
// time, before any top-level `const` would normally run).
const mocks = vi.hoisted(() => ({
	callMock: vi.fn(),
	usePythonEventMock: vi.fn(),
	mergeConfigMock: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({
		call: mocks.callMock,
		status: "connected",
		connectionStatus: "connected",
	}),
	usePythonEvent: mocks.usePythonEventMock,
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
		vi.fn((selector: (s: { mergeConfig: unknown }) => unknown) =>
			selector({ mergeConfig: mocks.mergeConfigMock }),
		),
		{
			getState: () => ({ mergeConfig: mocks.mergeConfigMock }),
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

// ── Test setup ───────────────────────────────────────────────────────

beforeEach(() => {
	mocks.callMock.mockReset();
	mocks.usePythonEventMock.mockReset();
	mocks.mergeConfigMock.mockReset();
	// Default: call resolves to an empty object (a get_config response).
	mocks.callMock.mockResolvedValue({});
	lsMock.clear();
});

afterEach(() => {
	cleanup();
});

// ── Helpers ──────────────────────────────────────────────────────────

/** Render a Probe that captures the hook's return value. */
async function renderProbe(
	captures: { current: Record<string, unknown> | null },
	renderCount?: { current: number },
) {
	const { useTheme, _resetThemeStoreForTest } = await import(
		"@/hooks/useTheme"
	);
	// Reset the singleton store + initOnce flag so each test starts
	// fresh (mirrors the `_resetNavigationForTest` pattern).
	_resetThemeStoreForTest();

	function Probe() {
		if (renderCount) renderCount.current += 1;
		const hook = useTheme(
			mocks.callMock as unknown as Parameters<typeof useTheme>[0],
		);
		captures.current = hook as unknown as Record<string, unknown>;
		return null as unknown as ReactNode;
	}

	const utils = render(<Probe />);
	return { utils, useTheme, _resetThemeStoreForTest };
}

/** Extract the `config_changed` handler captured by the
 * `usePythonEvent` mock. */
type ConfigChangedHandler = (data?: unknown) => (() => void) | undefined;

function getConfigChangedHandler(): ConfigChangedHandler | null {
	for (let i = mocks.usePythonEventMock.mock.calls.length - 1; i >= 0; i--) {
		const call = mocks.usePythonEventMock.mock.calls[i];
		if (call?.[0] === "config_changed") {
			return call[1] as ConfigChangedHandler;
		}
	}
	return null;
}

// ── Tests ────────────────────────────────────────────────────────────

describe("useTheme — singleton store: dual-instance shares state", () => {
	it("a setter call on one consumer updates the other consumer's state", async () => {
		const captures1: { current: Record<string, unknown> | null } = {
			current: null,
		};
		const captures2: { current: Record<string, unknown> | null } = {
			current: null,
		};

		// Mount TWO consumers (mirrors App.tsx + Settings.tsx).
		await renderProbe(captures1);
		// The second renderProbe call also resets the store, which is
		// fine — both consumers share the same module-level store.
		const { useTheme } = await import("@/hooks/useTheme");

		function Probe2() {
			const hook = useTheme(
				mocks.callMock as unknown as Parameters<typeof useTheme>[0],
			);
			captures2.current = hook as unknown as Record<string, unknown>;
			return null as unknown as ReactNode;
		}
		const utils2 = render(<Probe2 />);

		// Initially both consumers see the same themeMode (default
		// "system" — read from the empty localStorage stub).
		expect(captures1.current?.themeMode).toBe("system");
		expect(captures2.current?.themeMode).toBe("system");

		// Change themeMode via consumer 1's setter.
		const handleThemeChange = captures1.current?.handleThemeChange as (
			mode: string,
		) => Promise<void>;
		await act(async () => {
			await handleThemeChange("dark");
		});

		// Both consumers should now see "dark" — the singleton store
		// propagated the change to both subscribers.
		expect(captures1.current?.themeMode).toBe("dark");
		expect(captures2.current?.themeMode).toBe("dark");

		utils2.unmount();
	});

	it("preserves the return shape (consumer identity stays stable)", async () => {
		const captures: { current: Record<string, unknown> | null } = {
			current: null,
		};
		await renderProbe(captures);

		const result = captures.current;
		expect(result).not.toBeNull();
		// All 9 fields of the useTheme return shape must be present.
		expect(result).toHaveProperty("themeMode");
		expect(result).toHaveProperty("themePreset");
		expect(result).toHaveProperty("customTheme");
		expect(result).toHaveProperty("textSize");
		expect(result).toHaveProperty("setThemePreset");
		expect(result).toHaveProperty("setCustomTheme");
		expect(result).toHaveProperty("setTextSize");
		expect(result).toHaveProperty("handleThemeChange");
		expect(result).toHaveProperty("reloadThemeFromConfig");
	});

	it("reloadThemeFromConfig runs EXACTLY ONCE even with two consumers (initOnce guard)", async () => {
		// Mount consumer 1.
		const captures1: { current: Record<string, unknown> | null } = {
			current: null,
		};
		await renderProbe(captures1);

		// Count `get_config` calls after the first consumer mounts.
		// The singleton `ensureThemeSideEffects` runs once on the
		// first consumer's mount effect → 1 `get_config` call.
		const getConfigCallsAfterFirst = mocks.callMock.mock.calls.filter(
			(c) => c[0] === "get_config",
		).length;
		expect(getConfigCallsAfterFirst).toBe(1);

		// Mount consumer 2 (does NOT reset the store — would in a real
		// app, both consumers share the same already-initialized store).
		const { useTheme } = await import("@/hooks/useTheme");
		const captures2: { current: Record<string, unknown> | null } = {
			current: null,
		};
		function Probe2() {
			const hook = useTheme(
				mocks.callMock as unknown as Parameters<typeof useTheme>[0],
			);
			captures2.current = hook as unknown as Record<string, unknown>;
			return null as unknown as ReactNode;
		}
		const utils2 = render(<Probe2 />);

		// Allow the mount effect to fire.
		await act(async () => {
			await Promise.resolve();
		});

		// The second consumer's mount effect should NOT have triggered
		// another `get_config` call — the `themeInitStarted` flag
		// short-circuits `ensureThemeSideEffects`.
		const getConfigCallsAfterSecond = mocks.callMock.mock.calls.filter(
			(c) => c[0] === "get_config",
		).length;
		expect(getConfigCallsAfterSecond).toBe(1); // still 1, not 2

		utils2.unmount();
	});

	it("beforeunload flush listener is installed EXACTLY ONCE across two consumers", async () => {
		const addSpy = vi.spyOn(window, "addEventListener");
		addSpy.mockClear();

		// Mount consumer 1.
		const captures1: { current: Record<string, unknown> | null } = {
			current: null,
		};
		await renderProbe(captures1);

		const beforeUnloadAfterFirst = addSpy.mock.calls.filter(
			(c) => c[0] === "beforeunload",
		).length;
		expect(beforeUnloadAfterFirst).toBe(1);

		// Mount consumer 2.
		const { useTheme } = await import("@/hooks/useTheme");
		const captures2: { current: Record<string, unknown> | null } = {
			current: null,
		};
		function Probe2() {
			const hook = useTheme(
				mocks.callMock as unknown as Parameters<typeof useTheme>[0],
			);
			captures2.current = hook as unknown as Record<string, unknown>;
			return null as unknown as ReactNode;
		}
		const utils2 = render(<Probe2 />);

		// Allow mount effects to fire.
		await act(async () => {
			await Promise.resolve();
		});

		// Still only 1 `beforeunload` listener — the `initOnce` guard
		// in `ensureThemeSideEffects` short-circuits the second
		// consumer's install.
		const beforeUnloadAfterSecond = addSpy.mock.calls.filter(
			(c) => c[0] === "beforeunload",
		).length;
		expect(beforeUnloadAfterSecond).toBe(1); // still 1, not 2

		utils2.unmount();
		addSpy.mockRestore();
	});

	it("config_changed event updates the shared store (both consumers see the new value)", async () => {
		// Mount consumer 1.
		const captures1: { current: Record<string, unknown> | null } = {
			current: null,
		};
		await renderProbe(captures1);

		// Mount consumer 2.
		const { useTheme } = await import("@/hooks/useTheme");
		const captures2: { current: Record<string, unknown> | null } = {
			current: null,
		};
		function Probe2() {
			const hook = useTheme(
				mocks.callMock as unknown as Parameters<typeof useTheme>[0],
			);
			captures2.current = hook as unknown as Record<string, unknown>;
			return null as unknown as ReactNode;
		}
		const utils2 = render(<Probe2 />);

		// Both consumers see the initial textSize (14 — default).
		expect(captures1.current?.textSize).toBe(14);
		expect(captures2.current?.textSize).toBe(14);

		// Simulate a `config_changed` push from the backend.
		const handler = getConfigChangedHandler();
		expect(handler).toBeTruthy();

		act(() => {
			handler?.({ text_size: 18 });
		});

		// Both consumers should now see textSize = 18 — the singleton
		// handler updated the shared store, both subscribers re-rendered.
		expect(captures1.current?.textSize).toBe(18);
		expect(captures2.current?.textSize).toBe(18);

		utils2.unmount();
	});

	it("handleThemeChange schedules a single debounced set_config (consolidated across rapid calls)", async () => {
		// Use fake timers to control the 300ms debounce without waiting.
		vi.useFakeTimers();

		const captures: { current: Record<string, unknown> | null } = {
			current: null,
		};
		await renderProbe(captures);

		// Reset call mock after mount (mount triggers get_config).
		mocks.callMock.mockClear();

		// Rapidly toggle theme mode 3 times — only the LAST value
		// should be persisted (debounce coalesces).
		const handleThemeChange = captures.current?.handleThemeChange as (
			mode: string,
		) => Promise<void>;
		await act(async () => {
			await handleThemeChange("dark");
			await handleThemeChange("light");
			await handleThemeChange("system");
		});

		// No set_config yet — the debounce timer is still pending.
		const setConfigCallsBeforeDebounce = mocks.callMock.mock.calls.filter(
			(c) => c[0] === "set_config",
		).length;
		expect(setConfigCallsBeforeDebounce).toBe(0);

		// Fast-forward the 300ms debounce.
		await act(async () => {
			vi.advanceTimersByTime(300);
		});

		// Exactly ONE set_config call — the 3 rapid changes coalesced.
		const setConfigCallsAfterDebounce = mocks.callMock.mock.calls.filter(
			(c) => c[0] === "set_config",
		).length;
		expect(setConfigCallsAfterDebounce).toBe(1);

		// The persisted payload should contain the LAST value.
		const setConfigCall = mocks.callMock.mock.calls.find(
			(c) => c[0] === "set_config",
		);
		expect(setConfigCall?.[1]).toEqual({ theme_mode: "system" });

		vi.useRealTimers();
	});
});
