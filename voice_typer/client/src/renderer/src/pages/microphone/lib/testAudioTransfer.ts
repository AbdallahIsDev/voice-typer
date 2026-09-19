import type { PythonCall } from "@/hooks/usePython";
import type { TestAudioChunk } from "./types";

export const AUDIO_CHUNK_BYTES = 255 * 1024;

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
