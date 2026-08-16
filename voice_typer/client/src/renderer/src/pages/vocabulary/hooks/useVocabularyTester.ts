// "Test corrections" preview engine.
//
// The preview is run against the LIVE backend vocabulary rules via the
// ``test_vocabulary_correction`` IPC command (which calls
// ``VocabularyManager.apply_to_text`` — the exact engine dictation
// uses), so the panel can never drift from production behavior. The
// call is debounced (~180ms) so a fast typist doesn't fire one IPC
// round-trip per keystroke.
//
// Fallback: if the backend round-trip fails (bridge briefly offline,
// backend restarting), the client-side mirror in
// ``lib/testCorrection.ts`` is used so the panel never dead-ends — the
// mirror is a faithful port and the authoritative pass still runs
// server-side during dictation.

import { useEffect, useState } from "react";

import type { VocabularyEntry } from "@/types/ipc";
import { applyCorrections } from "../lib/testCorrection";

type CallFn = <T>(cmd: string, data?: Record<string, unknown>) => Promise<T>;

interface UseVocabularyTesterArgs {
	call: CallFn;
	entries: ReadonlyArray<VocabularyEntry>;
	query: string;
}

interface UseVocabularyTesterResult {
	/** Corrected output for the current query ("" when idle). */
	output: string;
	/** True when at least one correction was applied. */
	applied: boolean;
	/** True while waiting for the debounced backend round-trip. */
	pending: boolean;
	/** True when the backend call failed and the client mirror was used. */
	usingFallback: boolean;
}

const DEBOUNCE_MS = 180;

export function useVocabularyTester({
	call,
	entries,
	query,
}: UseVocabularyTesterArgs): UseVocabularyTesterResult {
	const [result, setResult] = useState<{ output: string; applied: boolean }>({
		output: "",
		applied: false,
	});
	const [pending, setPending] = useState(false);
	const [usingFallback, setUsingFallback] = useState(false);

	useEffect(() => {
		const trimmed = query.trim();
		if (!trimmed) {
			setResult({ output: "", applied: false });
			setPending(false);
			return;
		}
		setPending(true);
		let cancelled = false;
		const timer = setTimeout(async () => {
			try {
				const data = await call<{
					output?: unknown;
					applied?: unknown;
				}>("test_vocabulary_correction", { text: trimmed });
				if (cancelled) return;
				setResult({
					output: typeof data?.output === "string" ? data.output : trimmed,
					applied: data?.applied === true,
				});
				setUsingFallback(false);
			} catch (err) {
				console.warn(
					"[renderer:useVocabularyTester] backend test failed, using client mirror:",
					err,
				);
				if (cancelled) return;
				const mirror = applyCorrections(trimmed, entries);
				setResult(mirror);
				setUsingFallback(true);
			} finally {
				if (!cancelled) setPending(false);
			}
		}, DEBOUNCE_MS);
		return () => {
			cancelled = true;
			clearTimeout(timer);
		};
	}, [query, call, entries]);

	// The fallback path is silent in the UI (the mirror output is
	// correct in practice); the flag is exposed for tests/diagnostics.
	return {
		output: result.output,
		applied: result.applied,
		pending,
		usingFallback,
	};
}
