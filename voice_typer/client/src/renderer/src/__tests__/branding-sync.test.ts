import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { APP_NAME as RENDERER_APP_NAME } from "@/branding";

// ── Path resolution ────────────────────────────────────────────────────
// This test file lives at:
//   <repo-root>/voice_typer/client/src/renderer/src/__tests__/branding-sync.test.ts
// so `__dirname` is `.../__tests__`. We walk up to the project root and
// back down into the other branding files.
// Trace (__dirname = .../lausu/voice_typer/client/src/renderer/src/__tests__):
//   ..   → .../lausu/voice_typer/client/src/renderer/src
//   ../.. → .../lausu/voice_typer/client/src/renderer
//   ../../.. → .../lausu/voice_typer/client/src
//   ../../../.. → .../lausu/voice_typer/client
//   ../../../../.. → .../lausu/voice_typer
//   ../../../../../.. → .../lausu  (project root)
const REPO_ROOT = path.resolve(__dirname, "..", "..", "..", "..", "..", "..");

const SERVER_BRANDING_PATH = path.resolve(
	REPO_ROOT,
	"voice_typer",
	"server",
	"branding.py",
);

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

	it("server branding.py exists and exports the same APP_NAME as renderer", () => {
		expect(fs.existsSync(SERVER_BRANDING_PATH)).toBe(true);
		const serverContent = fs.readFileSync(SERVER_BRANDING_PATH, "utf-8");
		const serverAppName = extractAppName(serverContent, SERVER_BRANDING_PATH);
		expect(serverAppName).toBe(RENDERER_APP_NAME);
	});

	it("renderer and server APP_NAME values are identical (cross-language parity)", () => {
		const serverContent = fs.readFileSync(SERVER_BRANDING_PATH, "utf-8");
		const serverAppName = extractAppName(serverContent, SERVER_BRANDING_PATH);

		// A Set with one entry means both values are identical.
		const unique = new Set([RENDERER_APP_NAME, serverAppName]);
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
