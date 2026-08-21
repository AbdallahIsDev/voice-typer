/**
 * Unit tests for the display-layer model naming helpers added in the
 * UI/UX overhaul (point 5):
 *   • `formatModelDisplayName` — hyphenated internal slug →
 *     human-readable capitalized label (hyphens treated as word
 *     separators for display ONLY; the slug itself is untouched);
 *   • `getModelVariantDisplayName` — backend display_name priority +
 *     the Whisper family prefix ("Whisper Tiny", "Whisper Large V3").
 *   • `requiresHuggingFaceConsent` — which models are gated on HF
 *     download consent (point 4).
 */
import { describe, expect, it } from "vitest";
import {
	formatModelDisplayName,
	formatModelSize,
	getModelVariantDisplayName,
	type ModelInfo,
	requiresHuggingFaceConsent,
} from "@/lib/utils/models";

function makeModel(name: string, backend: string): ModelInfo {
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

describe("formatModelDisplayName — slug → display label (display-layer only)", () => {
	it("capitalizes single-word slugs", () => {
		expect(formatModelDisplayName("tiny")).toBe("Tiny");
		expect(formatModelDisplayName("qwen")).toBe("Qwen");
		expect(formatModelDisplayName("parakeet")).toBe("Parakeet");
	});

	it("treats each hyphen as a word separator and Title-Cases each word", () => {
		expect(formatModelDisplayName("large-v3")).toBe("Large V3");
		expect(formatModelDisplayName("large-v3-turbo")).toBe("Large V3 Turbo");
		expect(formatModelDisplayName("whisper-1")).toBe("Whisper 1");
		expect(formatModelDisplayName("nova-2")).toBe("Nova 2");
	});

	it("never alters the raw slug (presentation-only formatting)", () => {
		const slug = "large-v3-turbo";
		formatModelDisplayName(slug);
		expect(slug).toBe("large-v3-turbo");
	});

	it("handles edge cases (empty / consecutive hyphens)", () => {
		expect(formatModelDisplayName("")).toBe("");
		expect(formatModelDisplayName("a--b")).toBe("A B");
	});
});

describe("getModelVariantDisplayName — family prefix + display_name priority", () => {
	it("prepends 'Whisper' to formatted whisper-variant slugs", () => {
		expect(getModelVariantDisplayName(makeModel("tiny", "whisper"))).toBe(
			"Whisper Tiny",
		);
		expect(getModelVariantDisplayName(makeModel("large-v3", "whisper"))).toBe(
			"Whisper Large V3",
		);
		expect(
			getModelVariantDisplayName(makeModel("large-v3-turbo", "whisper")),
		).toBe("Whisper Large V3 Turbo");
	});

	it("keeps the backend display_name as the top priority (no double prefix)", () => {
		expect(
			getModelVariantDisplayName(makeModel("parakeet", "parakeet"), {
				display_name: "Parakeet-TDT-0.6b-V3",
			} as never),
		).toBe("Parakeet-TDT-0.6b-V3");
		expect(
			getModelVariantDisplayName(makeModel("qwen", "qwen"), {
				display_name: "Qwen-3",
			} as never),
		).toBe("Qwen-3");
	});

	it("formats non-whisper slugs without a family prefix", () => {
		expect(getModelVariantDisplayName(makeModel("qwen", "qwen"))).toBe("Qwen");
	});
});

describe("requiresHuggingFaceConsent — JIT consent gate scope (point 4)", () => {
	it("gates whisper + parakeet (HF-downloading models)", () => {
		expect(requiresHuggingFaceConsent(makeModel("tiny", "whisper"))).toBe(true);
		expect(
			requiresHuggingFaceConsent(makeModel("large-v3", "distil-whisper")),
		).toBe(true);
		expect(requiresHuggingFaceConsent(makeModel("parakeet", "parakeet"))).toBe(
			true,
		);
	});

	it("does NOT gate qwen (local-only — nothing phones home)", () => {
		expect(requiresHuggingFaceConsent(makeModel("qwen", "qwen"))).toBe(false);
	});
});

describe("formatModelSize — canonical `<number> <UNIT>` display (2026-08-21)", () => {
	it("strips the ~ approximation marker and inserts the number/unit space", () => {
		expect(formatModelSize("~75MB")).toBe("75 MB");
		expect(formatModelSize("~3GB")).toBe("3 GB");
		expect(formatModelSize("~809MB")).toBe("809 MB");
		expect(formatModelSize("~2.5GB")).toBe("2.5 GB");
	});

	it("normalizes an already-canonical size (no-op) and the ≈ marker", () => {
		expect(formatModelSize("75 MB")).toBe("75 MB");
		expect(formatModelSize("≈809MB")).toBe("809 MB");
	});

	it("uppercases the unit and trims surrounding whitespace", () => {
		expect(formatModelSize(" 3gb ")).toBe("3 GB");
		expect(formatModelSize("999TB")).toBe("999 TB");
		// Generic for arbitrary providers — supports a potential TB size.
		expect(formatModelSize("~1.2TB")).toBe("1.2 TB");
	});

	it("keeps the Variable sentinel translated", () => {
		expect(formatModelSize("Variable")).toBe("Variable");
	});

	it("passes through unrecognized strings untouched", () => {
		expect(formatModelSize("custom-path")).toBe("custom-path");
	});
});
