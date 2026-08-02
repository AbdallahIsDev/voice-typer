// @vitest-environment node
import path from "node:path";
import {
	afterAll,
	afterEach,
	beforeAll,
	beforeEach,
	describe,
	expect,
	it,
	vi,
} from "vitest";

const fsSpy = vi.hoisted(() => ({
	// The path parameter is required so `mockImplementation((p: string) => …)`
	// type-checks against the spy's inferred signature.
	existsSync: vi.fn((_p: string) => false),
}));
const mockApp = vi.hoisted(() => ({ isPackaged: false }));

// `python-args.ts` uses a default import (`import fs from "node:fs"`),
// so the mock must expose the spy via `default` too.
vi.mock("node:fs", () => ({
	default: { existsSync: fsSpy.existsSync },
	existsSync: fsSpy.existsSync,
}));
vi.mock("electron", () => ({ app: mockApp }));
vi.mock("../../constants", () => ({ IPC_PORT: 9876 }));
vi.mock("../../single_instance", () => ({
	computeConfigDir: () => "/home/user/.voice-typer",
}));

import { pythonArgs } from "../python-args";

const RESOURCES = "/opt/app/resources";
const CONFIG_DIR = "/home/user/.voice-typer";

describe("pythonArgs()", () => {
	let platformSpy: ReturnType<typeof vi.spyOn>;
	let originalResourcesPath: unknown;

	beforeAll(() => {
		originalResourcesPath = (process as unknown as Record<string, unknown>)
			.resourcesPath;
		// Electron-only property — define it so the packaged branches are
		// deterministic in the plain-Node test environment.
		Object.defineProperty(process, "resourcesPath", {
			value: RESOURCES,
			configurable: true,
			writable: true,
		});
	});

	afterAll(() => {
		if (originalResourcesPath === undefined) {
			delete (process as unknown as Record<string, unknown>).resourcesPath;
		} else {
			Object.defineProperty(process, "resourcesPath", {
				value: originalResourcesPath,
				configurable: true,
			});
		}
	});

	beforeEach(() => {
		platformSpy = vi.spyOn(process, "platform", "get");
		platformSpy.mockReturnValue("win32");
		mockApp.isPackaged = false;
		fsSpy.existsSync.mockReset();
		fsSpy.existsSync.mockReturnValue(false);
	});

	afterEach(() => {
		vi.restoreAllMocks();
	});

	it("returns the dev venv pythonw.exe on win32 when not packaged", () => {
		platformSpy.mockReturnValue("win32");
		expect(pythonArgs()).toEqual([
			path.join(CONFIG_DIR, "venv", "Scripts", "pythonw.exe"),
			["-m", "voice_typer.server.ipc_server", "--port", "9876"],
		]);
	});

	it("returns the dev venv python3 on linux when not packaged", () => {
		platformSpy.mockReturnValue("linux");
		expect(pythonArgs()).toEqual([
			path.join(CONFIG_DIR, "venv", "bin", "python3"),
			["-m", "voice_typer.server.ipc_server", "--port", "9876"],
		]);
	});

	it("resolves the bundled onefile backend on win32 when packaged", () => {
		platformSpy.mockReturnValue("win32");
		mockApp.isPackaged = true;
		const onefile = path.join(
			RESOURCES,
			"voice-typer-backend",
			"VoiceTyper.exe",
		);
		fsSpy.existsSync.mockImplementation((p: string) => p === onefile);
		const [exe, args] = pythonArgs();
		expect(exe).toBe(onefile);
		expect(args).toEqual(["--port", "9876"]);
	});

	it("falls back to the bundled onedir backend on win32 when the onefile is missing", () => {
		platformSpy.mockReturnValue("win32");
		mockApp.isPackaged = true;
		const onedir = path.join(
			RESOURCES,
			"voice-typer-backend",
			"VoiceTyper",
			"VoiceTyper.exe",
		);
		fsSpy.existsSync.mockImplementation((p: string) => p === onedir);
		expect(pythonArgs()[0]).toBe(onedir);
	});

	it("falls through to the dev venv when packaged but no bundled backend exists", () => {
		platformSpy.mockReturnValue("win32");
		mockApp.isPackaged = true;
		expect(pythonArgs()[0]).toBe(
			path.join(CONFIG_DIR, "venv", "Scripts", "pythonw.exe"),
		);
	});

	it("resolves the bundled .app backend on darwin when packaged", () => {
		platformSpy.mockReturnValue("darwin");
		mockApp.isPackaged = true;
		const appPath = path.join(
			RESOURCES,
			"voice-typer-backend.app",
			"Contents",
			"MacOS",
			"voice-typer",
		);
		fsSpy.existsSync.mockImplementation((p: string) => p === appPath);
		expect(pythonArgs()[0]).toBe(appPath);
	});

	it("falls back to the bare Mach-O backend on darwin when the .app is missing", () => {
		platformSpy.mockReturnValue("darwin");
		mockApp.isPackaged = true;
		const bare = path.join(RESOURCES, "voice-typer-backend", "voice-typer");
		fsSpy.existsSync.mockImplementation((p: string) => p === bare);
		expect(pythonArgs()[0]).toBe(bare);
	});

	it("resolves the onedir backend on linux when packaged", () => {
		platformSpy.mockReturnValue("linux");
		mockApp.isPackaged = true;
		const onedir = path.join(RESOURCES, "voice-typer-backend", "voice-typer");
		fsSpy.existsSync.mockImplementation((p: string) => p === onedir);
		expect(pythonArgs()[0]).toBe(onedir);
	});

	it("falls back to the onefile executable on linux when the onedir layout is missing", () => {
		platformSpy.mockReturnValue("linux");
		mockApp.isPackaged = true;
		const onefile = path.join(RESOURCES, "voice-typer-backend", "VoiceTyper");
		fsSpy.existsSync.mockImplementation((p: string) => p === onefile);
		expect(pythonArgs()[0]).toBe(onefile);
	});

	it("keeps trying candidates when fs.existsSync throws", () => {
		platformSpy.mockReturnValue("linux");
		mockApp.isPackaged = true;
		fsSpy.existsSync.mockImplementation(() => {
			throw new Error("EACCES");
		});
		// Every candidate lookup throws → falls through to the dev venv.
		expect(pythonArgs()[0]).toBe(
			path.join(CONFIG_DIR, "venv", "bin", "python3"),
		);
	});
});
