/**
 * BRAND-001 / Fix #25-1: assert that APP_NAME stays in sync across all
 * three branding modules.
 *
 * The renderer branding.ts is imported directly (it lives inside the
 * renderer's tsconfig include). The main branding.ts and the Python
 * branding.py live OUTSIDE the renderer's tsconfig include (they're
 * compiled under tsconfig.node.json and the Python toolchain
 * respectively), so we read them at test-time with `node:fs` and
 * regex out the APP_NAME literal — mirroring the approach used by
 * `scripts/check_branding.py`.
 *
 * Python-side parity: a separate Python test (e.g. in
 * `voice_typer/server/tests/`) would need to import `branding.py` and
 * read the TS files at runtime to assert cross-language equality.
 * `scripts/check_branding.py` already parses branding.py for APP_NAME
 * and scans source files for hardcoded occurrences; extend it if you
 * need a Python-side equality assertion. This test covers the TS side
 * (and reads the Python file as text for cross-language parity).
 */
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { APP_NAME as RENDERER_APP_NAME } from "@/branding";

// ── Path resolution ────────────────────────────────────────────────────
// This test file lives at:
//   <repo-root>/voice_typer/client/src/renderer/src/__tests__/branding-sync.test.ts
// so `__dirname` is `.../__tests__`. We walk up to the project root and
// back down into the other branding files.
//
// Trace (__dirname = .../voice-typer/voice_typer/client/src/renderer/src/__tests__):
//   ..   → .../voice-typer/voice_typer/client/src/renderer/src
//   ../.. → .../voice-typer/voice_typer/client/src/renderer
//   ../../.. → .../voice-typer/voice_typer/client/src
//   ../../../.. → .../voice-typer/voice_typer/client
//   ../../../../.. → .../voice-typer/voice_typer
//   ../../../../../.. → .../voice-typer  (project root)
const REPO_ROOT = path.resolve(__dirname, "..", "..", "..", "..", "..", "..");

const MAIN_BRANDING_PATH = path.resolve(
	REPO_ROOT,
	"voice_typer",
	"client",
	"src",
	"main",
	"branding.ts",
);
const SERVER_BRANDING_PATH = path.resolve(
	REPO_ROOT,
	"voice_typer",
	"server",
	"branding.py",
);

/**
 * Extract the APP_NAME literal from a TS or Python branding file.
 *
 * Matches both single- and double-quoted forms:
 *   APP_NAME = "Voice Typer"
 *   APP_NAME = 'Voice Typer'
 *
 * @param content  Source file contents.
 * @param source   Path used only for error reporting.
 */
function extractAppName(content: string, source: string): string {
	const match = content.match(/APP_NAME\s*=\s*["']([^"']+)["']/);
	if (!match?.[1]) {
		throw new Error(
			`Could not extract APP_NAME from ${source}. ` +
				'Ensure the file contains a line like `APP_NAME = "..."`.',
		);
	}
	return match[1];
}

describe("branding sync (BRAND-001)", () => {
	it("renderer branding.ts exports a non-empty APP_NAME string", () => {
		expect(RENDERER_APP_NAME).toBeTruthy();
		expect(typeof RENDERER_APP_NAME).toBe("string");
		expect(RENDERER_APP_NAME.length).toBeGreaterThan(0);
	});

	it("main branding.ts exists and exports the same APP_NAME as renderer", () => {
		expect(fs.existsSync(MAIN_BRANDING_PATH)).toBe(true);
		const mainContent = fs.readFileSync(MAIN_BRANDING_PATH, "utf-8");
		const mainAppName = extractAppName(mainContent, MAIN_BRANDING_PATH);
		expect(mainAppName).toBe(RENDERER_APP_NAME);
	});

	it("server branding.py exists and exports the same APP_NAME as renderer", () => {
		expect(fs.existsSync(SERVER_BRANDING_PATH)).toBe(true);
		const serverContent = fs.readFileSync(SERVER_BRANDING_PATH, "utf-8");
		const serverAppName = extractAppName(serverContent, SERVER_BRANDING_PATH);
		expect(serverAppName).toBe(RENDERER_APP_NAME);
	});

	it("all three APP_NAME values are identical (cross-language parity)", () => {
		const mainContent = fs.readFileSync(MAIN_BRANDING_PATH, "utf-8");
		const serverContent = fs.readFileSync(SERVER_BRANDING_PATH, "utf-8");
		const mainAppName = extractAppName(mainContent, MAIN_BRANDING_PATH);
		const serverAppName = extractAppName(serverContent, SERVER_BRANDING_PATH);

		// A Set with one entry means all three values are identical.
		const unique = new Set([RENDERER_APP_NAME, mainAppName, serverAppName]);
		expect(unique.size).toBe(1);
		expect([...unique][0]).toBe(RENDERER_APP_NAME);
	});

	it("documents the sync requirement via scripts/check_branding.py", () => {
		// Sanity: the canonical CI gate exists at the expected
		// path. This guards against accidental deletion of the
		// check script (which would silently disable the
		// hardcoded-string scan). The script itself owns the
		// Python-side enforcement; this test owns the TS-side
		// equality assertion.
		const checkScriptPath = path.resolve(
			REPO_ROOT,
			"scripts",
			"check_branding.py",
		);
		expect(fs.existsSync(checkScriptPath)).toBe(true);
	});
});
