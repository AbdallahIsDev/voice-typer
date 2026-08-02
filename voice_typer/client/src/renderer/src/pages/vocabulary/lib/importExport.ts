// Import parser for vocabulary files.
//
// Extracted from the former monolithic ``pages/Vocabulary.tsx`` so the
// import hook can call it without dragging in the React state layer.
//
// Accepts:
//  - A bare JSON array of ``{original, correction, category?}`` objects
//    (the new export shape — see ``useVocabularyImportExport``).
//  - A backend-shape ``VocabularyData`` object (the legacy / sync
//    export shape) — flattened via ``flattenEntries``.
//  - CSV text (the format produced by the export side's
//    ``exportVocabulary`` IPC handler) — ``original,correction[,category]``
//    per line, RFC 4180 quoting, optional header row.
//
// Throws on malformed JSON, unknown shape, or a CSV with zero valid
// rows so the caller can surface a toast.error with the parse failure
// reason.

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
		// Backend-shape VocabularyData — flatten it.
		return flattenEntries(parsed as VocabularyData);
	}
	throw new Error("File does not contain a vocabulary array or data object");
}

// Parse a CSV vocabulary export. Mirrors the export side's
// ``csvEscape`` (RFC 4180): cells containing a comma, double-quote, or
// newline are wrapped in double-quotes with any embedded double-quote
// doubled (``"`` → ``""``). The header row ``original,correction`` (or
// ``original,correction,category``) is optional — if present, it is
// skipped. Lines with fewer than 2 fields are skipped. A line with
// exactly 2 fields auto-detects its category via ``detectCategory``;
// a line with 3+ fields uses the third as the category (falling back
// to auto-detect if the value isn't a known backend category).
//
// Throws if zero valid rows are produced so the caller surfaces a
// toast.error instead of silently importing nothing.
function parseCsvVocabulary(text: string): VocabularyEntry[] {
	const rows: VocabularyEntry[] = [];
	const lines = splitCsvLines(text);
	let startIdx = 0;
	// Optional header detection — if the first non-empty line's first
	// cell is literally ``original`` (case-insensitive), skip it.
	const firstLine = lines[0];
	if (firstLine !== undefined) {
		const firstCells = parseCsvLine(firstLine);
		const firstCell = firstCells[0];
		if (
			firstCell !== undefined &&
			firstCell.trim().toLowerCase() === "original"
		) {
			startIdx = 1;
		}
	}
	for (let i = startIdx; i < lines.length; i++) {
		const line = lines[i];
		if (line === undefined) continue;
		const cells = parseCsvLine(line);
		if (cells.length < 2) continue;
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

// Split the CSV text into logical lines, honouring quoted newlines.
// A newline inside a double-quoted field does NOT terminate the row.
function splitCsvLines(text: string): string[] {
	const lines: string[] = [];
	let current = "";
	let inQuotes = false;
	for (let i = 0; i < text.length; i++) {
		const ch = text[i];
		if (ch === '"') {
			// Doubled double-quote inside a quoted field → literal quote,
			// stay inQuotes. Otherwise toggle the in-quotes state.
			if (inQuotes && text[i + 1] === '"') {
				current += '""';
				i++;
				continue;
			}
			inQuotes = !inQuotes;
			current += ch;
			continue;
		}
		if ((ch === "\n" || ch === "\r") && !inQuotes) {
			// Coalesce CRLF into a single line break.
			if (ch === "\r" && text[i + 1] === "\n") i++;
			lines.push(current);
			current = "";
			continue;
		}
		current += ch;
	}
	if (current.length > 0) lines.push(current);
	return lines.filter((l) => l.length > 0);
}

// Parse a single CSV line into cell strings, honouring RFC 4180
// double-quoting. Strips surrounding quotes from quoted cells and
// unescapes doubled double-quotes (``""`` → ``"``).
function parseCsvLine(line: string): string[] {
	const cells: string[] = [];
	let current = "";
	let inQuotes = false;
	for (let i = 0; i < line.length; i++) {
		const ch = line[i];
		if (ch === '"') {
			if (inQuotes && line[i + 1] === '"') {
				current += '"';
				i++;
				continue;
			}
			inQuotes = !inQuotes;
			continue;
		}
		if (ch === "," && !inQuotes) {
			cells.push(current);
			current = "";
			continue;
		}
		current += ch;
	}
	cells.push(current);
	return cells;
}
