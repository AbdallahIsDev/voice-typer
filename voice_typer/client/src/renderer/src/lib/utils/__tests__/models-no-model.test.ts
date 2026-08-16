/**
 * Client-side "no model selected" active-state tests.
 *
 * `model_size === ""` is the backend's `NO_MODEL_SIZE` sentinel: the
 * user has no active model. `isModelActive` must return false for
 * EVERY model (including qwen / parakeet, whose active check is
 * backend-keyed and would otherwise light up), `applyActiveState` must
 * clear all active flags, and `getActiveFamilyId` must return null.
 */
import { describe, expect, it } from "vitest";

import {
	applyActiveState,
	getActiveFamilyId,
	isModelActive,
	type ModelInfo,
} from "@/lib/utils/models";
import type { VoiceTyperConfig } from "@/types/config";

function makeModel(backend: string, name = backend): ModelInfo {
	return {
		name,
		size: "~1MB",
		speed: "Fast",
		backend,
		downloaded: false,
		depsOk: true,
		isActive: false,
	};
}

function makeConfig(model_size: string): VoiceTyperConfig {
	return {
		model_size,
		asr_backend: "whisper",
	} as VoiceTyperConfig;
}

describe("isModelActive — empty model_size means nothing is active", () => {
	it("returns false for whisper models when activeModel is empty", () => {
		expect(
			isModelActive(makeModel("whisper", "tiny"), "whisper", ""),
		).toBe(false);
	});

	it("returns false for backend-keyed models (qwen/parakeet) when activeModel is empty", () => {
		// qwen / parakeet are keyed by backend alone — without the guard
		// they would render active even with an empty model_size.
		expect(isModelActive(makeModel("qwen"), "qwen", "")).toBe(false);
		expect(isModelActive(makeModel("parakeet"), "parakeet", "")).toBe(
			false,
		);
	});

	it("still returns true for a real selection", () => {
		expect(
			isModelActive(makeModel("whisper", "tiny"), "whisper", "tiny"),
		).toBe(true);
	});
});

describe("applyActiveState / getActiveFamilyId with no model selected", () => {
	it("clears every active flag when model_size is empty", () => {
		const models = [
			makeModel("whisper", "tiny"),
			makeModel("qwen"),
			makeModel("parakeet"),
		];
		const applied = applyActiveState(models, makeConfig(""));
		expect(applied.every((m) => !m.isActive)).toBe(true);
	});

	it("getActiveFamilyId returns null when model_size is empty", () => {
		expect(getActiveFamilyId(makeConfig(""))).toBeNull();
	});
});
