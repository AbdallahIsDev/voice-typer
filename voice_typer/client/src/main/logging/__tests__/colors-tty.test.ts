// @vitest-environment node
/**
 * TTY gating for the ANSI color constants (`colors.ts`) + the `ts()`
 * timestamp form (`rotation.ts`).
 *
 * Colors are emitted ONLY when stdout/stderr are attached to a
 * terminal. When output is redirected to log files (the launcher's
 * `electron-stdout.log` / `electron-stderr.log`), pipes, or CI, every
 * constant resolves to `""` and `ts()` switches to the date-prefixed
 * form — so the files stay clean and grep-friendly (mirrors Node's own
 * `isTTY` color detection and the Python side's
 * `do_color = sys.stderr.isatty()` gate).
 */
import { afterEach, describe, expect, it, vi } from "vitest";

function setTty(value: boolean): void {
	Object.defineProperty(process.stdout, "isTTY", {
		value,
		configurable: true,
	});
}

afterEach(() => {
	vi.resetModules();
	setTty(false);
});

describe("ANSI color constants follow the TTY state", () => {
	it("resolve to empty strings when output is redirected (no terminal)", async () => {
		setTty(false);
		const {
			DIM,
			RESET,
			BUBBLE_CLR,
			RENDERER_CLR,
			INFO_CLR,
			WARN_CLR,
			ERROR_CLR,
		} = await import("../colors");
		expect(DIM).toBe("");
		expect(RESET).toBe("");
		expect(BUBBLE_CLR).toBe("");
		expect(RENDERER_CLR).toBe("");
		expect(INFO_CLR).toBe("");
		expect(WARN_CLR).toBe("");
		expect(ERROR_CLR).toBe("");
	});

	it("resolve to real ANSI codes when a terminal is attached", async () => {
		setTty(true);
		const { DIM, RESET, BUBBLE_CLR, INFO_CLR, WARN_CLR, ERROR_CLR } =
			await import("../colors");
		expect(DIM).toBe("\x1b[38;5;242m");
		expect(RESET).toBe("\x1b[0m");
		expect(BUBBLE_CLR).toBe("\x1b[38;5;39m");
		expect(INFO_CLR).toBe("\x1b[38;5;39m");
		expect(WARN_CLR).toBe("\x1b[38;5;226m");
		expect(ERROR_CLR).toBe("\x1b[38;5;196m");
	});
});

describe("ts() timestamp form follows the TTY state", () => {
	it("is time-only (dimmed) on a terminal", async () => {
		setTty(true);
		const { DIM, RESET } = await import("../colors");
		const { ts } = await import("../rotation");
		const out = ts();
		// ANSI-wrapped time-only form: DIM + HH:MM:SS + RESET.
		expect(out.startsWith(DIM)).toBe(true);
		expect(out.endsWith(RESET)).toBe(true);
		expect(out.replace(DIM, "").replace(RESET, "")).toMatch(
			/^\d{2}:\d{2}:\d{2}$/,
		);
	});

	it("is date-prefixed when output is redirected (no terminal)", async () => {
		setTty(false);
		const { ts } = await import("../rotation");
		const out = ts();
		expect(out).toMatch(/^\d{4}-\d{2}-\d{2} {2}\d{2}:\d{2}:\d{2}$/);
	});
});
