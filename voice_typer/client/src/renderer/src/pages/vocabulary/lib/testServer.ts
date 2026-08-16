// Live-engine correction test — single authoritative call site.
//
// Both the "Test corrections" panel (free-text, debounced) and the
// per-entry "Test this entry" row action run the phrase through the
// SAME server IPC command (``test_vocabulary_correction`` →
// ``VocabularyManager.apply_to_text``) — the exact engine dictation
// uses. Centralizing the call here means the two call sites can never
// drift in what they send or how they parse the response. The
// client-side mirror (``lib/testCorrection.ts``) is a preview-only
// fallback; this module is the real engine path.

export type VocabCallFn = <T>(
	cmd: string,
	data?: Record<string, unknown>,
) => Promise<T>;

export interface ServerCorrectionResult {
	/** Corrected output from the live engine. */
	output: string;
	/** True when at least one correction was applied. */
	applied: boolean;
}

/** One row's per-entry test lifecycle (drives the inline result). */
export type EntryTestResult =
	| { status: "running" }
	| { status: "done"; output: string; applied: boolean }
	| { status: "error" };

/**
 * Run a phrase through the authoritative server correction engine
 * (``test_vocabulary_correction`` IPC → ``apply_to_text``). Throws on
 * failure — callers decide whether to fall back (panel) or surface an
 * error (per-entry row action).
 */
export async function testPhraseOnServer(
	call: VocabCallFn,
	text: string,
): Promise<ServerCorrectionResult> {
	const data = await call<{ output?: unknown; applied?: unknown }>(
		"test_vocabulary_correction",
		{ text },
	);
	return {
		output: typeof data?.output === "string" ? data.output : text,
		applied: data?.applied === true,
	};
}
