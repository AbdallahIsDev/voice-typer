/**
 * Unit tests for the Home low-confidence detector (lib/quality.ts).
 *
 * Pins the exact contract `LastTranscriptionPreview` relies on:
 *   - A missing / null summary never warns (engines without per-segment
 *     stats are treated as "unknown confidence", not "low confidence").
 *   - Each signal is independent (the summary may be PARTIAL) and the
 *     result is their disjunction.
 *   - Thresholds are strict: mean_logprob == -1.0 and
 *     no_speech_prob_max == 0.6 sit exactly ON the boundary and do NOT
 *     warn.
 *   - Absent or non-finite fields never trigger a warning by
 *     themselves (NaN / ±Infinity / wrong types behave like "field
 *     absent").
 */
import { describe, expect, it } from "vitest";

import type { TranscriptionQualitySummary } from "@/types/ipc";

import {
	isLowConfidenceQuality,
	LOW_CONFIDENCE_MEAN_LOGPROB_THRESHOLD,
	LOW_CONFIDENCE_NO_SPEECH_PROB_THRESHOLD,
} from "../quality";

describe("isLowConfidenceQuality", () => {
	it("returns false for null and undefined summaries", () => {
		expect(isLowConfidenceQuality(null)).toBe(false);
		expect(isLowConfidenceQuality(undefined)).toBe(false);
	});

	it("returns false for an empty summary object", () => {
		expect(isLowConfidenceQuality({})).toBe(false);
	});

	it("exposes the documented thresholds", () => {
		expect(LOW_CONFIDENCE_MEAN_LOGPROB_THRESHOLD).toBe(-1.0);
		expect(LOW_CONFIDENCE_NO_SPEECH_PROB_THRESHOLD).toBe(0.6);
	});

	describe("mean_logprob signal", () => {
		it("flags below the threshold", () => {
			expect(isLowConfidenceQuality({ mean_logprob: -1.0001 })).toBe(true);
			expect(isLowConfidenceQuality({ mean_logprob: -2.5 })).toBe(true);
		});

		it("does not flag exactly at the threshold", () => {
			expect(
				isLowConfidenceQuality({
					mean_logprob: LOW_CONFIDENCE_MEAN_LOGPROB_THRESHOLD,
				}),
			).toBe(false);
		});

		it("does not flag confident decodings above the threshold", () => {
			expect(isLowConfidenceQuality({ mean_logprob: -1.0 })).toBe(false);
			expect(isLowConfidenceQuality({ mean_logprob: -0.5 })).toBe(false);
			expect(isLowConfidenceQuality({ mean_logprob: 0 })).toBe(false);
		});
	});

	describe("no_speech_prob_max signal", () => {
		it("flags above the threshold", () => {
			expect(isLowConfidenceQuality({ no_speech_prob_max: 0.6001 })).toBe(true);
			expect(isLowConfidenceQuality({ no_speech_prob_max: 0.9 })).toBe(true);
			expect(isLowConfidenceQuality({ no_speech_prob_max: 1 })).toBe(true);
		});

		it("does not flag exactly at the threshold", () => {
			expect(
				isLowConfidenceQuality({
					no_speech_prob_max: LOW_CONFIDENCE_NO_SPEECH_PROB_THRESHOLD,
				}),
			).toBe(false);
		});

		it("does not flag speech-like probabilities below the threshold", () => {
			expect(isLowConfidenceQuality({ no_speech_prob_max: 0.6 })).toBe(false);
			expect(isLowConfidenceQuality({ no_speech_prob_max: 0.3 })).toBe(false);
			expect(isLowConfidenceQuality({ no_speech_prob_max: 0 })).toBe(false);
		});
	});

	describe("combined signals", () => {
		it("flags when either signal alone crosses its threshold", () => {
			expect(
				isLowConfidenceQuality({ mean_logprob: -1.4, no_speech_prob_max: 0.1 }),
			).toBe(true);
			expect(
				isLowConfidenceQuality({ mean_logprob: -0.2, no_speech_prob_max: 0.8 }),
			).toBe(true);
		});

		it("flags when both signals cross their thresholds", () => {
			expect(
				isLowConfidenceQuality({
					mean_logprob: -3.2,
					no_speech_prob_max: 0.95,
				}),
			).toBe(true);
		});

		it("does not flag when both signals stay within thresholds", () => {
			expect(
				isLowConfidenceQuality({ mean_logprob: -0.7, no_speech_prob_max: 0.4 }),
			).toBe(false);
		});
	});

	describe("partial objects", () => {
		it("ignores a missing field while checking the present one", () => {
			expect(isLowConfidenceQuality({ no_speech_prob_max: 0.99 })).toBe(true);
			expect(isLowConfidenceQuality({ mean_logprob: -9.9 })).toBe(true);
			expect(isLowConfidenceQuality({ no_speech_prob_max: 0.1 })).toBe(false);
			expect(isLowConfidenceQuality({ mean_logprob: -0.1 })).toBe(false);
		});

		it("carries unrelated extra fields without effect", () => {
			expect(
				isLowConfidenceQuality({
					min_logprob: -12,
					segments: 3,
				} as TranscriptionQualitySummary),
			).toBe(false);
		});
	});

	describe("non-finite and non-numeric values", () => {
		it("treats NaN like an absent field", () => {
			expect(isLowConfidenceQuality({ mean_logprob: Number.NaN })).toBe(false);
			expect(isLowConfidenceQuality({ no_speech_prob_max: Number.NaN })).toBe(
				false,
			);
		});

		it("treats Infinity / -Infinity like an absent field", () => {
			expect(isLowConfidenceQuality({ mean_logprob: -Infinity })).toBe(false);
			expect(isLowConfidenceQuality({ mean_logprob: Infinity })).toBe(false);
			expect(isLowConfidenceQuality({ no_speech_prob_max: Infinity })).toBe(
				false,
			);
		});

		it("does not warn when every field is non-finite at once", () => {
			expect(
				isLowConfidenceQuality({
					mean_logprob: Number.NaN,
					no_speech_prob_max: Number.NaN,
				}),
			).toBe(false);
		});
	});
});
