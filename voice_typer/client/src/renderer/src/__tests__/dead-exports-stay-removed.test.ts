/**
 * Dead-export "stays removed" guard (renderer + main + Rust host).
 * Mirrors the Python-side guard pattern
 * (`tests/test_dead_code_stays_removed.py`): when a dead symbol, module,
 *    duration is fixed at 10s elsewhere (C-MIC-18).
 */

import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const RENDERER_SRC = resolve(__dirname, "..");
const CLIENT_SRC = resolve(RENDERER_SRC, "../..");
const REPO_ROOT = resolve(CLIENT_SRC, "../../..");

function readRenderer(relPath: string): string {
	return readFileSync(resolve(RENDERER_SRC, relPath), "utf8");
}

describe("dead exports stay removed, renderer hooks/components", () => {
	it("useModelDownload has no installDeps flow (phantom install_parakeet_deps IPC)", () => {
		const src = readRenderer("hooks/models/useModelDownload.ts");
		expect(src).not.toContain("installDeps");
		expect(src).not.toContain("installingDepsModel");
		expect(src).not.toContain("install_parakeet_deps");
	});

	it("ModelCardActions has no deps-install branch or props", () => {
		const src = readRenderer("components/models/ModelCardActions.tsx");
		expect(src).not.toContain("onInstallDeps");
		expect(src).not.toContain("isInstallingDepsThis");
		expect(src).not.toContain("anyInstallingDeps");
		expect(src).not.toContain("depsInstallable");
		expect(src).not.toContain("models.download.deps");
	});

	it("the semver module and its test stay deleted", () => {
		expect(existsSync(resolve(RENDERER_SRC, "lib/semver.ts"))).toBe(false);
		expect(
			existsSync(resolve(RENDERER_SRC, "lib/__tests__/semver.test.ts")),
		).toBe(false);
	});

	it("onboarding constants no longer define the dead 5s mic-test duration", () => {
		const src = readRenderer("pages/onboarding/lib/constants.ts");
		expect(src).not.toContain("ONBOARDING_MIC_TEST_DURATION_SEC");
	});
});

describe("dead exports stay removed, Rust host", () => {
	// NOTE: the predecessor main-process tree (src/main, including
	// tray_available.ts / refreshTrayAvailableCache) was deleted with
	// the predecessor shell; that assertion is gone with it.

	it("export.rs has no allocation-returning csv_escape twin (production uses csv_escape_into)", () => {
		const src = readFileSync(
			resolve(REPO_ROOT, "src-tauri/src/commands/export.rs"),
			"utf8",
		);
		// The exact-twin signature, must not match `fn csv_escape_into(`.
		expect(src).not.toContain("fn csv_escape(s: &str) -> String");
		expect(src).not.toContain("#[allow(dead_code)]");
		// The production escape path must still exist.
		expect(src).toContain("fn csv_escape_into(out: &mut String, s: &str)");
	});

	it("export_tests.rs exercises the escape behavior through csv_escape_into only", () => {
		const src = readFileSync(
			resolve(REPO_ROOT, "src-tauri/src/commands/export_tests.rs"),
			"utf8",
		);
		expect(src).not.toMatch(/use super::\{[^}]*\bcsv_escape, /s);
		expect(src).not.toMatch(/assert_eq!\(csv_escape\(/);
	});
});

describe("dead exports stay removed, orphaned i18n keys (all 8 locales)", () => {
	const LOCALES = ["ar", "de", "en", "es", "fr", "hi", "ru", "zh"] as const;

	it.each(LOCALES)(
		"models deps-flow keys are gone and live keys remain (%s.json)",
		(loc) => {
			const raw = readRenderer(`i18n/translations/${loc}.json`);
			const models = JSON.parse(raw).models as Record<
				string,
				Record<string, unknown>
			>;
			// Orphaned by the flow removal, deleted from every locale.
			expect(
				models.snack,
				`${loc}: models.snack.depsInstalled`,
			).not.toHaveProperty("depsInstalled");
			expect(
				models.download,
				`${loc}: models.download.deps`,
			).not.toHaveProperty("deps");
			expect(
				models.download,
				`${loc}: models.download.depsAria`,
			).not.toHaveProperty("depsAria");
			// LIVE keys that must never be swept up in a future cleanup.
			expect(
				models.snack,
				`${loc}: models.snack.depsRequiredName`,
			).toHaveProperty("depsRequiredName");
			expect(
				models.download,
				`${loc}: models.download.oneAtATime`,
			).toHaveProperty("oneAtATime");
		},
	);
});
