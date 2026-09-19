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
