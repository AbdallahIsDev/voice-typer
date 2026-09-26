import { describe, expect, it } from "vitest";

import type { LausuConfig } from "@/types/config";

import { DEFAULT_CONFIG, makeConfig } from "../fixtures";

describe("DEFAULT_CONFIG (XZ-CFG-05 drift pin)", () => {
	it("schema_version matches Python _CURRENT_SCHEMA_VERSION (3)", () => {
		// Mirror of Python `config_internals/migrations.py:38`.
		// Update BOTH this constant and the comment block in
		// `fixtures.ts` when Python's schema version bumps.
		const PYTHON_CURRENT_SCHEMA_VERSION = 3;
		expect(DEFAULT_CONFIG.schema_version).toBe(PYTHON_CURRENT_SCHEMA_VERSION);
	});

	it("llm_preset is a valid Python Literal value", () => {
		// Mirror of Python `config.py:590` Literal set + default.
		const VALID_LLM_PRESETS = [
			"professional",
			"casual",
			"email",
			"code",
		] as const;
		const PYTHON_DEFAULT_LLM_PRESET = "professional";
		expect(VALID_LLM_PRESETS).toContain(DEFAULT_CONFIG.llm_preset);
		expect(DEFAULT_CONFIG.llm_preset).toBe(PYTHON_DEFAULT_LLM_PRESET);
	});

	it("makeConfig overrides schema_version when explicitly provided", () => {
		// Sanity check that the override mechanism works for the
		// pinned fields, a test that needs an older schema
		// version (e.g. to test the migration path) must still be
		// able to override via makeConfig({ schema_version: 2 }).
		const cfg: LausuConfig = makeConfig({ schema_version: 2 });
		expect(cfg.schema_version).toBe(2);
		// Default still 3 when not overridden.
		expect(DEFAULT_CONFIG.schema_version).toBe(3);
	});

	it("makeConfig overrides llm_preset when explicitly provided", () => {
		const cfg: LausuConfig = makeConfig({ llm_preset: "email" });
		expect(cfg.llm_preset).toBe("email");
		// Default still "professional" when not overridden.
		expect(DEFAULT_CONFIG.llm_preset).toBe("professional");
	});
});
