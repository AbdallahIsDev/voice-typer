// types/ipc/history.ts
//
// History-domain IPC contracts: the data shape of a single history row,
// the per-day stats aggregate, and the response-data shapes for the two
// on-demand history endpoints (`get_history_count` and
// `get_transcription_text`).
//
//Split out from the original monolithic `types/ipc.ts` ( / ).
// No behaviour change vs. the original file — pure structural refactor.

// ── History data shapes (from Python history_db) ───────────────────

export interface HistoryRecord {
	id: number;
	text: string;
	timestamp: string;
	duration: number;
	model: string;
	device: string;
	word_count: number;
	char_count: number;
	favorite: number;
	language: string;
	// ``text`` is now a 500-char preview in list responses
	// (``get_history`` / ``get_favorites`` / ``search_history``).
	// ``text_truncated`` is ``true`` when the full text exceeded
	// the 500-char preview; ``text_full_length`` is the total
	// char count of the untruncated text. Both are OPTIONAL for
	// backward compatibility — older callers that don't read
	// these fields continue to work, and the renderer fetches the
	// full text via ``get_transcription_text`` when the user
	// expands a row.
	text_truncated?: boolean;
	text_full_length?: number;
}

export interface TodayStats {
	count: number;
	chars: number;
	word_count: number;
	duration: number;
}

// ── Response data shapes for the on-demand history endpoints ──────
//
// ``HistoryCountData`` is the ``data`` field of the
// ``history_count`` response (returned by ``get_history_count``);
// ``TranscriptionTextData`` is the ``data`` field of the
// ``transcription_text`` response (returned by
// ``get_transcription_text``).
//
// The corresponding request interfaces live in ``./requests.ts``.

export interface HistoryCountData {
	count: number;
}

export interface TranscriptionTextData {
	id: number;
	text: string;
}
