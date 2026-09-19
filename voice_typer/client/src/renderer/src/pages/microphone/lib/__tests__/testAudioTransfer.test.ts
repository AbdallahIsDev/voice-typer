/**
 * Unit tests for the chunked mic-test audio transport.
 * Pins the 3-byte-aligned IPC slice size (C-MIC-21), the bounded
 * multi-chunk assembly loop, failure propagation, and the single-flight
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PythonCall } from "@/hooks/usePython";
import {
	_resetAudioTransferForTests,
	AUDIO_CHUNK_BYTES,
	fetchTestAudioFile,
	fetchTestAudioFileDeduped,
} from "@/pages/microphone/lib/testAudioTransfer";
import type { TestAudioChunk } from "@/pages/microphone/lib/types";

function chunk(partial: Partial<TestAudioChunk> = {}): TestAudioChunk {
	return {
		success: true,
		data_b64: "",
		bytes_read: 0,
		total_bytes: 0,
		eof: false,
		message: "",
		...partial,
	};
}

beforeEach(() => {
	_resetAudioTransferForTests();
});

afterEach(() => {
	_resetAudioTransferForTests();
});

describe("AUDIO_CHUNK_BYTES", () => {
	it("stays under the 256 KiB backend clamp and is 3-byte-aligned", () => {
		// Interior fragments are concatenated as base64 without padding,
		// so the binary slice length must be divisible by 3.
		expect(AUDIO_CHUNK_BYTES).toBe(255 * 1024);
		expect(AUDIO_CHUNK_BYTES).toBeLessThanOrEqual(256 * 1024);
		expect(AUDIO_CHUNK_BYTES % 3).toBe(0);
	});
});

describe("fetchTestAudioFile", () => {
	it("assembles multi-chunk responses into one base64 string", async () => {
		const call = vi.fn(
			async (_type: string, data?: Record<string, unknown>) => {
				const offset = Number(data?.offset ?? 0);
				if (offset === 0) {
					return chunk({
						data_b64: "AAAA",
						bytes_read: 3,
						total_bytes: 6,
						eof: false,
					});
				}
				return chunk({
					data_b64: "BBBB",
					bytes_read: 3,
					total_bytes: 6,
					eof: true,
				});
			},
		) as unknown as PythonCall;

		const result = await fetchTestAudioFile(call, "/tmp/a.wav");
		expect(result).toBe("AAAABBBB");
		expect(call).toHaveBeenCalledTimes(2);
		expect(call).toHaveBeenNthCalledWith(
			1,
			"microphone_test_read_audio",
			expect.objectContaining({ path: "/tmp/a.wav", offset: 0 }),
		);
		expect(call).toHaveBeenNthCalledWith(
			2,
			"microphone_test_read_audio",
			expect.objectContaining({ path: "/tmp/a.wav", offset: 3 }),
		);
	});

	it("stops when offset reaches total_bytes even without eof", async () => {
		const call = vi.fn(async () =>
			chunk({
				data_b64: "XXXX",
				bytes_read: 4,
				total_bytes: 4,
				eof: false,
			}),
		) as unknown as PythonCall;

		expect(await fetchTestAudioFile(call, "/tmp/b.wav")).toBe("XXXX");
		expect(call).toHaveBeenCalledTimes(1);
	});

	it("skips empty base64 payloads while still advancing the offset", async () => {
		const call = vi.fn(
			async (_type: string, data?: Record<string, unknown>) => {
				const offset = Number(data?.offset ?? 0);
				if (offset === 0) {
					return chunk({
						data_b64: "CC",
						bytes_read: 2,
						total_bytes: 3,
						eof: false,
					});
				}
				return chunk({
					data_b64: "",
					bytes_read: 1,
					total_bytes: 3,
					eof: true,
				});
			},
		) as unknown as PythonCall;

		expect(await fetchTestAudioFile(call, "/tmp/c.wav")).toBe("CC");
		expect(call).toHaveBeenCalledTimes(2);
	});

	it("throws the backend message on a failed slice", async () => {
		const call = vi.fn(async () =>
			chunk({ success: false, message: "file missing" }),
		) as unknown as PythonCall;

		await expect(fetchTestAudioFile(call, "/tmp/missing.wav")).rejects.toThrow(
			"file missing",
		);
	});

	it("throws a generic error when the failure has no message", async () => {
		const call = vi.fn(async () =>
			chunk({ success: false, message: "" }),
		) as unknown as PythonCall;

		await expect(fetchTestAudioFile(call, "/tmp/x.wav")).rejects.toThrow(
			"audio chunk read failed",
		);
	});
});

describe("fetchTestAudioFileDeduped", () => {
	it("shares one in-flight request burst for the same path", async () => {
		let resolveFirst!: (c: TestAudioChunk) => void;
		const call = vi.fn(
			() =>
				new Promise<TestAudioChunk>((resolve) => {
					resolveFirst = resolve;
				}),
		) as unknown as PythonCall;

		const p1 = fetchTestAudioFileDeduped(call, "/tmp/shared.wav");
		const p2 = fetchTestAudioFileDeduped(call, "/tmp/shared.wav");
		expect(p1).toBe(p2);
		expect(call).toHaveBeenCalledTimes(1);

		resolveFirst(
			chunk({ data_b64: "ZZ", bytes_read: 2, total_bytes: 2, eof: true }),
		);
		await expect(Promise.all([p1, p2])).resolves.toEqual(["ZZ", "ZZ"]);
	});

	it("issues separate fetches for different paths", async () => {
		const call = vi.fn(async () =>
			chunk({ data_b64: "A", bytes_read: 1, total_bytes: 1, eof: true }),
		) as unknown as PythonCall;

		await Promise.all([
			fetchTestAudioFileDeduped(call, "/tmp/one.wav"),
			fetchTestAudioFileDeduped(call, "/tmp/two.wav"),
		]);
		expect(call).toHaveBeenCalledTimes(2);
	});

	it("clears the registry after settle so a later fetch re-requests", async () => {
		const call = vi.fn(async () =>
			chunk({ data_b64: "A", bytes_read: 1, total_bytes: 1, eof: true }),
		) as unknown as PythonCall;

		await fetchTestAudioFileDeduped(call, "/tmp/re.wav");
		await fetchTestAudioFileDeduped(call, "/tmp/re.wav");
		expect(call).toHaveBeenCalledTimes(2);
	});

	it("rethrows from the shared promise and clears the entry", async () => {
		const call = vi
			.fn()
			.mockRejectedValueOnce(new Error("ipc down"))
			.mockResolvedValueOnce(
				chunk({ data_b64: "OK", bytes_read: 2, total_bytes: 2, eof: true }),
			) as unknown as PythonCall;

		await expect(
			fetchTestAudioFileDeduped(call, "/tmp/fail.wav"),
		).rejects.toThrow("ipc down");
		await expect(
			fetchTestAudioFileDeduped(call, "/tmp/fail.wav"),
		).resolves.toBe("OK");
	});
});
