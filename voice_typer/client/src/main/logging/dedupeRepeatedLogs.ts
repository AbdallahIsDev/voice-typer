/**
 * Collapse consecutive identical log lines into a single line with an
 * `(xN)` repeat count.
 *
 * Motivation: the Electron main process can emit a burst of identical
 * `logger.warn` lines — e.g. `python-call rejected {cmd, code}` while
 * the renderer retries IPC calls against a disconnected backend — and
 * the raw repetition floods `electron-main.log` / stderr with N copies
 * of the same fact. This wrapper keeps the FIRST occurrence as-is (so
 * the line appears immediately, with no added latency), suppresses
 * identical repeats, and emits one `(xN)` summary line when the streak
 * of identical messages ends.
 *
 * Semantics (per streak = consecutive calls with the same message AND
 * the same serialized args):
 *
 *   - 1st call: forwarded to `emit` unchanged (no suffix).
 *   - 2nd..Nth identical call: suppressed; the repeat counter grows.
 *   - Streak break (a different message/args arrive): if the streak had
 *     >= 2 occurrences, one summary `emit(msg, ...args, "(xN)")` is
 *     emitted — `N` is the TOTAL number of occurrences in the streak —
 *     BEFORE the new message's first line.
 *   - Growth-gated heartbeat: while a streak of >= 2 is STILL GROWING,
 *     a summary `(xN)` (cumulative count) is re-emitted every
 *     `flushIntervalMs` (default 60s), so an endless flood (e.g. a
 *     never-ending "backend not connected" probe loop) shows a live
 *     count instead of going silent after the first line. Once the
 *     count stops growing the timer stops (no phantom summaries for an
 *     ended burst), and it re-arms on the next repeat.
 *
 * The summary appends `(xN)` as a trailing STRING argument — both the
 * structuredLogger (`electron-main.log`) and the console formatter
 * render it as a bare suffix on the same line:
 *
 *     python-call rejected { cmd: 'get_config', code: 'backend_not_connected' } (x15)
 *
 * The key includes the serialized args, so two calls that happen to
 * share a message but carry different fields (e.g. different `cmd`) are
 * independent streaks.
 *
 * Note: this is a consecutive-run deduper. Two bursts of the same
 * message separated by a different message are collapsed separately
 * (each burst gets its own `(xN)`). That is intentional — it matches
 * the bursty failure patterns these logs actually exhibit.
 */
export interface DedupeRepeatedLogsOptions {
	/**
	 * Heartbeat interval for an active repeat streak (>= 2 occurrences
	 * and still growing). A summary `(xN)` is re-emitted every interval
	 * while the count keeps growing; the timer stops once the streak
	 * goes idle and re-arms on the next repeat. Default: 60_000 ms.
	 */
	flushIntervalMs?: number;
}

/**
 * Serialize an argument into the dedup key. Objects are JSON-stringified
 * (key order is stable for the literal object shapes passed at each call
 * site); anything unstringifiable falls back to `String(value)` so the
 * key construction never throws.
 */
function keyPart(value: unknown): string {
	try {
		return JSON.stringify(value) ?? String(value);
	} catch {
		return String(value);
	}
}

/**
 * Wrap `emit` so consecutive identical `(msg, ...args)` calls collapse
 * into one line with an `(xN)` repeat count. See the module docstring
 * for the full semantics. Returns a function with the same signature as
 * `emit`, so it can be dropped in wherever a raw `logger.*` call was
 * made.
 */
export function dedupeRepeatedLogs(
	emit: (msg: string, ...args: unknown[]) => void,
	options?: DedupeRepeatedLogsOptions,
): (msg: string, ...args: unknown[]) => void {
	const flushIntervalMs = options?.flushIntervalMs ?? 60_000;

	let currentKey: string | null = null;
	let currentMsg = "";
	let currentArgs: unknown[] = [];
	let count = 0;
	// Total occurrences reflected by the most recent `(xN)` summary for
	// the CURRENT streak. The heartbeat only re-emits when the count has
	// grown past this, and the streak-break summary only emits when there
	// is something newer to report — so an ended burst is summarized at
	// most once instead of once per minute forever.
	let lastSummaryCount = 0;
	let timer: ReturnType<typeof setTimeout> | null = null;

	const stopTimer = (): void => {
		if (timer !== null) {
			clearTimeout(timer);
			timer = null;
		}
	};

	/** Emit the `(xN)` summary for the CURRENT streak if there is any
	 * repeat to report that hasn't been reported yet. */
	const emitSummaryIfNew = (): void => {
		if (count >= 2 && count > lastSummaryCount) {
			emit(currentMsg, ...currentArgs, `(x${count})`);
			lastSummaryCount = count;
		}
	};

	const startHeartbeat = (): void => {
		stopTimer();
		timer = setTimeout(() => {
			timer = null;
			if (currentKey === null) {
				return;
			}
			const grew = count > lastSummaryCount;
			if (grew) {
				emit(currentMsg, ...currentArgs, `(x${count})`);
				lastSummaryCount = count;
			}
			// Keep heartbeating only while the streak is still growing.
			// If it went idle (no new occurrences since the last summary),
			// stop the timer; the next repeat re-arms it.
			if (grew) {
				startHeartbeat();
			}
		}, flushIntervalMs);
	};

	return (msg: string, ...args: unknown[]): void => {
		const key = `${msg}\u0000${args.map((arg) => keyPart(arg)).join("\u0000")}`;
		if (key === currentKey) {
			count += 1;
			// Arm the heartbeat on the first repeat; re-arm it when a
			// flood resumes after the timer went idle.
			if (count === 2 || timer === null) {
				startHeartbeat();
			}
			return; // suppressed repeat
		}
		// Streak ended (different message/args): flush the previous
		// summary BEFORE emitting the new message's first line. Skips a
		// summary the heartbeat already emitted at the same count (no
		// duplicate when a streak breaks right after a heartbeat tick).
		emitSummaryIfNew();
		stopTimer();
		currentKey = key;
		currentMsg = msg;
		currentArgs = args;
		count = 1;
		lastSummaryCount = 0;
		emit(msg, ...args);
	};
}
