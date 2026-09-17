import type { PythonCall } from "@/hooks/usePython";
import type { TestAudioChunk } from "./types";

/**
 * Binary bytes fetched per ``microphone_test_read_audio`` chunk. Each
 * response stays well under the 1 MiB IPC frame cap; a completed 10 s
 * test WAV is ~0.9 MB, so ~5 chunks per file.
 *
 * MUST be divisible by 3: client-side reassembly concatenates the
 * backend's base64 slices verbatim, so an interior unaligned length
 * would insert mid-stream ``=`` padding and corrupt every multi-chunk
 * playback. 255 KiB stays under the backend clamp (max 256 KiB) while
 * keeping the alignment invariant.
 */
export const AUDIO_CHUNK_BYTES = 255 * 1024;

/**
 * Single-flight registry: concurrent ``fetchTestAudioFile`` calls for the
 * SAME path share one in-flight promise instead of issuing parallel
 * duplicate ``microphone_test_read_audio`` request bursts (which would
 * double the slice count against the shared per-connection rate budget
 * for no benefit). Entries self-remove on settle.
 */
const _inFlightAudioFetches = new Map<string, Promise<string>>();

export function fetchTestAudioFileDeduped(
	call: PythonCall,
	path: string,
): Promise<string> {
	const existing = _inFlightAudioFetches.get(path);
	if (existing) return existing;
	const p = fetchTestAudioFile(call, path).finally(() => {
		_inFlightAudioFetches.delete(path);
	});
	_inFlightAudioFetches.set(path, p);
	return p;
}

/** Clear the single-flight registry (tests only). */
export function _resetAudioTransferForTests(): void {
	_inFlightAudioFetches.clear();
}

/**
 * Fetch a persisted mic-test WAV via the chunked file-reference IPC
 * transport and return its full base64 payload (playback keeps using
 * data URIs, so only the TRANSPORT is chunked, the assembled result
 * shape is unchanged).
 *
 * The backend persists each completed test's WAVs on disk precisely
 * because a base64 double-WAV stop payload exceeded the 1 MiB frame cap
 * and was silently dropped, leaving the 10 s recording unusable.
 */
export async function fetchTestAudioFile(
	call: PythonCall,
	path: string,
): Promise<string> {
	let offset = 0;
	const parts: string[] = [];
	// Bounded loop: total/bytes_read come from the backend; the guard
	// prevents an endless loop against a buggy server.
	for (let safety = 0; safety < 1024; safety++) {
		const res = await call<TestAudioChunk>("microphone_test_read_audio", {
			path,
			offset,
			length: AUDIO_CHUNK_BYTES,
		});
		if (!res?.success) {
			throw new Error(res?.message || "audio chunk read failed");
		}
		if (res.data_b64) parts.push(res.data_b64);
		offset += res.bytes_read || 0;
		if (res.eof || offset >= (res.total_bytes || Infinity)) break;
	}
	return parts.join("");
}
