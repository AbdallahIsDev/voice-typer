/**
 * : parity tests for the shared `DEFAULT_CONFIG` fixture.
 *
 * The hand-maintained fixture in `fixtures.ts` has historically drifted
 * from the Python `Config` dataclass defaults. The two most dangerous
 * drifts (silent test breakage) are pinned here so future contributors
 * get a loud vitest failure if they accidentally regress either value:
 *
 *   1. `schema_version` MUST match Python `_CURRENT_SCHEMA_VERSION`
 *      (currently `3`, in `voice_typer/server/config_internals/migrations.py`).
 *      A mismatch causes Python's `Config.load()` to invoke the forward-
 *      migration path on every test invocation, masking schema-bug
 *      regressions and producing flaky tests whenever migration code
 *      changes.
 *
 *   2. `llm_preset` MUST be one of the values in the Python
 *      `Literal["professional", "casual", "email", "code"]` set. The
 *      Python default is `"professional"`. An invalid value here would
 *      cause `Config.validate()` to reject the fixture once the parity
 *      test imports the Python default (TODO: parity test that imports
 *      Python Config defaults via a CI script — out of scope for this
 *      agent's file ownership).
 *
 * These tests do NOT attempt to import Python (vitest runs in Node).
 * They assert the values documented in the fixtures.ts comment block.
 * The "30+ fields" drift the finding mentions is partially intentional
 * (test-determinism overrides for `waveform_bubble`, `autostart`,
 * `volume_duck_enabled`, `noise_filter_enabled`, etc.) so we only pin
 * the two fields whose drift is unambiguously a bug.
 */
import { describe, expect, it } from "vitest";

import type { VoiceTyperConfig } from "@/types/config";

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
		// pinned fields — a test that needs an older schema
		// version (e.g. to test the migration path) must still be
		// able to override via makeConfig({ schema_version: 2 }).
		const cfg: VoiceTyperConfig = makeConfig({ schema_version: 2 });
		expect(cfg.schema_version).toBe(2);
		// Default still 3 when not overridden.
		expect(DEFAULT_CONFIG.schema_version).toBe(3);
	});

	it("makeConfig overrides llm_preset when explicitly provided", () => {
		const cfg: VoiceTyperConfig = makeConfig({ llm_preset: "email" });
		expect(cfg.llm_preset).toBe("email");
		// Default still "professional" when not overridden.
		expect(DEFAULT_CONFIG.llm_preset).toBe("professional");
	});
});
