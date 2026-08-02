// BUILD-N11: vitest setup file. Runs before each test file.
// Import @testing-library/jest-dom so DOM-specific matchers like
// toBeInTheDocument, toBeChecked, toHaveAttribute are available.
import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
// Centralised per-test cleanup so individual test files no longer
// need to repeat `afterEach(() => { cleanup(); localStorage.clear(); })`
// at the top of every spec. Pre-fix, 16+ test files rolled their own
// copy of this boilerplate, which led to drift (some cleared
// localStorage, some didn't; some called cleanup, some relied on the
// framework default). Running both here guarantees a clean DOM AND a
// clean localStorage between tests, regardless of what an individual
// spec forgets to do.
//
// The guards (`typeof`, `!= null`) make this safe to import in Node
// unit-test contexts that don't have a DOM or localStorage at all —
// vitest evaluates `setupFiles` once per worker, but a config can
// mix jsdom and node environments across files in the same run.
import { afterEach } from "vitest";

afterEach(() => {
	cleanup();
	if (typeof localStorage !== "undefined") {
		localStorage.clear();
	}
});

// ── localStorage / sessionStorage ─────────────────────────────────────
// Node >=26 ships an experimental built-in webstorage `localStorage`
// global that is a getter returning `undefined` unless the process is
// started with `--localstorage-file`. Vitest's jsdom environment only
// copies jsdom's own Storage onto `globalThis` when the key is NOT
// already present on the Node global (see `getWindowKeys` in vitest),
// so on Node 26+ `localStorage` resolves to the useless built-in stub
// and any test touching it crashes with "Cannot read properties of
// undefined". Provide an in-memory Storage fallback so the suite runs
// identically on Node 24 (CI) and Node 26+ (local dev).
if (
	typeof globalThis.localStorage === "undefined" ||
	typeof globalThis.sessionStorage === "undefined"
) {
	const createMemoryStorage = (): Storage => {
		const store = new Map<string, string>();
		return {
			get length() {
				return store.size;
			},
			clear() {
				store.clear();
			},
			getItem(key: string) {
				return store.get(key) ?? null;
			},
			key(index: number) {
				return [...store.keys()][index] ?? null;
			},
			removeItem(key: string) {
				store.delete(key);
			},
			setItem(key: string, value: string) {
				store.set(key, String(value));
			},
		} as Storage;
	};
	if (typeof globalThis.localStorage === "undefined") {
		Object.defineProperty(globalThis, "localStorage", {
			value: createMemoryStorage(),
			configurable: true,
			writable: true,
		});
	}
	if (typeof globalThis.sessionStorage === "undefined") {
		Object.defineProperty(globalThis, "sessionStorage", {
			value: createMemoryStorage(),
			configurable: true,
			writable: true,
		});
	}
}

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

//collect the jsdom polyfills that previously had to be
// re-installed inside individual test files (Bubble.test.tsx,
// Bubble-keyboard-move.test.tsx, ux-components-behavior.test.tsx, etc.).
// Centralising them here means new tests get a working DOM environment
// out of the box, and the existing tests can drop their inline copies.

// ── window.matchMedia ─────────────────────────────────────────────────
// jsdom doesn't implement matchMedia. Used by useThemeSync (Bubble.tsx)
// and by any component that probes the prefers-color-scheme media query.
if (typeof window !== "undefined" && typeof window.matchMedia !== "function") {
	window.matchMedia = ((query: string) => ({
		matches: false,
		media: query,
		onchange: null,
		addListener: () => {},
		removeListener: () => {},
		addEventListener: () => {},
		removeEventListener: () => {},
		dispatchEvent: () => false,
	})) as unknown as typeof window.matchMedia;
}

// ── Element.prototype.scrollIntoView ──────────────────────────────────
// Radix Select calls scrollIntoView on the highlighted option when the
// dropdown opens; jsdom's stub throws "Not implemented". Without this
// polyfill, the Vocabulary "Category select" test crashes inside
// Radix's commitHookEffectListMount.
if (
	typeof Element !== "undefined" &&
	typeof Element.prototype.scrollIntoView !== "function"
) {
	Element.prototype.scrollIntoView = function scrollIntoView() {
		// no-op — jsdom doesn't actually scroll
	};
}

// ── IntersectionObserver ──────────────────────────────────────────────
// Used by some lazy-mount components (and by Radix primitives in
// certain configurations) to detect when an element enters the
// viewport. jsdom has no layout engine so we treat every element as
// immediately intersecting.
if (
	typeof globalThis.IntersectionObserver === "undefined" &&
	typeof window !== "undefined"
) {
	class IntersectionObserverStub {
		readonly root: Element | Document | null = null;
		readonly rootMargin: string = "0px";
		readonly thresholds: ReadonlyArray<number> = [0];
		constructor(
			private callback: IntersectionObserverCallback,
			_options?: IntersectionObserverInit,
		) {}
		observe(target: Element) {
			// Immediately report the target as intersecting so any
			// `onContentVisible` style callback fires synchronously.
			this.callback(
				[
					{
						target,
						isIntersecting: true,
						intersectionRatio: 1,
						boundingClientRect: target.getBoundingClientRect(),
						intersectionRect: target.getBoundingClientRect(),
						rootBounds: null,
						time: 0,
					},
				],
				this as unknown as IntersectionObserver,
			);
		}
		unobserve() {}
		disconnect() {}
		takeRecords() {
			return [];
		}
	}
	globalThis.IntersectionObserver =
		IntersectionObserverStub as unknown as typeof IntersectionObserver;
	if (typeof window.IntersectionObserver === "undefined") {
		window.IntersectionObserver = globalThis.IntersectionObserver;
	}
}

// ── PointerEvent ──────────────────────────────────────────────────────
// jsdom's PointerEvent is incomplete in older versions and entirely
// absent in others; some Radix primitives (Dialog, DropdownMenu) call
// `new PointerEvent(...)` during focus management. Provide a minimal
// subclass that inherits from MouseEvent (which jsdom DOES implement)
// and exposes `pointerId` / `pointerType` so consumer reads don't
// throw.
if (
	typeof window !== "undefined" &&
	typeof window.PointerEvent === "undefined"
) {
	class PointerEventStub extends MouseEvent {
		pointerId: number;
		pointerType: string;
		width: number;
		height: number;
		pressure: number;
		tiltX: number;
		tiltY: number;
		isPrimary: boolean;
		constructor(type: string, init: PointerEventInit = {}) {
			super(type, init);
			this.pointerId = init.pointerId ?? 0;
			this.pointerType = init.pointerType ?? "";
			this.width = init.width ?? 0;
			this.height = init.height ?? 0;
			this.pressure = init.pressure ?? 0;
			this.tiltX = init.tiltX ?? 0;
			this.tiltY = init.tiltY ?? 0;
			this.isPrimary = init.isPrimary ?? false;
		}
	}
	window.PointerEvent = PointerEventStub as unknown as typeof PointerEvent;
	if (typeof globalThis.PointerEvent === "undefined") {
		globalThis.PointerEvent = window.PointerEvent;
	}
}

// ── window.bubble default stub ────────────────────────────────────────
// Bubble.tsx subscribes to `window.bubble.onLevel` etc. at module-load
// time. Tests that DON'T care about the bubble still indirectly import
// components that touch `window.bubble`, so a no-op default prevents
// "Cannot read properties of undefined" crashes during render.
//
// Tests that DO assert on bubble behaviour (e.g. Bubble-keyboard-move)
// install their own mock in `beforeEach` via `installBubbleBridgeMock`
// (see `__tests__/helpers/mocks.ts`), which OVERWRITES this default.
if (typeof window !== "undefined") {
	const w = window as unknown as { bubble?: unknown };
	if (!w.bubble) {
		w.bubble = {
			onLevel: () => () => {},
			onShow: () => () => {},
			onHide: () => () => {},
			onSetState: () => () => {},
			onDraggable: () => () => {},
			signalReady: () => {},
			hideComplete: () => {},
			resizeTo: () => {},
			moveBy: () => {},
		};
	}
}
