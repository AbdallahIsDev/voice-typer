/**
 * Unit tests for the consolidated download-progress pure helpers.
 *
 * These helpers own the field mapping for `download_progress` events,
 * the progress-reset field set, and the React `Object.is` bailout that
 * the single-state consolidation depends on.
 */
import { describe, expect, it } from "vitest";

import {
	applyDownloadPatch,
	buildDownloadProgressPatch,
	type DownloadState,
	INITIAL_DOWNLOAD_STATE,
	withResetProgress,
} from "@/hooks/models/downloadState";

function makeState(overrides: Partial<DownloadState> = {}): DownloadState {
	return { ...INITIAL_DOWNLOAD_STATE, ...overrides };
}

describe("INITIAL_DOWNLOAD_STATE", () => {
	it("starts with no active download and null byte/ETA fields", () => {
		expect(INITIAL_DOWNLOAD_STATE).toEqual({
			downloadingModel: null,
			downloadProgress: 0,
			downloadStatus: "",
			isPaused: false,
			downloadedBytes: null,
			totalBytes: null,
			speedBps: null,
			etaSeconds: null,
			failedDownload: null,
		});
	});
});

describe("withResetProgress", () => {
	it("zeroes progress fields while preserving model and failure", () => {
		const prev = makeState({
			downloadingModel: "tiny.en",
			downloadProgress: 42,
			downloadStatus: "downloading",
			isPaused: true,
			downloadedBytes: 1024,
			totalBytes: 2048,
			speedBps: 100,
			etaSeconds: 10,
			failedDownload: { modelName: "other", error: "boom" },
		});
		expect(withResetProgress(prev)).toEqual({
			downloadingModel: "tiny.en",
			downloadProgress: 0,
			downloadStatus: "",
			isPaused: false,
			downloadedBytes: null,
			totalBytes: null,
			speedBps: null,
			etaSeconds: null,
			failedDownload: { modelName: "other", error: "boom" },
		});
	});

	it("does not mutate the previous state object", () => {
		const prev = makeState({ downloadProgress: 42, downloadStatus: "x" });
		const next = withResetProgress(prev);
		expect(prev.downloadProgress).toBe(42);
		expect(next).not.toBe(prev);
	});
});

describe("buildDownloadProgressPatch", () => {
	it("returns null for missing or unrecognized payloads", () => {
		expect(buildDownloadProgressPatch(undefined)).toBeNull();
		expect(buildDownloadProgressPatch({})).toBeNull();
		expect(buildDownloadProgressPatch({ unrelated: 1 })).toBeNull();
	});

	it("maps recognized numeric/string fields onto DownloadState keys", () => {
		expect(
			buildDownloadProgressPatch({
				progress: 50,
				status: "downloading",
				downloaded_bytes: 100,
				total_bytes: 200,
				speed_bytes_per_sec: 10,
				eta_seconds: 20,
				paused: true,
			}),
		).toEqual({
			downloadProgress: 50,
			downloadStatus: "downloading",
			downloadedBytes: 100,
			totalBytes: 200,
			speedBps: 10,
			etaSeconds: 20,
			isPaused: true,
		});
	});

	it("ignores wrong-typed values instead of writing them", () => {
		const patch = buildDownloadProgressPatch({
			progress: "50",
			status: 1,
			downloaded_bytes: "100",
			paused: "yes",
		});
		expect(patch).toBeNull();
	});

	it("clears speed/ETA only on transitions (status/paused/resumed)", () => {
		// Partial event without a transition: absent speed/ETA means
		// "not re-measured", not "reset".
		expect(
			buildDownloadProgressPatch({ progress: 75, downloaded_bytes: 10 }),
		).toEqual({ downloadProgress: 75, downloadedBytes: 10 });

		// Status change with absent speed/ETA clears them.
		expect(buildDownloadProgressPatch({ status: "paused" })).toEqual({
			downloadStatus: "paused",
			speedBps: null,
			etaSeconds: null,
		});

		// Lone paused flag also counts as a transition.
		expect(buildDownloadProgressPatch({ paused: true })).toEqual({
			isPaused: true,
			speedBps: null,
			etaSeconds: null,
		});
	});

	it("treats resumed === true as a transition and clears isPaused", () => {
		expect(buildDownloadProgressPatch({ resumed: true })).toEqual({
			isPaused: false,
			speedBps: null,
			etaSeconds: null,
		});
	});

	it("does not treat resumed === false as a transition (no speed/ETA wipe)", () => {
		// Only `resumed === true` counts as a transition; a false value
		// produces an empty patch so the live speed/ETA readout survives.
		expect(buildDownloadProgressPatch({ resumed: false })).toBeNull();
	});
});

describe("applyDownloadPatch", () => {
	it("returns the previous reference when nothing changed (React bailout)", () => {
		const prev = makeState({ downloadProgress: 10 });
		const next = applyDownloadPatch(prev, { downloadProgress: 10 });
		expect(next).toBe(prev);
	});

	it("returns a new object when a patched field differs", () => {
		const prev = makeState({ downloadProgress: 10 });
		const next = applyDownloadPatch(prev, { downloadProgress: 20 });
		expect(next).not.toBe(prev);
		expect(next.downloadProgress).toBe(20);
		expect(next.downloadingModel).toBe(prev.downloadingModel);
	});

	it("treats NaN as changed against a number (Object.is semantics)", () => {
		const prev = makeState({ downloadProgress: 0 });
		const next = applyDownloadPatch(prev, { downloadProgress: Number.NaN });
		expect(next).not.toBe(prev);
	});
});
