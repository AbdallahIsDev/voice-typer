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
	resolveActiveModel,
} from "@/lib/utils/models";
import type { VoiceTyperConfig } from "@/types/config";
import type { ModelStatusMap } from "@/types/ipc";

/**
 * Backend-shaped install truth for the shared resolver.
 * ``downloaded: true`` for the configured model = weights on disk.
 */
function makeStatus(downloaded: boolean): ModelStatusMap {
	return {
		tiny: { downloaded, deps_ok: true },
	};
}

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
		expect(isModelActive(makeModel("whisper", "tiny"), "whisper", "")).toBe(
			false,
		);
	});

	it("returns false for backend-keyed models (qwen/parakeet) when activeModel is empty", () => {
		// qwen / parakeet are keyed by backend alone — without the guard
		// they would render active even with an empty model_size.
		expect(isModelActive(makeModel("qwen"), "qwen", "")).toBe(false);
		expect(isModelActive(makeModel("parakeet"), "parakeet", "")).toBe(false);
	});

	it("still returns true for a real selection", () => {
		expect(isModelActive(makeModel("whisper", "tiny"), "whisper", "tiny")).toBe(
			true,
		);
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

describe("resolveActiveModel — shared no-model truth (Analytics + About)", () => {
	it("returns null/null when the configured model is empty (no model selected)", () => {
		expect(resolveActiveModel("", makeStatus(true), "cuda")).toEqual({
			model: null,
			device: null,
		});
	});

	it("returns null/null when the configured model's weights are NOT on disk", () => {
		// Config defaults to "tiny" / "cuda" even with nothing
		// installed — the resolver must NOT surface them.
		expect(resolveActiveModel("tiny", makeStatus(false), "cuda")).toEqual({
			model: null,
			device: null,
		});
	});

	it("returns null/null when the status map lacks the configured model", () => {
		expect(resolveActiveModel("tiny", {}, "cuda")).toEqual({
			model: null,
			device: null,
		});
	});

	it("returns the REAL model + device when the weights are installed", () => {
		expect(resolveActiveModel("tiny", makeStatus(true), "cuda")).toEqual({
			model: "tiny",
			device: "cuda",
		});
	});

	it("returns the real model with a null device when the config lacks one", () => {
		expect(resolveActiveModel("tiny", makeStatus(true), undefined)).toEqual({
			model: "tiny",
			device: null,
		});
	});
});
