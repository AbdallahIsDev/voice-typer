// @vitest-environment node
/**
 * Regression coverage: printfLogger formats args exactly once per
 * `log.warn` / `log.error` call.
 *
 * Pre-fix `log.warn("foo", "bar")` called `formatArgsForFile(args)` twice:
 * once inside `writeStdout` (for the stdout tee) and once inside
 * `mainRuntimeLogger.write` (for the file tee). Both calls produced the
 * identical string, so the second call was pure overhead — every WARN/
 * ERROR line paid for two PII-redaction passes over the same args.
 *
 * Post-fix the printf-style `log.warn` / `log.error` compute the
 * formatted string ONCE and pass it to both `writeStdout` and
 * `mainRuntimeLogger.write`.
 *
 * The test counts `redactPii` calls (spied via the rotation module mock)
 * as a proxy for `formatArgsForFile` calls — `formatArgsForFile` invokes
 * `redactPii` exactly once per arg, so `redactPii` call count =
 * `args.length * (number of formatArgsForFile invocations)`.
 *
 * Tests:
 *   1. `log.warn("foo", "bar")` calls `redactPii` exactly 2 times
 *      (once per arg) — proving `formatArgsForFile` was invoked once.
 *      Pre-fix this would have been 4 (2 args × 2 calls).
 *   2. Same assertion for `log.error`.
 *   3. `log.info` with N args calls `redactPii` exactly N times (single
 *      format pass for the stdout tee; the PERSIST_INFO branch uses
 *      `String(a)` and does NOT call `redactPii`).
 *   4. The stdout line and the file line contain the same formatted text
 *      (no asymmetric drift between the two tees).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock `electron` so `app.getPath("userData")` returns a deterministic
// path and `app.isPackaged` returns `false`. The printfLogger module
// imports `{ app }` at module-load time, so the mock must be in place
// before the first `import("../printfLogger")`.
vi.mock("electron", () => ({
	app: {
		getPath: () => "/tmp/vt-printf-single-format-test-userdata",
		isPackaged: false,
	},
}));

// Track calls to the mocked rotation module. `redactPii` is the proxy
// for `formatArgsForFile` call count (each `formatArgsForFile` invocation
// calls `redactPii` exactly once per arg). `appendLogLine` is spied so
// we can assert the file-tee line content matches the stdout-tee line.
const rotationMocks = vi.hoisted(() => ({
	redactPiiSpy: vi.fn((s: string) => s),
	appendLogLineSpy: vi.fn(),
	tsStub: vi.fn(() => "12:00:00"),
}));

vi.mock("../rotation", () => ({
	appendLogLine: (...args: unknown[]) =>
		rotationMocks.appendLogLineSpy(...args),
	rotateIfNeeded: vi.fn(),
	cleanConsoleMsg: vi.fn(),
	redactPii: (s: string) => rotationMocks.redactPiiSpy(s),
	ts: () => rotationMocks.tsStub(),
}));

// Mock `../structuredLogger` so `PERSIST_INFO` is `false` (default) and
// `appendLifecycleLine` is a no-op spy. This isolates the test to the
// stdout + file-tee paths (no INFO persistence branch interference).
const structuredMocks = vi.hoisted(() => ({
	appendLifecycleLineSpy: vi.fn(),
}));
vi.mock("../structuredLogger", () => ({
	PERSIST_INFO: false,
	appendLifecycleLine: (...args: unknown[]) =>
		structuredMocks.appendLifecycleLineSpy(...args),
}));

// Suppress console output during the test (the printf logger mirrors to
// console.warn / console.error / console.log — we don't want the test
// runner output polluted).
let logSpy: ReturnType<typeof vi.spyOn>;
let warnSpy: ReturnType<typeof vi.spyOn>;
let errorSpy: ReturnType<typeof vi.spyOn>;

describe("printfLogger formats args exactly once per log.warn / log.error", () => {
	beforeEach(async () => {
		vi.resetModules();
		rotationMocks.redactPiiSpy.mockClear();
		rotationMocks.appendLogLineSpy.mockClear();
		rotationMocks.tsStub.mockClear();
		structuredMocks.appendLifecycleLineSpy.mockClear();
		// Default implementation: passthrough redactPii.
		rotationMocks.redactPiiSpy.mockImplementation((s: string) => s);

		logSpy = vi.spyOn(console, "log").mockImplementation(() => undefined);
		warnSpy = vi.spyOn(console, "warn").mockImplementation(() => undefined);
		errorSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);
	});

	afterEach(() => {
		logSpy.mockRestore();
		warnSpy.mockRestore();
		errorSpy.mockRestore();
	});

	it("log.warn with 2 args calls redactPii exactly 2 times (single format pass)", async () => {
		const { log } = await import("../printfLogger");
		log.warn("foo", "bar");
		// 2 args × 1 formatArgsForFile call = 2 redactPii calls.
		// Pre-fix this would have been 4 (2 args × 2 formatArgsForFile calls).
		expect(rotationMocks.redactPiiSpy).toHaveBeenCalledTimes(2);
	});

	it("log.error with 2 args calls redactPii exactly 2 times (single format pass)", async () => {
		const { log } = await import("../printfLogger");
		log.error("foo", "bar");
		expect(rotationMocks.redactPiiSpy).toHaveBeenCalledTimes(2);
	});

	it("log.warn with 0 args calls redactPii exactly 0 times (no args to format)", async () => {
		const { log } = await import("../printfLogger");
		log.warn();
		expect(rotationMocks.redactPiiSpy).toHaveBeenCalledTimes(0);
	});

	it("log.warn with 5 args calls redactPii exactly 5 times", async () => {
		const { log } = await import("../printfLogger");
		log.warn("a", "b", "c", "d", "e");
		expect(rotationMocks.redactPiiSpy).toHaveBeenCalledTimes(5);
	});

	it("log.error with 3 args calls redactPii exactly 3 times", async () => {
		const { log } = await import("../printfLogger");
		log.error("a", "b", "c");
		expect(rotationMocks.redactPiiSpy).toHaveBeenCalledTimes(3);
	});

	it("log.info with 3 args calls redactPii exactly 3 times (single stdout format)", async () => {
		const { log } = await import("../printfLogger");
		log.info("a", "b", "c");
		expect(rotationMocks.redactPiiSpy).toHaveBeenCalledTimes(3);
	});

	it("stdout line and file line share the same formatted text (no drift)", async () => {
		const { log } = await import("../printfLogger");
		log.warn("hello", "world");
		// The file-tee line is the 2nd arg to appendLogLine.
		expect(rotationMocks.appendLogLineSpy).toHaveBeenCalledTimes(1);
		const fileCall = rotationMocks.appendLogLineSpy.mock.calls[0];
		const fileLine = String(fileCall?.[1]);
		// The stdout line is the 1st arg to console.warn.
		expect(warnSpy).toHaveBeenCalledTimes(1);
		const stdoutLine = String(warnSpy.mock.calls[0][0]);
		// Both lines must contain the same redacted args text. The stdout
		// line has the ANSI color prefix + timestamp; the file line has the
		// ISO timestamp + level tag. But both must contain "hello world".
		expect(stdoutLine).toContain("hello world");
		expect(fileLine).toContain("hello world");
		// The file line ends with \n (so tail -f shows it cleanly).
		expect(fileLine.endsWith("\n")).toBe(true);
	});

	it("redactPii is actually invoked (not just imported) — confirms formatArgsForFile ran", async () => {
		const { log } = await import("../printfLogger");
		log.warn("secret-token-test");
		// Verify redactPii was called with the arg string (proving the
		// format path actually ran, not just that the spy was wired).
		expect(rotationMocks.redactPiiSpy).toHaveBeenCalledWith(
			"secret-token-test",
		);
	});
});
