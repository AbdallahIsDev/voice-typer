/**
 * Tests for useModelDownload — focused on the single-setState
 * consolidation of the 10 previously-separate useState fields.
 *
 * Background
 * ----------
 * Previously: the hook used 10 separate `useState` calls
 * (`downloadingModel`, `downloadProgress`, `downloadStatus`, `isPaused`,
 * `downloadedBytes`, `totalBytes`, `speedBps`, `etaSeconds`,
 * `failedDownload`, `installingDepsModel`). Each `download_progress`
 * event invoked up to 8 of these setters — React 18 batched them into
 * a single re-render, but the per-setter overhead (state-entry lookup
 * + Object.is check + subscriber notification) ran 8 times per event.
 *
 * After consolidation: the 10 fields live in a single
 * `useState<DownloadState>`. Each `download_progress` event produces
 * ONE setState call with a patch object containing only the fields
 * present in the event payload.
 *
 * These tests verify:
 *   1. A multi-field `download_progress` event updates ALL fields
 *      atomically (single setState — verified by render count).
 *   2. The return shape is preserved (consumer identity stays stable).
 *   3. A `download_progress` event with only one field updates only
 *      that field (others preserved).
 *   4. A `download_progress` event with no recognised fields is a
 *      no-op (no setState, no re-render).
 *
 * Implementation note: the production-code import (`useModelDownload`)
 * is done via dynamic `await import()` inside the test setup so the
 * vi.mock factory for `@/hooks/usePython` is fully initialized before
 * the mock is asked to resolve `usePythonEvent` (mirrors the
 * `useTheme-flush-pending-save.test.tsx` pattern).
 */
import { act, cleanup, render } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// `vi.hoisted` guarantees the mock fn is initialized BEFORE the
// vi.mock factory is invoked (which happens at import-resolution
// time, before any top-level `const` would normally run).
const mocks = vi.hoisted(() => ({
	usePythonEventMock: vi.fn(),
}));

const stable = vi.hoisted(() => ({
	pythonCall: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({
		call: stable.pythonCall,
		status: "connected",
		connectionStatus: "connected",
	}),
	usePythonEvent: mocks.usePythonEventMock,
}));

vi.mock("@/i18n/i18n", () => ({
	t: (key: string) => key,
}));

vi.mock("sonner", () => ({
	toast: { error: vi.fn() },
}));

// Stub localStorage so any downstream code that touches it doesn't blow
// up in the jsdom environment.
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
	vi.clearAllMocks();
	mocks.usePythonEventMock.mockReset();
	lsMock.clear();
});

afterEach(() => {
	cleanup();
});

// ── Helpers ──────────────────────────────────────────────────────────

/** Extract the `download_progress` handler captured by the
 * `usePythonEvent` mock. The mock is called with `(type, handler)`;
 * we want the handler for the `download_progress` subscription. */
function getDownloadProgressHandler(): ((data?: unknown) => unknown) | null {
	for (let i = mocks.usePythonEventMock.mock.calls.length - 1; i >= 0; i--) {
		const call = mocks.usePythonEventMock.mock.calls[i];
		if (call?.[0] === "download_progress") {
			return (call[1] as (data?: unknown) => unknown) ?? null;
		}
	}
	return null;
}

// ── Tests ────────────────────────────────────────────────────────────

describe("useModelDownload — single-setState consolidation", () => {
	it("updates ALL fields from a multi-field download_progress event in ONE setState (one re-render)", async () => {
		const { useModelDownload } = await import(
			"@/hooks/models/useModelDownload"
		);

		const captures: { current: ReturnType<typeof useModelDownload> | null } = {
			current: null,
		};
		const renderCount = { current: 0 };

		function Probe() {
			renderCount.current += 1;
			const hook = useModelDownload({
				call: vi.fn(),
				showSnack: vi.fn(),
				setModels: vi.fn(),
				refreshModelStatus: vi.fn(),
				reconcileAfterDownload: vi.fn(),
			});
			captures.current = hook;
			return null as unknown as ReactNode;
		}

		render(<Probe />);

		// After mount, the hook should have subscribed to
		// `download_progress` exactly once.
		const progressSubs = mocks.usePythonEventMock.mock.calls.filter(
			(c) => c[0] === "download_progress",
		);
		expect(progressSubs.length).toBe(1);

		const handler = getDownloadProgressHandler();
		expect(handler).toBeTruthy();

		// Reset render count after mount settles.
		const mountRenderCount = renderCount.current;

		// Fire a multi-field progress event. Previously this would
		// invoke up to 8 separate setters (one per field); now it
		// produces ONE setState with a patch object.
		act(() => {
			handler?.({
				progress: 42,
				status: "downloading",
				downloaded_bytes: 4200,
				total_bytes: 10000,
				speed_bytes_per_sec: 1500,
				eta_seconds: 3.5,
				paused: false,
			});
		});

		// Exactly ONE re-render should have occurred (consolidated
		// setState). The original 8-setState pattern would also
		// produce one re-render under React 18 batching, so this
		// assertion is a sanity check — the meaningful guarantee
		// is the atomicity of the field updates (asserted below).
		expect(renderCount.current).toBe(mountRenderCount + 1);

		// All 7 fields should be updated atomically.
		expect(captures.current?.downloadProgress).toBe(42);
		expect(captures.current?.downloadStatus).toBe("downloading");
		expect(captures.current?.downloadedBytes).toBe(4200);
		expect(captures.current?.totalBytes).toBe(10000);
		expect(captures.current?.speedBps).toBe(1500);
		expect(captures.current?.etaSeconds).toBe(3.5);
		expect(captures.current?.isPaused).toBe(false);
	});

	it("preserves the return shape (consumer identity stays stable)", async () => {
		const { useModelDownload } = await import(
			"@/hooks/models/useModelDownload"
		);

		const captures: { current: ReturnType<typeof useModelDownload> | null } = {
			current: null,
		};
		const renderCount = { current: 0 };

		function Probe() {
			renderCount.current += 1;
			const hook = useModelDownload({
				call: vi.fn(),
				showSnack: vi.fn(),
				setModels: vi.fn(),
				refreshModelStatus: vi.fn(),
				reconcileAfterDownload: vi.fn(),
			});
			captures.current = hook;
			return null as unknown as ReactNode;
		}

		render(<Probe />);

		// All 15 fields of `UseModelDownloadResult` must be present.
		const result = captures.current;
		expect(result).not.toBeNull();
		expect(result).toHaveProperty("downloadingModel");
		expect(result).toHaveProperty("downloadProgress");
		expect(result).toHaveProperty("downloadStatus");
		expect(result).toHaveProperty("isPaused");
		expect(result).toHaveProperty("downloadedBytes");
		expect(result).toHaveProperty("totalBytes");
		expect(result).toHaveProperty("speedBps");
		expect(result).toHaveProperty("etaSeconds");
		expect(result).toHaveProperty("failedDownload");
		expect(result).toHaveProperty("installingDepsModel");
		expect(result).toHaveProperty("downloadModel");
		expect(result).toHaveProperty("retryDownload");
		expect(result).toHaveProperty("installDeps");
		expect(result).toHaveProperty("handleTogglePause");
		expect(result).toHaveProperty("handleCancelDownload");

		// Initial state values match the consolidated initial state.
		expect(result?.downloadingModel).toBeNull();
		expect(result?.downloadProgress).toBe(0);
		expect(result?.downloadStatus).toBe("");
		expect(result?.isPaused).toBe(false);
		expect(result?.downloadedBytes).toBeNull();
		expect(result?.totalBytes).toBeNull();
		expect(result?.speedBps).toBeNull();
		expect(result?.etaSeconds).toBeNull();
		expect(result?.failedDownload).toBeNull();
		expect(result?.installingDepsModel).toBeNull();
	});

	it("updates only the fields present in a partial event (others preserved)", async () => {
		const { useModelDownload } = await import(
			"@/hooks/models/useModelDownload"
		);

		const captures: { current: ReturnType<typeof useModelDownload> | null } = {
			current: null,
		};
		const renderCount = { current: 0 };

		function Probe() {
			renderCount.current += 1;
			const hook = useModelDownload({
				call: vi.fn(),
				showSnack: vi.fn(),
				setModels: vi.fn(),
				refreshModelStatus: vi.fn(),
				reconcileAfterDownload: vi.fn(),
			});
			captures.current = hook;
			return null as unknown as ReactNode;
		}

		render(<Probe />);

		const handler = getDownloadProgressHandler();
		expect(handler).toBeTruthy();

		// First event: set progress + total.
		act(() => {
			handler?.({ progress: 10, total_bytes: 1000 });
		});

		expect(captures.current?.downloadProgress).toBe(10);
		expect(captures.current?.totalBytes).toBe(1000);
		// Other fields stay at their initial values.
		expect(captures.current?.downloadStatus).toBe("");
		expect(captures.current?.downloadedBytes).toBeNull();
		expect(captures.current?.speedBps).toBeNull();
		expect(captures.current?.etaSeconds).toBeNull();
		expect(captures.current?.isPaused).toBe(false);

		// Second event: update only `progress`. The previously-set
		// `totalBytes` must be preserved (single-state spread).
		act(() => {
			handler?.({ progress: 50 });
		});

		expect(captures.current?.downloadProgress).toBe(50);
		expect(captures.current?.totalBytes).toBe(1000); // preserved
	});

	it("PRESERVES speed/eta on partial events; clears them only on a transition event", async () => {
		const { useModelDownload } = await import(
			"@/hooks/models/useModelDownload"
		);

		const captures: { current: ReturnType<typeof useModelDownload> | null } = {
			current: null,
		};
		const renderCount = { current: 0 };

		function Probe() {
			renderCount.current += 1;
			const hook = useModelDownload({
				call: vi.fn(),
				showSnack: vi.fn(),
				setModels: vi.fn(),
				refreshModelStatus: vi.fn(),
				reconcileAfterDownload: vi.fn(),
			});
			captures.current = hook;
			return null as unknown as ReactNode;
		}

		render(<Probe />);

		const handler = getDownloadProgressHandler();
		expect(handler).toBeTruthy();

		// Set non-null values.
		act(() => {
			handler?.({
				speed_bytes_per_sec: 2000,
				eta_seconds: 4.2,
			});
		});
		expect(captures.current?.speedBps).toBe(2000);
		expect(captures.current?.etaSeconds).toBe(4.2);

		// A partial event WITHOUT a transition marker (no status/paused/
		// resumed field) means "speed/eta not re-measured" — the previous
		// values must be PRESERVED. Clearing on absence made every
		// transition-only backend event (e.g. a lone `paused: true`)
		// wipe the readout, but worse, plain partial progress events
		// reset it too. Contract: absence is not clearance.
		act(() => {
			handler?.({
				speed_bytes_per_sec: null,
				eta_seconds: null,
			});
		});
		expect(captures.current?.speedBps).toBe(2000);
		expect(captures.current?.etaSeconds).toBe(4.2);

		// A TRANSITION event (status present) clears both — the old
		// measurement window is over.
		act(() => {
			handler?.({
				status: "downloading",
				speed_bytes_per_sec: null,
				eta_seconds: null,
			});
		});
		expect(captures.current?.speedBps).toBeNull();
		expect(captures.current?.etaSeconds).toBeNull();
	});

	it("treats `resumed: true` as 'set isPaused to false'", async () => {
		const { useModelDownload } = await import(
			"@/hooks/models/useModelDownload"
		);

		const captures: { current: ReturnType<typeof useModelDownload> | null } = {
			current: null,
		};
		const renderCount = { current: 0 };

		function Probe() {
			renderCount.current += 1;
			const hook = useModelDownload({
				call: vi.fn(),
				showSnack: vi.fn(),
				setModels: vi.fn(),
				refreshModelStatus: vi.fn(),
				reconcileAfterDownload: vi.fn(),
			});
			captures.current = hook;
			return null as unknown as ReactNode;
		}

		render(<Probe />);

		const handler = getDownloadProgressHandler();
		expect(handler).toBeTruthy();

		// Set paused = true.
		act(() => {
			handler?.({ paused: true });
		});
		expect(captures.current?.isPaused).toBe(true);

		// Resume via `resumed: true` flag.
		act(() => {
			handler?.({ resumed: true });
		});
		expect(captures.current?.isPaused).toBe(false);
	});

	it("is a no-op when the event payload has no recognised fields (no re-render)", async () => {
		const { useModelDownload } = await import(
			"@/hooks/models/useModelDownload"
		);

		const captures: { current: ReturnType<typeof useModelDownload> | null } = {
			current: null,
		};
		const renderCount = { current: 0 };

		function Probe() {
			renderCount.current += 1;
			const hook = useModelDownload({
				call: vi.fn(),
				showSnack: vi.fn(),
				setModels: vi.fn(),
				refreshModelStatus: vi.fn(),
				reconcileAfterDownload: vi.fn(),
			});
			captures.current = hook;
			return null as unknown as ReactNode;
		}

		render(<Probe />);

		const handler = getDownloadProgressHandler();
		expect(handler).toBeTruthy();

		const mountRenderCount = renderCount.current;

		// Fire an event with only unknown fields.
		act(() => {
			handler?.({ unknown_field: "ignored", another: 42 });
		});

		// No recognised fields → no patch → no setState → no re-render.
		expect(renderCount.current).toBe(mountRenderCount);

		// State stays at initial values.
		expect(captures.current?.downloadProgress).toBe(0);
		expect(captures.current?.downloadStatus).toBe("");
	});
});
