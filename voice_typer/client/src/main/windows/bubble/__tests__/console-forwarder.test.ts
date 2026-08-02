// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";

const logSpies = vi.hoisted(() => ({
	info: vi.fn(),
	warn: vi.fn(),
	error: vi.fn(),
	debug: vi.fn(),
}));

const cleanConsoleMsgSpy = vi.hoisted(() => vi.fn((m: unknown) => String(m)));

vi.mock("../../../logging", () => ({
	log: logSpies,
	RESET: "\u001b[0m",
	cleanConsoleMsg: cleanConsoleMsgSpy,
}));

import type { BrowserWindow } from "electron";
import { attachConsoleForwarder } from "../console-forwarder";

interface MockConsoleEvent {
	level: number;
	message: string;
	lineNumber: number;
	sourceId: string;
}

function setup(options?: { tag: string; colorPrefix: string }) {
	let handler: ((e: MockConsoleEvent) => void) | undefined;
	const win = {
		webContents: {
			on: vi.fn((_event: string, cb: (e: MockConsoleEvent) => void) => {
				handler = cb;
			}),
		},
	} as unknown as BrowserWindow;
	attachConsoleForwarder(
		win,
		options ?? { tag: "[BUBBLE]", colorPrefix: "\u001b[36m" },
	);
	const onSpy = win.webContents.on as ReturnType<typeof vi.fn>;
	return {
		onSpy,
		emit: (e: MockConsoleEvent) => handler?.(e),
	};
}

describe("attachConsoleForwarder", () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it("registers a console-message listener on webContents", () => {
		const { onSpy } = setup();
		expect(onSpy).toHaveBeenCalledWith("console-message", expect.any(Function));
	});

	it("routes INFO (level 1) messages to log.info with the tag and location", () => {
		const { emit } = setup({ tag: "[BUBBLE]", colorPrefix: "" });
		emit({
			level: 1,
			message: "hello renderer",
			lineNumber: 42,
			sourceId: "bubble.html",
		});
		expect(logSpies.info).toHaveBeenCalledWith(
			expect.stringMatching(
				/\[BUBBLE\] INFO.*hello renderer \(bubble\.html:42\)/,
			),
		);
		expect(logSpies.warn).not.toHaveBeenCalled();
		expect(logSpies.error).not.toHaveBeenCalled();
	});

	it("routes WARN (level 2) messages to log.warn", () => {
		const { emit } = setup();
		emit({ level: 2, message: "careful", lineNumber: 7, sourceId: "a.ts" });
		expect(logSpies.warn).toHaveBeenCalledWith(
			expect.stringMatching(/WARN.*careful \(a\.ts:7\)/),
		);
	});

	it("routes ERROR (level 3) messages to log.error", () => {
		const { emit } = setup();
		emit({ level: 3, message: "boom", lineNumber: 9, sourceId: "b.ts" });
		expect(logSpies.error).toHaveBeenCalledWith(
			expect.stringMatching(/ERROR.*boom \(b\.ts:9\)/),
		);
	});

	it("drops VERBOSE (level 0) messages", () => {
		const { emit } = setup();
		emit({ level: 0, message: "noisy", lineNumber: 1, sourceId: "c.ts" });
		expect(logSpies.info).not.toHaveBeenCalled();
		expect(logSpies.warn).not.toHaveBeenCalled();
		expect(logSpies.error).not.toHaveBeenCalled();
	});

	it("tags unknown levels as LOG and routes them through the ERROR channel (level >= 3)", () => {
		const { emit } = setup();
		emit({ level: 9, message: "weird", lineNumber: 3, sourceId: "d.ts" });
		expect(logSpies.error).toHaveBeenCalledWith(
			expect.stringMatching(/LOG.*weird \(d\.ts:3\)/),
		);
	});
});
