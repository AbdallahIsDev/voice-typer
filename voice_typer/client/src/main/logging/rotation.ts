/**
 * Single-file log primitive + low-level log-line helpers for the
 * Electron main-process loggers.
 *
 * Single-file policy: each log is ONE file. When it exceeds its size
 * cap it is truncated IN PLACE (emptied) and writing continues — a
 * numbered backup (`.1`, `.2`, ...) is NEVER created.
 *
 * Per-path "perms verified" cache + deferred truncation via setImmediate.
 */
import fs from "node:fs";

import { ANSI_ENABLED_FLAG, DIM, RESET } from "./colors";
import {
	DEFAULT_CRASH_LOG_MAX_BYTES,
	DEFAULT_MAIN_LOG_MAX_BYTES,
} from "./constants";
import {
	_clearCachedFileSize,
	_getCachedFileSize,
	_setCachedFileSize,
} from "./fileSizeCache";

// Per-path "perms verified" cache.
const _permsVerified = new Set<string>();

/**
 * Reset the per-path "perms verified" cache. Exported for tests.
 * @internal
 */
export function _resetPermsVerifiedForTest(): void {
	_permsVerified.clear();
}

// ─── Logging-health ring buffer ───────────────────────────
//
// In packaged Electron builds, stdout/stderr are closed (no terminal
// attached), so the `console.warn(...)` calls at the failure sites in
// `rotateIfNeeded` / `appendLogLine` / `appendLifecycleLine` are no-ops.
// When logging silently degrades (disk full, perm regression, userData
// path moved to a read-only mount), there is ZERO durable trace and no
// way for the app to surface "logging is broken" to the user — the
// diagnostics meant to debug crashes are themselves silent.
//
// The ring buffer keeps the last `LOGGING_HEALTH_RING_MAX` failure
// entries in memory so an orchestrator (or future IPC handler) can
// query `getLoggingHealth()` and surface "logging degraded since
// <timestamp>: <error>" on a Troubleshooting page. The buffer is
// bounded so it can never grow unbounded on a churning disk failure.
// It is in-process only (cleared on restart) — durable persistence is
// intentionally NOT provided here because the act of writing to disk
// is itself the failing operation.

/**
 * A single entry in the logging-health ring buffer. Captured at every
 * `console.warn` failure site in the logging package so the orchestrator
 * can surface "logging degraded" to the user.
 */
export interface LoggingFailureEntry {
	/** ISO-8601 timestamp of the failure. */
	timestamp: string;
	/**
	 * The log file path involved in the failure, or `""` when the
	 * path itself could not be resolved (e.g. `app.getPath`
	 * threw before the path was computed).
	 */
	filePath: string;
	/**
	 * Short label identifying the failure site
	 * (e.g. `"rotateIfNeeded"`, `"appendLogLine"`, `"chmod 0o600"`).
	 */
	operation: string;
	/** Stringified error message (`Name: Message`). */
	error: string;
}

const LOGGING_FAILURE_RING: LoggingFailureEntry[] = [];
const LOGGING_HEALTH_RING_MAX = 20;

/**
 * Record a logging failure to the in-memory ring buffer. Called at
 * every `console.warn` site in the logging package so the orchestrator
 * can later surface "logging degraded" via {@link getLoggingHealth}.
 *
 * Best-effort — never throws. If the ring buffer itself fails (e.g.
 * `JSON.stringify` recursion on a hostile error object), the failure is
 * swallowed so the diagnostic code never crashes the caller.
 *
 * Exported (NOT in the public barrel) so `structuredLogger.ts` can
 * record failures from its `appendLifecycleLine` catch site alongside
 * the four call sites in this module. The orchestrator can also call
 * it directly if a future code path needs to record a non-`console.warn`
 * logging degradation (e.g. a synchronous flush that detected data loss).
 *
 * @internal — the public surface is `getLoggingHealth` /
 * `_resetLoggingHealthForTest`.
 */
export function recordLoggingFailure(
	filePath: string,
	operation: string,
	error: unknown,
): void {
	try {
		const entry: LoggingFailureEntry = {
			timestamp: new Date().toISOString(),
			filePath,
			operation,
			error:
				error instanceof Error
					? `${error.name}: ${error.message}`
					: String(error),
		};
		LOGGING_FAILURE_RING.push(entry);
		// Bound to last N entries.
		if (LOGGING_FAILURE_RING.length > LOGGING_HEALTH_RING_MAX) {
			LOGGING_FAILURE_RING.splice(
				0,
				LOGGING_FAILURE_RING.length - LOGGING_HEALTH_RING_MAX,
			);
		}
	} catch {
		// Swallow — the diagnostic code must never crash the caller.
		// The console.warn at the call site still fires in dev mode.
	}
}

/**
 * Return a snapshot of recent logging failures (last
 * {@link LOGGING_HEALTH_RING_MAX} entries). The orchestrator (or a
 * future IPC handler wired by the orchestrator) can call this to
 * surface "logging degraded since <timestamp>" on a Troubleshooting
 * page or in a support-bundle export.
 *
 * Returns a shallow copy so callers can iterate / mutate without
 * affecting the internal buffer. The entries themselves are NOT frozen
 * — callers should treat them as read-only.
 *
 * NOT wired to an IPC handler yet — kept as a plain exported function
 * so the orchestrator can wire it later (e.g. a `logging:get-health`
 * IPC handler in `ipc/window-handlers.ts`). Offline-app compliant
 * (AGENTS.md C-DATA-1) — never phones home, never writes to disk.
 */
export function getLoggingHealth(): LoggingFailureEntry[] {
	return [...LOGGING_FAILURE_RING];
}

/**
 * Reset the logging-health ring buffer. Exported for tests so each
 * test starts with a clean buffer state.
 * @internal
 */
export function _resetLoggingHealthForTest(): void {
	LOGGING_FAILURE_RING.length = 0;
}

// ─── PII redaction (TS port of Python's redact_pii) ────────

const _MIN_REDACT_LEN = 20;

const _PII_PATTERNS: ReadonlyArray<readonly [RegExp, string]> = [
	[/\b[\w.+-]+@[\w-]+\.[\w.-]+\b/g, "[EMAIL]"],
	[/\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b/g, "[IBAN]"],
	[/\b\d{3}[-.]?\d{3}[-.]?\d{4}\b/g, "[PHONE]"],
	[/\+\d{1,3}[\s-]?\(?\d{1,4}\)?[\s-]?\d{3,4}[\s-]?\d{3,4}\b/g, "[PHONE]"],
	[/\b\d{3}-\d{2}-\d{4}\b/g, "[SSN]"],
	[/\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b/g, "[CC]"],
];

// SEC-9 flag / key=value patterns. Mirrors Python's
// `_FLAG_KEY_PATTERNS` in `voice_typer/server/_secrets.py`. Applied
// BEFORE the `_MIN_REDACT_LEN` short-string guard because the
// explicit secret-bearing keyword makes them specific enough to be
// safe on short inputs (e.g. `--token=abc` is 12 chars but is
// unambiguously a secret-bearing flag).
//
// Two forms:
//   A. `--keyword=value` or `--keyword value` (long-flag form).
//      No `\b` required before `--` (mirrors Python).
//   B. `keyword=value` (bare key=value form, e.g. env vars / config).
//      `\b` prevents matching inside larger words (`monkey=` does
//      NOT match `key=`).
//
// Keyword alternation is ordered most-specific first, `key` last,
// so `api_key=` wins over `key=` at the same position (JS regex
// alternation is leftmost-first, like Python). Case-insensitive
// (`gi` flags) mirrors Python's `(?i)`.
const _FLAG_VALUE_PATTERN =
	/(--(?:token|apikey|api_key|api-key|secret|password|passwd|pwd|auth|authorization|authentication|access_token|access-token|refreshtoken|refresh_token|refresh-token|client_secret|client-secret|private_key|private-key|key)(?:=|\s+))[^\s=]+/gi;
const _BARE_KEY_VALUE_PATTERN =
	/\b((?:token|apikey|api_key|api-key|secret|password|passwd|pwd|auth|authorization|authentication|access_token|access-token|refreshtoken|refresh_token|refresh-token|client_secret|client-secret|private_key|private-key|key)=)[^\s=]+/gi;

const _SECRET_PATTERNS: ReadonlyArray<readonly [RegExp, string]> = [
	[/\bBearer\s+[A-Za-z0-9._~+/=-]+/g, "Bearer ***"],
	[/\bToken\s+[A-Za-z0-9._~+/=-]+/g, "Token ***"],
	// OpenAI / Stripe / generic `<prefix>-<token>` keys. Widened
	// charset (incl. `-`) so `sk-proj-…` matches. Capture group
	// preserves the prefix in the output (`sk-***` / `pk-***`).
	[/\b((?:sk|pk|key)-)[A-Za-z0-9_-]{8,}\b/g, "$1***"],
	// Groq `gsk_<token>` keys.
	[/\b(gsk_)[A-Za-z0-9_-]{8,}\b/g, "$1***"],
	// 20+ char bare alphanumeric catch-all. Catches GitHub PATs
	// (`ghp_<36>`), GitLab PATs (`glpat-<20>`), Slack tokens
	// (`xox[baprs]-<…>`), and any other bare long token with no
	// recognized prefix. Negative lookbehind/lookahead on `/` and
	// `\` prevent false-positive redaction of 20+ char filesystem
	// path components (e.g. `username_with_long_name` in
	// `/home/username_with_long_name/logs`). Mirrors Python's
	// `_KEY_PATTERNS[-1]`.
	[/(?<![/\\])\b[A-Za-z0-9_-]{20,}\b(?![/\\])/g, "***"],
];

const _URL_USERINFO = /([a-zA-Z][a-zA-Z0-9+.-]*:\/\/)[^\s:@/]+:[^\s@/]+@/g;

/**
 * PII / API-key / URL-credential redaction (TS port of Python's
 * `voice_typer.server.security.redact_pii`, which delegates the
 * API-key portion to `voice_typer.server._secrets.redact_secret`).
 *
 * Idempotent on already-redacted text so callers that pre-redact
 * (e.g. via `cleanConsoleMsg` chains) don't double-redact. Exported
 * so external callers that bypass `formatArgsForFile` — notably
 * `ipc/window-handlers.ts`'s `appendRendererError` call site, which
 * writes via direct `appendLogLine` — can apply the same redaction
 * the format helpers apply internally. Internal callers
 * (`printfLogger.ts`, `structuredLogger.ts`) import directly from
 * `./rotation` to avoid the barrel re-export overhead on the hot log
 * path; external callers should import via the `logging/index.ts`
 * barrel re-export.
 *
 * Patterns redacted:
 *   - Email addresses     → `[EMAIL]`
 *   - IBAN codes          → `[IBAN]`
 *   - Phone numbers       → `[PHONE]`
 *   - SSNs                → `[SSN]`
 *   - Credit-card numbers → `[CC]`
 *   - `Bearer`/`Token`    → `Bearer ***` / `Token ***`
 *   - `sk-`/`pk-`/`key-`  → `sk-***` / `pk-***` / `key-***`
 *   - `gsk_`              → `gsk_***`            (Groq API keys)
 *   - 20+ char bare run   → `***`                (catches GitHub /
 *                                                   GitLab / Slack PATs;
 *                                                   path-delimiter
 *                                                   lookarounds skip
 *                                                   filesystem paths)
 *   - `--keyword=value`   → `--keyword=***`      (SEC-9 flag form)
 *   - `--keyword value`   → `--keyword ***`      (SEC-9 flag form)
 *   - `keyword=value`     → `keyword=***`        (SEC-9 bare form)
 *   - URL userinfo        → stripped
 *
 * The SEC-9 flag / key=value patterns run BEFORE the
 * `_MIN_REDACT_LEN` short-string guard (the explicit keyword makes
 * them specific enough to be safe on short inputs like `--token=abc`).
 * The Bearer / Token / sk- / gsk_ / 20+ char patterns run AFTER the
 * guard (mirrors Python's `redact_secret`).
 */
export function redactPii(text: string): string {
	if (typeof text !== "string" || text.length === 0) return text;
	let out = text;
	for (const [pat, repl] of _PII_PATTERNS) {
		out = out.replace(pat, repl);
	}
	// SEC-9: flag / key=value patterns run before the length guard
	// (specific enough to be safe on short inputs).
	out = out.replace(_FLAG_VALUE_PATTERN, "$1***");
	out = out.replace(_BARE_KEY_VALUE_PATTERN, "$1***");
	if (out.length >= _MIN_REDACT_LEN) {
		for (const [pat, repl] of _SECRET_PATTERNS) {
			out = out.replace(pat, repl);
		}
	}
	if (out.includes("@")) {
		out = out.replace(_URL_USERINFO, "$1");
	}
	return out;
}

/**
 * Truncate the file in place if it exceeds {@link maxSize} bytes.
 *
 * Single-file policy: the file is emptied (truncated to zero bytes) and
 * keeps its single identity — numbered backups are never created.
 * Best-effort: any I/O error is swallowed and recorded to the
 * logging-health ring buffer.
 */
export function rotateIfNeeded(
	filePath: string,
	maxSize: number = DEFAULT_CRASH_LOG_MAX_BYTES,
): void {
	const cachedSize = _getCachedFileSize(filePath);
	let size: number;
	if (cachedSize !== null) {
		size = cachedSize;
	} else {
		try {
			size = fs.statSync(filePath).size;
		} catch (e) {
			// ENOENT is the expected case (file not yet
			// created on the first append) — return
			// silently so `appendLogLine`'s appendFileSync
			// creates the file. Any other error (EACCES,
			// EIO, ENOTDIR, ...) signals a real
			// degradation that the orchestrator should
			// surface via the logging-health ring.
			const code = (e as NodeJS.ErrnoException).code;
			if (code === "ENOENT") return;
			recordLoggingFailure(filePath, "statSync", e);
			return;
		}
	}
	if (size <= maxSize) {
		_setCachedFileSize(filePath, size);
		return;
	}
	// Single-file policy: NEVER create a numbered backup (`.1`, `.2`, ...).
	// When the file exceeds the cap, truncate it IN PLACE (empty it) and
	// keep writing to the same file — the log stays exactly one file.
	try {
		fs.truncateSync(filePath, 0);
		_clearCachedFileSize(filePath);
		// Reset the per-path "perms verified" flag so the next append
		// re-asserts 0o600 on the (now empty) file.
		_permsVerified.delete(filePath);
	} catch (e) {
		console.warn("[logging] rotateIfNeeded failed:", e);
		recordLoggingFailure(filePath, "rotateIfNeeded", e);
	}
}

/**
 * Append a single line to filePath, truncating it in place first if the
 * file has grown past maxBytes (single-file policy). Best-effort: any
 * I/O error is swallowed.
 *
 * Deferred truncation: the rotateIfNeeded call is wrapped in
 * setImmediate(...) so the rotation I/O does not block the current IPC
 * dispatch. The appendFileSync still runs synchronously (crash
 * durability preserved).
 *
 * Perms cache: fs.chmodSync is skipped if the per-path
 * "perms verified" flag is set. Eliminates 30 sync chmods/sec churn.
 *
 * ── Throughput vs. crash-safety trade-off (intentional design) ──
 *
 * This function uses `fs.appendFileSync` (open + write + close per
 * call) rather than a persistent `fs.createWriteStream(path, { flags:
 * 'a' })` held in a module-level Map. The write-stream approach would
 * eliminate the per-call open/close syscall overhead (the stream
 * buffers in memory and flushes in the background), but it regresses
 * crash durability: bytes buffered in the stream's internal WriteStream
 * buffer are LOST on a hard process crash (SIGKILL, segfault, OOM-kill)
 * because the kernel never receives them. `appendFileSync` is
 * synchronous — when the call returns, the bytes are in the kernel's
 * page cache (and will reach disk on the next fsync / kernel flush).
 * For a diagnostic log whose last few lines are the MOST valuable
 * lines precisely when the process is about to crash (the crash
 * traceback, the "shutting down" breadcrumb, the final IPC error),
 * losing them to a background flush is unacceptable.
 *
 * The open/close overhead is ~50-100µs per call on a warm SSD. At the
 * 60 Hz `bubble_level` hot path the deferred-executor already
 * serializes fan-out through a single worker thread, so the main /
 * RT threads never see this cost — only the executor does, and it has
 * ample headroom (60 calls/sec × 100µs = 6ms/sec = 0.6% of one core).
 * The write-stream alternative was therefore rejected as a
 * crash-safety regression for a negligible perf gain on a non-RT path.
 *
 * If a future hot path ever needs >1000 writes/sec to the SAME file
 * from a non-deferred thread, revisit this decision — a per-path
 * WriteStream with an explicit `end()`-on-exit flush hook would then
 * be worth the complexity. Until then, synchronous append is the
 * correct trade.
 */
export function appendLogLine(
	filePath: string,
	line: string,
	maxBytes: number = DEFAULT_MAIN_LOG_MAX_BYTES,
): void {
	try {
		// Pre-append synchronous rotation: if the cached size
		// already exceeds the cap, rotate BEFORE the append so
		// the rotation is not lost on a hard crash exit
		// (SIGKILL / segfault / OOM-kill). The deferred
		// `setImmediate` rotation below only fires on the next
		// event-loop tick — a hard crash before that tick would
		// lose the rotation entirely, leaving the next process
		// to inherit an oversized file. The synchronous path
		// only fires when the cache already knows the file is
		// over the cap (so we don't pay the `statSync` cost on
		// the cold-start / cache-miss path — the deferred
		// `rotateIfNeeded` handles that).
		const preCachedSize = _getCachedFileSize(filePath);
		if (preCachedSize !== null && preCachedSize > maxBytes) {
			try {
				rotateIfNeeded(filePath, maxBytes);
			} catch (e) {
				console.warn(
					`[logging] synchronous rotateIfNeeded failed for ${filePath}:`,
					e,
				);
				recordLoggingFailure(filePath, "rotateIfNeeded.pre-append", e);
			}
		} else {
			// Defer rotation to the next event-loop tick
			// for the non-urgent case (cached size below
			// the cap, or cache miss — the deferred
			// `rotateIfNeeded` will stat the file then).
			setImmediate(() => {
				try {
					rotateIfNeeded(filePath, maxBytes);
				} catch (e) {
					console.warn(
						`[logging] deferred rotateIfNeeded failed for ${filePath}:`,
						e,
					);
					recordLoggingFailure(filePath, "rotateIfNeeded.deferred", e);
				}
			});
		}
		fs.appendFileSync(filePath, line, { flag: "a", mode: 0o600 });
		// Skip chmod if already verified for this path.
		if (!_permsVerified.has(filePath)) {
			try {
				fs.chmodSync(filePath, 0o600);
				_permsVerified.add(filePath);
			} catch (e) {
				// Best-effort — leave flag unset so next append retries.
				// Surface the failure so a perm regression (read-only dir,
				// Windows ACL reset) is visible in the dev console instead
				// of silently swallowed.
				console.warn(`[logging] chmod 0o600 failed for ${filePath}:`, e);
				recordLoggingFailure(filePath, "chmod 0o600", e);
			}
		}
		const prevSize = _getCachedFileSize(filePath);
		if (prevSize !== null) {
			_setCachedFileSize(filePath, prevSize + Buffer.byteLength(line, "utf-8"));
		}
	} catch (e) {
		console.warn(`[logging] appendLogLine failed for ${filePath}:`, e);
		recordLoggingFailure(filePath, "appendLogLine", e);
	}
}

export const cleanConsoleMsg = (msg: string): string =>
	msg
		.replace(/^%c[^;]+;\s*/, "")
		.replace(/%[csoidf]/g, "")
		.replace(/\n{3,}/g, "\n\n")
		.replace(/[ \t]+/g, " ")
		.trim();

export function ts(): string {
	const d = new Date();
	// 24-hour clock (00-23). The previous 12-hour form
	// (`d.getHours() % 12 || 12`) was ambiguous in log triage —
	// `07:15:30` could be AM or PM. 24-hour is the unambiguous
	// ISO-style convention used by Python's logging module and by
	// the rest of the Electron / Rust / Python cross-process log
	// timeline, so the printf-style `log.*` lines now line up with
	// `electron-main.log`'s ISO-8601 timestamps (which already use
	// 24-hour via `new Date().toISOString()`).
	const h = String(d.getHours()).padStart(2, "0");
	const m = String(d.getMinutes()).padStart(2, "0");
	const s = String(d.getSeconds()).padStart(2, "0");
	const time = `${h}:${m}:${s}`;
	// File-redirected output (no terminal — ANSI colors disabled):
	// prefix the date so a multi-session `electron-stderr.log` is
	// unambiguous. Mirrors the Python side's timestamp split
	// (`_iso_timestamp`: terminal = time-only, file = date + time).
	// A real terminal keeps the time-only dimmed form.
	if (!ANSI_ENABLED_FLAG) {
		const y = String(d.getFullYear());
		const mo = String(d.getMonth() + 1).padStart(2, "0");
		const day = String(d.getDate()).padStart(2, "0");
		return `${y}-${mo}-${day}  ${time}`;
	}
	return `${DIM}${time}${RESET}`;
}
