// BUILD-N11: vitest setup file. Runs before each test file.
// Import @testing-library/jest-dom so DOM-specific matchers like
// toBeInTheDocument, toBeChecked, toHaveAttribute are available.
import "@testing-library/jest-dom/vitest";

// SEGMENTED-CTRL-FIX: polyfill ResizeObserver for jsdom (used by
// SegmentedControl to position the animated indicator).
// jsdom doesn't implement ResizeObserver, so we provide a minimal stub.
if (typeof globalThis.ResizeObserver === "undefined") {
	class ResizeObserverStub {
		observe() {}
		unobserve() {}
		disconnect() {}
	}
	globalThis.ResizeObserver =
		ResizeObserverStub as unknown as typeof ResizeObserver;
}
