// @vitest-environment node
/**
 * Regression coverage for the logger-facade consolidation: BOTH
 * logger implementations (`printfLogger.ts::log` and
 * `structuredLogger.ts::logger`) must format through the SAME
 * primitive (`structuredLogger.ts::redactArgsForFile`), so an
 * identical call produces BYTE-IDENTICAL formatted text in every
 * sink (`electron-runtime.log`, `electron-main.log`,
 * `electron-lifecycle.log`). Pre-consolidation each module carried
 * its own copy of the per-arg redact-and-format mapper, which could
 * drift silently.
 *
 * The tests compare the formatted SEGMENT of each persisted line
 * (everything after the `  LEVEL  ` separator) so the comparison is
 * independent of the wall-clock timestamps prepended by each sink.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const MOCK_CONFIG_DIR = "/tmp/vt-shared-format-primitive-test-config";

const rotationMocks = vi.hoisted(() => ({
	appendLogLineSpy: vi.fn(),
}));

vi.mock("../rotation", async () => {
	const actual =
		await vi.importActual<typeof import("../rotation")>("../rotation");
	return {
		...actual,
		appendLogLine: (...args: unknown[]) =>
			rotationMocks.appendLogLineSpy(...args),
	};
});

vi.mock("../../config-dir", () => ({
	computeConfigDir: () => MOCK_CONFIG_DIR,
}));

vi.mock("electron", () => ({
	app: { isPackaged: false },
}));

/** Extract the formatted segment (after `  <LEVEL>  `) from a persisted line. */
function formattedSegment(line: string, level: string): string {
	const marker = `  ${level}  `;
	const idx = line.indexOf(marker);
	expect(idx, `line should contain the ${marker} separator`).toBeGreaterThan(
		-1,
	);
	return line.slice(idx + marker.length).replace(/\n$/, "");
}

function findLine(level: string, filename: string): string {
	const call = rotationMocks.appendLogLineSpy.mock.calls.find(
		(c) =>
			String(c[0]).endsWith(filename) && String(c[1]).includes(`  ${level}  `),
	);
	expect(
		call,
		`expected a persisted ${level} line in ${filename}`,
	).toBeDefined();
	return String(call?.[1]);
}

describe("both loggers format through one shared primitive", () => {
	let logSpy: ReturnType<typeof vi.spyOn>;
	let warnSpy: ReturnType<typeof vi.spyOn>;
	let errorSpy: ReturnType<typeof vi.spyOn>;

	beforeEach(async () => {
		delete process.env.VOICE_TYPER_ELECTRON_INFO_LOG;
		vi.resetModules();
		rotationMocks.appendLogLineSpy.mockClear();
		logSpy = vi.spyOn(console, "log").mockImplementation(() => undefined);
		warnSpy = vi.spyOn(console, "warn").mockImplementation(() => undefined);
		errorSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);
	});

	afterEach(() => {
		logSpy.mockRestore();
		warnSpy.mockRestore();
		errorSpy.mockRestore();
	});

	it("plain args: printf log.warn and structured logger.warn persist identical text", async () => {
		const { log } = await import("../printfLogger");
		const { logger } = await import("../structuredLogger");

		log.warn("disk almost full", "95%");
		logger.warn("disk almost full", "95%");

		const printfText = formattedSegment(
			findLine("WARN", "electron-runtime.log"),
			"WARN",
		);
		const structuredText = formattedSegment(
			findLine("WARN", "electron-main.log"),
			"WARN",
		);
		expect(printfText).toBe(structuredText);
		expect(printfText).toBe("disk almost full 95%");
	});

	it("object + Error args: both sinks stringify identically (stack preserved)", async () => {
		const { log } = await import("../printfLogger");
		const { logger } = await import("../structuredLogger");
		const err = new TypeError("connection reset mid-flight");

		log.error("send failed", { attempt: 2 }, err);
		logger.error("send failed", { attempt: 2 }, err);

		const printfText = formattedSegment(
			findLine("ERROR", "electron-runtime.log"),
			"ERROR",
		);
		const structuredText = formattedSegment(
			findLine("ERROR", "electron-main.log"),
			"ERROR",
		);
		expect(printfText).toBe(structuredText);
		expect(printfText).toContain('{"attempt":2}');
		expect(printfText).toContain("TypeError: connection reset mid-flight");
	});

	it("zero args: both sinks persist just the message", async () => {
		const { log } = await import("../printfLogger");
		const { logger } = await import("../structuredLogger");

		log.warn("heartbeat missed");
		logger.warn("heartbeat missed");

		const printfText = formattedSegment(
			findLine("WARN", "electron-runtime.log"),
			"WARN",
		);
		const structuredText = formattedSegment(
			findLine("WARN", "electron-main.log"),
			"WARN",
		);
		expect(printfText).toBe("heartbeat missed");
		expect(structuredText).toBe("heartbeat missed");
	});
});
