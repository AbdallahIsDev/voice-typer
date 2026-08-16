// Client-side mirror of the server's vocabulary application rules
// (voice_typer/server/vocabulary.py `VocabularyManager.apply_to_text`),
// used by the "Test corrections" panel on the Vocabulary page so users
// can verify an entry fires WITHOUT sending anything to the backend.
//
// The mirror reproduces the server's two-stage semantics exactly:
//   1. Phrase-level (phrase_corrections, extra_word_patterns):
//      case-insensitive, literal (non-regex) replacement, longer
//      phrases applied first (server compiles `re.escape(original)`
//      with IGNORECASE and substitutes the correction verbatim — no
//      word boundaries, so mid-word matches fire too).
//   2. Word-level (misspellings, technical_terms, names, products):
//      split on single spaces, strip leading/trailing non-word chars,
//      lowercase, dict lookup; on match, re-wrap the correction in the
//      original token's leading/trailing punctuation.
//
// Note: this is a faithful approximation of the SERVER's semantics for
// preview purposes. The authoritative correction pass still runs
// server-side during dictation; if the two ever diverge, the server
// wins.
import type { VocabularyEntry } from "@/types/ipc";

const PHRASE_CATEGORIES = new Set([
	"phrase_corrections",
	"extra_word_patterns",
]);

// `^\W+|\W+$` — strip leading/trailing non-word chars (mirrors
// `_RE_TOKEN_KEY` in server text_cleanup.py). \W = not [A-Za-z0-9_].
const TOKEN_KEY = /^\W+|\W+$/g;
// `^(\W*)(\w+)(\W*)$` — captures the punctuation wrap around a token
// so a correction can be re-wrapped (mirrors `_RE_MISSPELL_WRAP`).
const TOKEN_WRAP = /^(\W*)(\w+)(\W*)$/;

function escapeRegExp(s: string): string {
	return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Apply vocabulary entries to text using the same semantics as the
 * server. Returns the corrected output + whether anything changed.
 */
export function applyCorrections(
	text: string,
	entries: ReadonlyArray<VocabularyEntry>,
): { output: string; applied: boolean } {
	if (!text || entries.length === 0) {
		return { output: text, applied: false };
	}

	let output = text;

	// Stage 1 — phrase-level, longer first (server sorts by length desc).
	const phrases = entries
		.filter((e) => PHRASE_CATEGORIES.has(e.category) && e.original)
		.sort((a, b) => b.original.length - a.original.length);
	for (const p of phrases) {
		// `split(...).join(good)` performs a literal, case-insensitive
		// global replace with NO backreference interpretation (mirrors
		// the server's `pattern.sub(lambda _m, _g=good: _g, text)`).
		output = output
			.split(new RegExp(escapeRegExp(p.original), "gi"))
			.join(p.correction);
	}

	// Stage 2 — word-level, tokenized.
	const wordDict = new Map<string, VocabularyEntry>();
	for (const e of entries) {
		if (!PHRASE_CATEGORIES.has(e.category) && e.original) {
			wordDict.set(e.original.trim().toLowerCase(), e);
		}
	}
	if (wordDict.size > 0) {
		const tokens = output.split(" ");
		for (let i = 0; i < tokens.length; i++) {
			// noUncheckedIndexedAccess: empty token → key "" → never a
			// dict hit, so the ?? "" fallback is behaviorally identical.
			const token = tokens[i] ?? "";
			const key = token.replace(TOKEN_KEY, "").toLowerCase();
			const entry = wordDict.get(key);
			if (entry) {
				const wrap = TOKEN_WRAP.exec(token);
				tokens[i] = wrap
					? `${wrap[1]}${entry.correction}${wrap[3]}`
					: entry.correction;
			}
		}
		output = tokens.join(" ");
	}

	return { output, applied: output !== text };
}
