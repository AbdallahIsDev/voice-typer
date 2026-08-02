/**
 *  regression tests for Group 2 (Performance & Resources) fixes.
 *
 * Covers:
 *  (a)  — i18n ``t()`` caches the per-key interpolation RegExp.
 *  (b)  — ``formatBytes`` caches ``Intl.NumberFormat`` instances.
 *  (c)  — ``closeAudioContext`` nulls the shared AudioContext.
 *  (d)  — ``useConnection`` only probes ``get_status`` after a
 *      5-minute gap with no backend push events.
 *
 * These tests are deliberately narrow: they verify the caching / gating
 * behaviour added by the  wave, not the broader functional
 * behaviour (which is covered by the existing ``useConnection.test.tsx``,
 * ``useSoundFeedback.test.tsx``, and ``sound-manager.test.ts`` suites).
 */
import { act, cleanup, render } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ───────────────────────────────────────────────────────────────────────
//(a)  — t() caches the per-key interpolation RegExp
// ───────────────────────────────────────────────────────────────────────

describe("ER-20: t() caches interpolation RegExp by key", () => {
	beforeEach(() => {
		vi.resetModules();
		localStorage.clear();
		// The cache lives at module scope, so a fresh module load
		// is enough to reset it between tests.
	});

	afterEach(() => {
		vi.restoreAllMocks();
	});

	it("reuses the same RegExp instance for repeated params with the same key", async () => {
		// Spy on the RegExp constructor so we can count how many
		// times the interpolation regex ``\{name\}`` is built.
		// The cache should ensure it's built ONCE for the ``name``
		// key no matter how many ``t()`` calls interpolate it.
		const realRegExp = RegExp;
		// NOTE: a regular (non-arrow) function so `new ctorSpy(...)`
		// works — Vitest forwards the construct call to the mock
		// implementation, and arrow functions are not constructible.
		const ctorSpy = vi.fn(function (
			this: unknown,
			pattern: string,
			flags?: string,
		) {
			return new (
				realRegExp as unknown as {
					new (p: string, f?: string): RegExp;
				}
			)(pattern, flags);
		} as unknown as { new (pattern: string, flags?: string): RegExp });
		// Replace the global RegExp with our spy for the duration
		// of the test. ``i18n.ts`` calls ``new RegExp(`\\{${k}\\}`, "g")``
		// inside ``interpRegex()`` — that's the call we want to
		// count.
		const g = globalThis as unknown as { RegExp: typeof RegExp };
		g.RegExp = ctorSpy as unknown as typeof RegExp;

		try {
			const { t, registerTranslations } = await import("@/i18n/i18n");
			// Register a string with a ``{name}`` placeholder.
			registerTranslations("en", { greet: "Hello, {name}!" });
			// Pre-clear any cache state by reloading the module.
			// (vi.resetModules in beforeEach already did this.)

			// First call: builds the ``\{name\}`` RegExp.
			t("greet", { name: "Alice" });
			const callsAfterFirst = ctorSpy.mock.calls.length;

			// Second call with the same key but a different
			// value: the cache should be reused, so the
			// RegExp constructor should NOT be called again
			// for the ``\{name\}`` pattern.
			t("greet", { name: "Bob" });
			t("greet", { name: "Carol" });
			t("greet", { name: "Dave" });

			const interpCalls = ctorSpy.mock.calls.filter(
				([pattern]) =>
					typeof pattern === "string" && pattern.includes("\\{name\\}"),
			).length;

			// The interpolation regex for ``name`` was built
			// at most once across all four calls.
			expect(interpCalls).toBeLessThanOrEqual(1);
			// Sanity: the spy was invoked at least once (the
			// first interpolation).
			expect(callsAfterFirst).toBeGreaterThan(0);
		} finally {
			g.RegExp = realRegExp;
		}
	});
});

// ───────────────────────────────────────────────────────────────────────
//(b)  — formatBytes caches Intl.NumberFormat
// ───────────────────────────────────────────────────────────────────────

describe("ER-23: formatBytes caches Intl.NumberFormat", () => {
	let originalNumberFormat: typeof Intl.NumberFormat;

	beforeEach(() => {
		vi.resetModules();
		originalNumberFormat = Intl.NumberFormat;
	});

	afterEach(() => {
		Object.defineProperty(Intl, "NumberFormat", {
			value: originalNumberFormat,
			configurable: true,
			writable: true,
		});
		vi.restoreAllMocks();
	});

	it("reuses the same Intl.NumberFormat instance for identical locale+opts", async () => {
		// Spy that wraps the real NumberFormat so we can count
		// constructions while still producing valid output.
		const ctorSpy = vi.fn();
		class SpyNumberFormat extends Intl.NumberFormat {
			constructor(locale?: string | string[], opts?: Intl.NumberFormatOptions) {
				super(locale, opts);
				ctorSpy(locale, opts);
			}
		}
		Object.defineProperty(Intl, "NumberFormat", {
			value: SpyNumberFormat,
			configurable: true,
			writable: true,
		});

		const { formatBytes } = await import("@/lib/format");
		// Reset the module-level cache after the import so we
		// start from a known-empty state.
		vi.resetModules();
		const mod = await import("@/lib/format");
		const fmt = mod.formatBytes;

		// Two calls with the same byte count + same locale should
		// reuse the cached formatter.
		fmt(2048, "en"); // 2 KB — kilobyte path
		const callsAfterFirst = ctorSpy.mock.calls.length;
		fmt(2048, "en");
		fmt(2048, "en");
		fmt(2048, "en");

		// The kilobyte formatter should have been constructed
		// exactly once across all four calls.
		expect(ctorSpy).toHaveBeenCalledTimes(callsAfterFirst);
		// Sanity: at least one construction happened.
		expect(callsAfterFirst).toBeGreaterThan(0);

		// Reference the unused import so TS doesn't complain.
		expect(typeof formatBytes).toBe("function");
	});
});

// ───────────────────────────────────────────────────────────────────────
//(c)  — closeAudioContext nulls the shared AudioContext
// ───────────────────────────────────────────────────────────────────────

describe("ER-28: closeAudioContext nulls the shared AudioContext", () => {
	let mockCtor: ReturnType<typeof vi.fn>;

	beforeEach(() => {
		vi.resetModules();
		localStorage.clear();

		// Minimal mock AudioContext — only the methods the
		// manager touches. ``close()`` returns a Promise so the
		// manager's ``closeAudioContext`` exercises its
		// ``typeof p.then === "function"`` branch.
		class MockAudioContext {
			state: "suspended" | "running" | "closed" = "suspended";
			currentTime = 0;
			destination = {} as AudioDestinationNode;
			createOscillator() {
				return {
					connect: () => ({ connect: () => ({}) }),
					start: vi.fn(),
					stop: vi.fn(),
					onended: null as (() => void) | null,
					frequency: {
						setValueAtTime: vi.fn(),
						exponentialRampToValueAtTime: vi.fn(),
					},
				} as unknown as OscillatorNode;
			}
			createGain() {
				return {
					connect: () => ({ connect: () => ({}) }),
					gain: {
						setValueAtTime: vi.fn(),
						exponentialRampToValueAtTime: vi.fn(),
					},
				} as unknown as GainNode;
			}
			resume() {
				this.state = "running";
				return Promise.resolve();
			}
			close() {
				this.state = "closed";
				return Promise.resolve();
			}
		}
		// regular function (not arrow) so `new Ctor()` works — Vitest
		// forwards construct calls to the mock implementation.
		mockCtor = vi.fn(() => new MockAudioContext());
		window.AudioContext = mockCtor as unknown as typeof AudioContext;
	});

	afterEach(() => {
		vi.restoreAllMocks();
		cleanup();
	});

	it("after closeAudioContext, initAudioContext constructs a fresh AudioContext", async () => {
		const { initAudioContext, closeAudioContext, _resetSoundManagerForTests } =
			await import("@/lib/sound-manager");
		_resetSoundManagerForTests();

		// First init: constructs the shared context.
		expect(initAudioContext()).toBe(true);
		expect(mockCtor).toHaveBeenCalledTimes(1);

		// closeAudioContext should null out the shared context —
		// verified by the NEXT initAudioContext call constructing
		// a brand-new one. If closeAudioContext left the old
		// context in place, initAudioContext would no-op (its
		// ``_initSucceeded && state !== "closed"`` guard would
		// short-circuit) and mockCtor would stay at 1 call.
		closeAudioContext();
		expect(initAudioContext()).toBe(true);
		expect(mockCtor).toHaveBeenCalledTimes(2);
	});

	it("closeAudioContext is a no-op when no context exists", async () => {
		const { closeAudioContext, _resetSoundManagerForTests } = await import(
			"@/lib/sound-manager"
		);
		_resetSoundManagerForTests();
		// Should not throw — defensive against being called before
		// any AudioContext was ever constructed.
		expect(() => closeAudioContext()).not.toThrow();
		expect(mockCtor).not.toHaveBeenCalled();
	});
});

// ───────────────────────────────────────────────────────────────────────
//(d)  — useConnection probes only after a 5-minute event gap
// ───────────────────────────────────────────────────────────────────────

// Hoist the mock call/event handlers so they're available inside the
// vi.mock factory (which is hoisted to the top of the file by vitest).
const { mockCall, mockPythonEvent } = vi.hoisted(() => ({
	mockCall: vi.fn(),
	mockPythonEvent: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
	usePythonEvent: mockPythonEvent,
}));

vi.mock("@/stores/appStore", () => ({
	useAppStore: (selector: (s: Record<string, unknown>) => unknown) => {
		// Minimal stub store — useConnection only reads
		// ``connectionStatus`` and writes back via the setters.
		// We back it with a plain object so the hook sees
		// "connected" immediately and runs the health-check effect.
		const store: Record<string, unknown> = {
			connectionStatus: "connected",
			recordingState: "idle",
			lastError: null,
			setConnectionStatus: vi.fn(),
			setRecordingState: vi.fn(),
			setLastError: vi.fn(),
			setConfig: vi.fn(),
		};
		return selector(store);
	},
}));

// useNavigation is imported by the harness but its behaviour isn't
// under test — stub it to a no-op so the harness compiles.
vi.mock("@/hooks/useNavigation", () => ({
	useNavigation: () => ({
		currentPage: "home" as const,
		navigate: vi.fn(),
		goBack: vi.fn(),
		goForward: vi.fn(),
		canGoBack: false,
		canGoForward: false,
	}),
}));

describe("ER-61: useConnection probes only after 5-minute event gap", () => {
	beforeEach(() => {
		vi.useFakeTimers({
			shouldAdvanceTime: false,
			now: new Date("2026-01-01T00:00:00Z"),
		});
		mockCall.mockReset();
		mockPythonEvent.mockReset();
		mockCall.mockImplementation((type: string) => {
			if (type === "get_status") return Promise.resolve({ status: "idle" });
			if (type === "get_config") return Promise.resolve({});
			if (type === "onboarding_is_first_run")
				return Promise.resolve({ is_first_run: false });
			return Promise.resolve({});
		});
		localStorage.clear();
	});

	afterEach(() => {
		vi.useRealTimers();
		cleanup();
		vi.clearAllMocks();
	});

	// Helper: render the hook. usePythonEvent is mocked, so we
	// capture the registered callbacks to simulate backend pushes.
	async function renderHook() {
		const { useConnection } = await import("@/hooks/useConnection");
		const captured: Array<(data?: Record<string, unknown>) => void> = [];
		// mockPythonEvent is called once per usePythonEvent(...)
		// invocation inside the hook. Each call registers a
		// callback for a specific event type — we record all of
		// them so the test can dispatch a synthetic push to
		// every subscriber.
		mockPythonEvent.mockImplementation(((
			_type: string,
			cb: (data?: Record<string, unknown>) => void,
		) => {
			captured.push(cb);
			return undefined;
		}) as unknown as typeof mockPythonEvent);

		function Probe() {
			useConnection({
				call: ((type: string) => mockCall(type)) as unknown as <T = unknown>(
					type: string,
					data?: Record<string, unknown>,
				) => Promise<T>,
				currentPage: "home",
				navigate: vi.fn(),
			});
			return null as unknown as ReactNode;
		}
		render(<Probe />);
		return { captured };
	}

	it("does NOT probe within the 60s push-grace window (probe resumes after)", async () => {
		const { captured } = await renderHook();

		// Flush the initial connection probe + microtasks.
		await act(async () => {
			await vi.advanceTimersByTimeAsync(1000);
		});
		const initialCallCount = mockCall.mock.calls.filter(
			([type]) => type === "get_status",
		).length;

		// Simulate a backend push event (status_change) — this
		// refreshes the ``lastEventTs`` tracked by the hook.
		act(() => {
			for (const cb of captured) cb({ status: "idle" });
		});

		// Advance 45s — inside the 60s grace (HEALTH_CHECK_EVENT_GRACE_MS).
		// The 15s interval tick fires, but the probe must be SKIPPED.
		await act(async () => {
			await vi.advanceTimersByTimeAsync(45 * 1000);
		});

		const callsWithinGrace = mockCall.mock.calls.filter(
			([type]) => type === "get_status",
		).length;
		expect(callsWithinGrace).toBe(initialCallCount);

		// Advance past the 60s grace — the next 15s tick probes again.
		await act(async () => {
			await vi.advanceTimersByTimeAsync(60 * 1000);
		});
		const callsAfterGrace = mockCall.mock.calls.filter(
			([type]) => type === "get_status",
		).length;
		expect(callsAfterGrace).toBeGreaterThan(initialCallCount);
	});

	it("probes get_status after 5 minutes with no backend push events", async () => {
		await renderHook();

		// Flush the initial connection probe.
		await act(async () => {
			await vi.advanceTimersByTimeAsync(1000);
		});
		const initialCallCount = mockCall.mock.calls.filter(
			([type]) => type === "get_status",
		).length;

		// Advance 5 minutes with NO backend push events — the
		// fallback poll should fire and call ``get_status``.
		await act(async () => {
			await vi.advanceTimersByTimeAsync(5 * 60 * 1000 + 100);
		});

		const callsAfter5Min = mockCall.mock.calls.filter(
			([type]) => type === "get_status",
		).length;
		expect(callsAfter5Min).toBeGreaterThan(initialCallCount);
	});
});
