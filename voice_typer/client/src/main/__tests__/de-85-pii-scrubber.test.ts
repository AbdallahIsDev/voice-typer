// @vitest-environment node
/**
 * DE-85 unit tests: scrubComponentStackPii strips string-literal
 * prop values from React componentStack before writing to
 * electron-renderer-errors.log.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

// Mock electron minimally so window-handlers.ts can be imported.
vi.mock("electron", () => ({
	app: { getPath: vi.fn(() => "/tmp"), isPackaged: false },
	dialog: { showOpenDialog: vi.fn() },
	ipcMain: { handle: vi.fn() },
	shell: { openPath: vi.fn() },
}));
vi.mock("../i18n", () => ({ mainT: (k: string) => k }));
vi.mock("../single_instance", () => ({
	computeConfigDir: () => "/mock",
}));
vi.mock("../state", () => ({ state: { mainWindow: null } }));

describe("DE-85: scrubComponentStackPii strips prop values from componentStack", () => {
	let scrub: (s: string) => string;

	beforeEach(async () => {
		vi.resetModules();
		const mod = await import("../ipc/window-handlers");
		scrub = mod.scrubComponentStackPii;
	});

	it("strips single-quoted prop values", () => {
		const input =
			"    in Transcription text='user secret utterance' (created by App)";
		const out = scrub(input);
		expect(out).toContain("text='[scrubbed]'");
		expect(out).not.toContain("user secret utterance");
	});

	it("strips double-quoted prop values", () => {
		const input =
			'    in Transcription text="user secret utterance" (created by App)';
		const out = scrub(input);
		expect(out).toContain('text="[scrubbed]"');
		expect(out).not.toContain("user secret utterance");
	});

	it("strips braced prop expressions", () => {
		const input =
			"    in Transcription text={'user data here'} (created by App)";
		const out = scrub(input);
		expect(out).toContain("text={[scrubbed]}");
		expect(out).not.toContain("user data here");
	});

	it("strips multiple props on the same line", () => {
		const input = "    in Foo a='secret1' b='secret2' (created by App)";
		const out = scrub(input);
		expect(out).toContain("a='[scrubbed]'");
		expect(out).toContain("b='[scrubbed]'");
		expect(out).not.toContain("secret1");
		expect(out).not.toContain("secret2");
	});

	it("preserves the component tree structure (names + 'created by')", () => {
		const input =
			"    in Transcription text='secret' (created by App)\n    in div (created by Transcription)";
		const out = scrub(input);
		expect(out).toContain("in Transcription");
		expect(out).toContain("(created by App)");
		expect(out).toContain("in div");
		expect(out).toContain("(created by Transcription)");
	});

	it("leaves componentStack without props unchanged", () => {
		const input =
			"    in Transcription (created by App)\n    in div (created by Transcription)";
		expect(scrub(input)).toBe(input);
	});

	it("handles a realistic React componentStack with mixed props", () => {
		const input = [
			"    in Transcription text='hello world' (created by App)",
			'    in Bubble visible="true" (created by Transcription)',
			"    in div className={'container'} (created by Bubble)",
		].join("\n");
		const out = scrub(input);
		expect(out).not.toContain("hello world");
		expect(out).not.toContain("container");
		expect(out).toContain("[scrubbed]");
	});
});
