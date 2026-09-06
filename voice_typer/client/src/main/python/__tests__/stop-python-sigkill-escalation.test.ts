// @vitest-environment node
/**
 * Regression test: stop-python.ts SIGKILL escalation actually fires
 * on POSIX when the Python backend ignores SIGTERM.
 *
 * Pre-fix bug (POSIX-only — Linux + macOS): the escalateTimer callback at
 * ``stop-python.ts`` checked ``if (!proc.killed)`` to decide whether to send
 * SIGKILL. But Node.js docs confirm ``subprocess.killed`` is set to ``true``
 * immediately after ``subprocess.kill()`` is used to successfully SEND a
 * signal — it does NOT reset when the proc actually dies. So at t=3s when
 * escalateTimer fires, ``proc.killed`` was already ``true`` (set at the
 * SIGTERM step by ``proc.kill("SIGTERM")``), and ``!proc.killed`` returned
 * ``false``, so ``proc.kill("SIGKILL")`` was NEVER REACHED on POSIX. This
 * orphaned the Python process when it was stuck in a C extension holding the
 * GIL (e.g. torch model load, sounddevice buffer hold) — the orphan kept
 * holding the ``VoiceTyperSingleInstance`` mutex, blocking the next launch.
 *
 * Fix: replace ``!proc.killed`` with
 * ``proc.exitCode === null && proc.signalCode === null`` — matching the
 * already-shipped fix in ``kill-python.ts::killPythonProcessWithSigkillFallback``
 * (see ``kill-python.ts:84``). The new check fires SIGKILL whenever the proc
 * has NOT actually exited, regardless of whether a previous signal was sent.
 *
 * Test strategy: we cannot reliably run a real "ignores SIGTERM" child proc
 * under vitest fake timers (fake timers don't advance real wall-clock time, so
 * a real spawned child would not actually receive signals within the test
 * window). Instead we use a mock ChildProcess whose ``kill()`` records the
 * signal name and whose ``exitCode`` / ``signalCode`` mirror Node's real
 * semantics — ``null`` while alive, set when the proc actually exits. The
 * ``autoExitOnKill: false`` variant simulates a proc that ignores SIGTERM
 * (the C-extension-stuck case): ``kill("SIGTERM")`` is called but the proc
 * never emits ``"exit"``, so ``exitCode`` / ``signalCode`` stay ``null``.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { MainState } from "../../state";

// ─── Mocks ──────────────────────────────────────────────────────────────

const mockClearTcpStartupTimeout = vi.fn();

const mockState: MainState = {
	pythonProcess: null,
	tcpSocket: null,
	mainWindow: null,
	bubbleWindow: null,
	pendingRequests: new Map(),
	nextId: 1,
	tcpBuffer: Buffer.alloc(0),
	pythonReady: false,
	pythonExitedEarly: false,
	heartbeatInterval: null,
	sessionNonce: "",
	bubblePosition: "bottom",
	bubbleDraggable: true,
	_hideTimeout: null,
	_tcpRetryCount: 0,
	_tcpRetryGeneration: 0,
	_tcpRetryTimer: null,
	_tcpAuthed: false,
	_hadConnectedBefore: false,
	_relaunching: false,
	_restartTriggered: false,
	_stopPythonCalled: false,
};

vi.mock("electron", () => ({
	app: {
		quit: vi.fn(),
		exit: vi.fn(),
		relaunch: vi.fn(),
		isPackaged: false,
		isQuitting: false,
	},
}));
vi.mock("../../state", () => ({ state: mockState }));
vi.mock("../send-to-python", () => ({
	sendToPython: vi.fn(() => Promise.resolve()),
	_resetIpcBackpressure: vi.fn(),
}));
vi.mock("../tcp-connect", () => ({
	tcpConnect: vi.fn(),
	clearTcpStartupTimeout: mockClearTcpStartupTimeout,
}));

// ─── Mock proc factory ──────────────────────────────────────────────────

/**
 * Build a mock ChildProcess-like object whose ``exitCode`` / ``signalCode``
 * mirror Node's real semantics:
 *   - Both start ``null`` and stay ``null`` while the proc is alive.
 *   - When ``kill(sig)`` is called AND ``autoExitOnKill === true``, the mock
 *     emits an ``"exit"`` event (mirroring a graceful kill) and sets
 *     ``signalCode = sig`` (matching Node's behavior on signal-kill).
 *   - When ``autoExitOnKill === false``, the mock ignores the signal —
 *     ``exitCode`` / ``signalCode`` stay ``null``, simulating a proc stuck
 *     in a C extension that never delivers SIGTERM to the Python signal
 *     handler. ``killed`` is still flipped to ``true`` (matching Node,
 *     which sets ``subprocess.killed = true`` synchronously inside
 *     ``subprocess.kill()`` regardless of whether the proc actually dies).
 *
 * The ``kill`` spy records every call's signal argument so tests can assert
 * the SIGTERM → SIGKILL escalation sequence.
 */
function makeMockProc(opts: { autoExitOnKill?: boolean } = {}) {
	const { autoExitOnKill = true } = opts;
	const listeners: Record<string, Array<(...a: unknown[]) => void>> = {};
	const proc = {
		pid: 99999,
		killed: false,
		// Node sets `exitCode` / `signalCode` to `null` until the child
		// actually exits. The mock mirrors that so the SIGKILL-fallback
		// liveness check
		// (`proc.exitCode === null && proc.signalCode === null`) behaves
		// the same way it does against a real ChildProcess.
		exitCode: null as number | null,
		signalCode: null as string | null,
		on: vi.fn((ev: string, cb: (...a: unknown[]) => void) => {
			if (!listeners[ev]) listeners[ev] = [];
			listeners[ev].push(cb);
		}),
		once: vi.fn((ev: string, cb: (...a: unknown[]) => void) => {
			if (!listeners[ev]) listeners[ev] = [];
			listeners[ev].push(cb);
		}),
		removeAllListeners: vi.fn((ev?: string) => {
			if (ev) listeners[ev] = [];
			else for (const k of Object.keys(listeners)) delete listeners[k];
		}),
		kill: vi.fn((sig?: string) => {
			// Node sets `killed = true` synchronously inside kill(),
			// regardless of whether the proc actually exits. This is the
			// root cause of the pre-fix bug — `proc.killed` was true even
			// though the proc was still alive.
			proc.killed = true;
			if (autoExitOnKill) {
				queueMicrotask(() => {
					// Mirror Node: when a signal kills the proc,
					// `signalCode = sig` and `exitCode = null`.
					proc.signalCode = sig ?? "SIGTERM";
					proc.exitCode = null;
					(listeners.exit ?? []).forEach((cb) => {
						cb(null, proc.signalCode);
					});
				});
			}
			// When autoExitOnKill === false, do nothing — the signal is
			// "queued but never delivered" (stuck in C extension), so
			// exitCode/signalCode remain null and `killed` is true.
			return true;
		}),
		emit: vi.fn((ev: string, ...a: unknown[]) => {
			(listeners[ev] ?? []).forEach((cb) => {
				cb(...a);
			});
		}),
	};
	return proc;
}

// ─── Tests ──────────────────────────────────────────────────────────────

describe("stop-python SIGKILL escalation", () => {
	let stopPython: () => void;
	let KILL_TIMER_MS: number;
	let ESCALATE_TIMER_MS: number;

	beforeEach(async () => {
		vi.clearAllMocks();
		vi.useFakeTimers();
		Object.assign(mockState, {
			pythonProcess: null,
			tcpSocket: null,
			mainWindow: null,
			tcpBuffer: Buffer.alloc(0),
			pythonReady: false,
			pythonExitedEarly: false,
			_tcpRetryCount: 0,
			_tcpRetryTimer: null,
			_tcpRetryGeneration: 0,
			_tcpAuthed: false,
			_hadConnectedBefore: false,
			_relaunching: false,
			_restartTriggered: false,
			_stopPythonCalled: false,
		});
		vi.resetModules();
		const mod = await import("../stop-python");
		stopPython = mod.stopPython;
		KILL_TIMER_MS = mod.KILL_TIMER_MS;
		ESCALATE_TIMER_MS = mod.ESCALATE_TIMER_MS;
	});

	// This is the core regression test. The pre-fix code's
	// `!proc.killed` check was always `false` after the SIGTERM (because
	// `proc.killed` is set synchronously inside `proc.kill("SIGTERM")`), so
	// the SIGKILL escalation NEVER fired on POSIX. With the fix, the
	// `proc.exitCode === null && proc.signalCode === null` check evaluates
	// `true` (proc is still alive) and SIGKILL is sent.
	it.skipIf(process.platform === "win32")(
		"escalateTimer sends SIGKILL when proc ignores SIGTERM (POSIX)",
		async () => {
			// Mock proc that IGNORES SIGTERM (autoExitOnKill: false) —
			// simulates `node -e "process.on('SIGTERM', () => {}); process.stdin.resume()"`
			// or a Python proc stuck in a C extension holding the GIL.
			// SIGTERM is queued but never delivered, so `exitCode` /
			// `signalCode` stay null and the escalateTimer MUST fire SIGKILL.
			const proc = makeMockProc({ autoExitOnKill: false });
			mockState.pythonProcess = proc as unknown as MainState["pythonProcess"];

			stopPython();

			// Advance past the KILL_TIMER_MS grace period — SIGTERM fires.
			await vi.advanceTimersByTimeAsync(KILL_TIMER_MS);
			expect(proc.kill).toHaveBeenCalledWith("SIGTERM");
			// The proc ignored SIGTERM — exitCode/signalCode are still null,
			// so the escalateTimer MUST fire SIGKILL next. Only one kill
			// call so far (the SIGTERM).
			expect(proc.kill).toHaveBeenCalledTimes(1);

			// Advance past ESCALATE_TIMER_MS — SIGKILL escalation fires.
			await vi.advanceTimersByTimeAsync(ESCALATE_TIMER_MS);
			expect(proc.kill).toHaveBeenCalledWith("SIGKILL");
			// Total calls: 1 SIGTERM + 1 SIGKILL.
			expect(proc.kill).toHaveBeenCalledTimes(2);
		},
	);

	it.skipIf(process.platform === "win32")(
		"escalateTimer is a no-op when proc gracefully exits before ESCALATE_TIMER_MS (POSIX)",
		async () => {
			// Mock proc that DOES exit on SIGTERM (autoExitOnKill: true) —
			// simulates graceful shutdown. After SIGTERM fires, the mock
			// sets `signalCode = "SIGTERM"` and emits "exit", which both
			// (a) makes the escalateTimer liveness check evaluate false
			// (signalCode !== null), and (b) triggers the
			// `proc.once("exit", () => clearTimeout(escalateTimer))`
			// handler to clear the escalateTimer entirely.
			const proc = makeMockProc({ autoExitOnKill: true });
			mockState.pythonProcess = proc as unknown as MainState["pythonProcess"];

			stopPython();

			// Advance past KILL_TIMER_MS — SIGTERM fires and the proc
			// schedules an async "exit" emit via queueMicrotask.
			await vi.advanceTimersByTimeAsync(KILL_TIMER_MS);
			expect(proc.kill).toHaveBeenCalledWith("SIGTERM");
			// Drain the queueMicrotask that emits the "exit" event so the
			// escalateTimer is cleared by the .once("exit") handler.
			await Promise.resolve();
			await Promise.resolve();

			// Advance past ESCALATE_TIMER_MS — escalateTimer should NOT have
			// fired (it was cleared by the exit handler). Even if it had
			// fired, the liveness check would be false (signalCode !== null).
			await vi.advanceTimersByTimeAsync(ESCALATE_TIMER_MS);
			// Only the SIGTERM call — no SIGKILL escalation.
			expect(proc.kill).toHaveBeenCalledTimes(1);
			expect(proc.kill).not.toHaveBeenCalledWith("SIGKILL");
		},
	);

	it.skipIf(process.platform === "win32")(
		"SIGKILL fires exactly KILL_TIMER_MS + ESCALATE_TIMER_MS after stopPython() (POSIX)",
		async () => {
			// Pins the escalation contract: SIGKILL must fire at
			// KILL_TIMER_MS + ESCALATE_TIMER_MS (currently 6000ms) after
			// stopPython(). Extending either delay would silently break the
			// orphan-cleanup guarantee on POSIX.
			const proc = makeMockProc({ autoExitOnKill: false });
			mockState.pythonProcess = proc as unknown as MainState["pythonProcess"];

			stopPython();

			// Advance to just before the escalation threshold. SIGTERM must
			// have fired (at KILL_TIMER_MS) but SIGKILL must NOT have fired.
			await vi.advanceTimersByTimeAsync(KILL_TIMER_MS + ESCALATE_TIMER_MS - 1);
			expect(proc.kill).toHaveBeenCalledWith("SIGTERM");
			expect(proc.kill).not.toHaveBeenCalledWith("SIGKILL");

			// Cross the escalation threshold — SIGKILL fires.
			await vi.advanceTimersByTimeAsync(1);
			expect(proc.kill).toHaveBeenCalledWith("SIGKILL");
		},
	);
});
