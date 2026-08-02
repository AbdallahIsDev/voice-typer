// @vitest-environment node
/**
 * Unit tests for `src/main/power.ts` registerPowerMonitorHandlers.
 *
 * Verifies that `registerPowerMonitorHandlers()`:
 *   • subscribes to all three `powerMonitor` events: `suspend`,
 *     `resume`, `on-battery` (the finding explicitly named these
 *     three).
 *   • is idempotent — calling N times still registers exactly one
 *     listener per event (no listener stacking).
 *   • wires `suspend` → `stopPython()`, `resume` → `startPython()`
 *     (the Python lifecycle hooks).
 *
 * C-DATA-1: powerMonitor is a local OS event (no network) — these
 * tests do NOT touch the network and do NOT require the Python
 * backend to be running. `startPython` / `stopPython` are mocked.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

// Vitest 4 hoists `vi.mock` factories to the top of the file, ABOVE
// any `const` declarations. To make the spy references available
// inside the hoisted factory, declare them with `vi.hoisted` (also
// hoisted, runs before any imports). This is the canonical vitest 4
// pattern — same as `bootstrap-error-handler-fixes.test.ts`.
const mocks = vi.hoisted(() => {
	// Capture every `.on(event, handler)` call on the mocked
	// powerMonitor so the tests can assert which events were
	// subscribed + invoke the handlers directly to verify the
	// lifecycle wiring.
	const powerMonitorOnCalls: Array<{ event: string; handler: () => void }> = [];
	const mockPowerMonitorOn = vi.fn((event: string, handler: () => void) => {
		powerMonitorOnCalls.push({ event, handler });
	});
	return {
		powerMonitorOnCalls,
		mockPowerMonitorOn,
		stopPythonMock: vi.fn(),
		startPythonMock: vi.fn(),
	};
});

vi.mock("electron", () => ({
	powerMonitor: {
		on: mocks.mockPowerMonitorOn,
	},
}));

vi.mock("../logging", () => ({
	log: {
		info: vi.fn(),
		warn: vi.fn(),
		error: vi.fn(),
	},
}));

vi.mock("../python", () => ({
	stopPython: mocks.stopPythonMock,
	startPython: mocks.startPythonMock,
}));

import {
	_powerMonitorHandlersRegisteredForTest,
	_resetPowerMonitorHandlersForTest,
	registerPowerMonitorHandlers,
} from "../power";

describe("power.ts registerPowerMonitorHandlers", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		mocks.powerMonitorOnCalls.length = 0;
		_resetPowerMonitorHandlersForTest();
	});

	it("subscribes to suspend, resume, and on-battery on powerMonitor", () => {
		registerPowerMonitorHandlers();

		const events = mocks.powerMonitorOnCalls.map((c) => c.event);
		expect(events).toContain("suspend");
		expect(events).toContain("resume");
		expect(events).toContain("on-battery");
		// exactly 3 listeners — no extras
		expect(mocks.powerMonitorOnCalls.length).toBe(3);
	});

	it("is idempotent — calling N times registers each listener exactly once", () => {
		for (let i = 0; i < 5; i++) {
			registerPowerMonitorHandlers();
		}
		expect(mocks.powerMonitorOnCalls.length).toBe(3);
		expect(_powerMonitorHandlersRegisteredForTest()).toBe(true);
	});

	it("suspend handler calls stopPython", () => {
		registerPowerMonitorHandlers();
		const suspendCall = mocks.powerMonitorOnCalls.find(
			(c) => c.event === "suspend",
		);
		expect(suspendCall).toBeDefined();

		suspendCall?.handler();
		expect(mocks.stopPythonMock).toHaveBeenCalledTimes(1);
		// suspend must NOT trigger a startPython
		expect(mocks.startPythonMock).not.toHaveBeenCalled();
	});

	it("resume handler calls startPython", () => {
		registerPowerMonitorHandlers();
		const resumeCall = mocks.powerMonitorOnCalls.find(
			(c) => c.event === "resume",
		);
		expect(resumeCall).toBeDefined();

		resumeCall?.handler();
		expect(mocks.startPythonMock).toHaveBeenCalledTimes(1);
		// resume must NOT trigger a stopPython
		expect(mocks.stopPythonMock).not.toHaveBeenCalled();
	});

	it("on-battery handler does NOT call stopPython or startPython (logs only)", () => {
		registerPowerMonitorHandlers();
		const onBatteryCall = mocks.powerMonitorOnCalls.find(
			(c) => c.event === "on-battery",
		);
		expect(onBatteryCall).toBeDefined();

		onBatteryCall?.handler();
		// on-battery is a best-effort log-only transition — must
		// NOT tear down or spawn the backend (the backend's
		// prewarm scheduler handles its own battery backoff).
		expect(mocks.stopPythonMock).not.toHaveBeenCalled();
		expect(mocks.startPythonMock).not.toHaveBeenCalled();
	});

	it("suspend handler swallows stopPython exceptions (Python not running)", () => {
		// Simulate the "Python process may not be running" case:
		// stopPython throws. The handler must NOT re-throw —
		// otherwise a throw inside the powerMonitor listener
		// would crash the main process.
		mocks.stopPythonMock.mockImplementationOnce(() => {
			throw new Error("python not running");
		});

		registerPowerMonitorHandlers();
		const suspendCall = mocks.powerMonitorOnCalls.find(
			(c) => c.event === "suspend",
		);

		expect(() => suspendCall?.handler()).not.toThrow();
		expect(mocks.stopPythonMock).toHaveBeenCalledTimes(1);
	});

	it("resume handler swallows startPython exceptions", () => {
		mocks.startPythonMock.mockImplementationOnce(() => {
			throw new Error("python binary missing");
		});

		registerPowerMonitorHandlers();
		const resumeCall = mocks.powerMonitorOnCalls.find(
			(c) => c.event === "resume",
		);

		expect(() => resumeCall?.handler()).not.toThrow();
		expect(mocks.startPythonMock).toHaveBeenCalledTimes(1);
	});
});
