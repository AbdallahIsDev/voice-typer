// Live-engine correction test — single authoritative call site for
// the per-entry "Test this entry" row action. The phrase runs through
// the SAME server IPC command (``test_vocabulary_correction`` →
// ``VocabularyManager.apply_to_text``) — the exact engine dictation
// uses. (The standalone free-text "Test corrections" panel — and its
// client-side mirror fallback, ``lib/testCorrection.ts`` — were
// removed; the per-row Test action covers the same need with one
// click.)

import type { PythonCall } from "@/hooks/usePython";

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
	call: PythonCall,
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
