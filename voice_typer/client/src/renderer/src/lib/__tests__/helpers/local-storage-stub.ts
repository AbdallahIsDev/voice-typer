/**
 * Shared test helper: build a controlled in-memory Storage stub and
 * install it as the global `localStorage` via `vi.stubGlobal`.
 *
 * Spying on the jsdom `localStorage` INSTANCE does not reliably
 * intercept module calls in the CI (Node 24) jsdom environment
 * ("expected setItem to be called" fails there even though the same
 * spy works under Node 26's fallback storage). Modules that read/write
 * the bare `localStorage` global at call time (sound-manager,
 * accessibility-manager, …) are intercepted environment-independently
 * by replacing `globalThis.localStorage` entirely.
 *
 * Always pair with `vi.unstubAllGlobals()` in the test's `finally`.
 */
import { vi } from "vitest";

export const stubGlobalLocalStorage = (overrides: {
	setItem?: (key: string, value: string) => void;
	getItem?: (key: string) => string | null;
}): void => {
	vi.stubGlobal("localStorage", {
		get length() {
			return 0;
		},
		clear: vi.fn(),
		getItem: overrides.getItem ?? vi.fn(() => null),
		key: vi.fn(() => null),
		removeItem: vi.fn(),
		setItem: overrides.setItem ?? vi.fn(),
	});
};
