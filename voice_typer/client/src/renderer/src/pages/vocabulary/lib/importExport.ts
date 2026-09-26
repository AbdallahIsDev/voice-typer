// Import parser for vocabulary files.
// import hook can call it without dragging in the React state layer.
// Accepts:
//  - A bare JSON array of ``{original, correction, category?}`` objects
//    (the new export shape, see ``useVocabularyImportExport``).
//  - A backend-shape ``VocabularyData`` object (the legacy / sync
//    export shape), flattened via ``flattenEntries``.
//  - CSV text (the format produced by the export side's
//    ``exportVocabulary`` IPC handler), ``original,correction[,category]``
//    per line, RFC 4180 quoting, optional header row.
// Throws on malformed JSON, unknown shape, or a CSV with zero valid
// rows so the caller can surface a toast.error with the parse failure
// reason.

import Papa from "papaparse";

import type { VocabularyData, VocabularyEntry } from "@/types/ipc";

import { CATEGORIES, detectCategory } from "./categories";
import { flattenEntries } from "./transform";

export function parseImportedVocabulary(text: string): VocabularyEntry[] {
	const trimmed = text.trim();
	// Heuristic: JSON inputs start with ``[`` or ``{``. Anything else
	// is treated as CSV (the export side's CSV writer emits a header
	// line ``original,correction`` followed by quoted/unquoted rows,
	// never starting with ``[`` or ``{``).
	if (trimmed.startsWith("[") || trimmed.startsWith("{")) {
		return parseJsonVocabulary(trimmed);
	}
	return parseCsvVocabulary(trimmed);
}

function parseJsonVocabulary(text: string): VocabularyEntry[] {
	const parsed = JSON.parse(text) as unknown;
	if (Array.isArray(parsed)) {
		return parsed
			.filter(
				(
					e: unknown,
				): e is {
					original: unknown;
					correction: unknown;
					category?: unknown;
				} => typeof e === "object" && e !== null,
			)
			.map((e) => ({
				original: typeof e.original === "string" ? e.original : "",
				correction: typeof e.correction === "string" ? e.correction : "",
				category:
					typeof e.category === "string" &&
					CATEGORIES.includes(e.category as (typeof CATEGORIES)[number])
						? e.category
						: detectCategory(typeof e.original === "string" ? e.original : ""),
			}));
	}
	if (parsed && typeof parsed === "object") {
		// Backend-shape VocabularyData, flatten it.
		return flattenEntries(parsed as VocabularyData);
	}
	throw new Error("File does not contain a vocabulary array or data object");
}

// Parse a CSV vocabulary export via the shared CSV parser (RFC 4180
// quoting, CRLF + quoted-newline rows). The header row
// ``original,correction[,category]`` is optional and skipped on the
// same case-insensitive first-cell rule as before. Rows with fewer
// than 2 fields or two empty cells are skipped. A 2-field row
// auto-detects its category; a 3+-field row uses the third cell when
// it names a known backend category. Throws on zero valid rows so the
// caller surfaces a toast instead of silently importing nothing.
function parseCsvVocabulary(text: string): VocabularyEntry[] {
	const parsed = Papa.parse<string[]>(text, {
		delimiter: ",",
		header: false,
		skipEmptyLines: true,
	});
	const data = (parsed.data ?? []).filter((row): row is string[] =>
		Array.isArray(row),
	);
	let startIdx = 0;
	const firstCell = data[0]?.[0];
	if (
		firstCell !== undefined &&
		firstCell.trim().toLowerCase() === "original"
	) {
		startIdx = 1;
	}
	const rows: VocabularyEntry[] = [];
	for (let i = startIdx; i < data.length; i++) {
		const cells = data[i];
		if (cells === undefined || cells.length < 2) continue;
		const original = cells[0] ?? "";
		const correction = cells[1] ?? "";
		if (!original && !correction) continue;
		const rawCategory = cells[2]?.trim() ?? "";
		const category: VocabularyEntry["category"] =
			rawCategory &&
			CATEGORIES.includes(rawCategory as (typeof CATEGORIES)[number])
				? (rawCategory as VocabularyEntry["category"])
				: detectCategory(original);
		rows.push({ original, correction, category });
	}
	if (rows.length === 0) {
		throw new Error("File does not contain a vocabulary array or data object");
	}
	return rows;
}
