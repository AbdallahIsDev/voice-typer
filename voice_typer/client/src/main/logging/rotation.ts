/**
 * File-rotation primitive + low-level log-line helpers for the
 * Electron main-process loggers.
 *
 * Per-path "perms verified" cache + deferred rotation via setImmediate.
 */
import fs from "node:fs";

import { DIM, RESET } from "./colors";
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

const _SECRET_PATTERNS: ReadonlyArray<readonly [RegExp, string]> = [
	[/\bBearer\s+[A-Za-z0-9._~+/=-]+/g, "Bearer ***"],
	[/\bToken\s+[A-Za-z0-9._~+/=-]+/g, "Token ***"],
	[/\b(?:sk|pk|key)-[A-Za-z0-9]{10,}\b/g, "***"],
];

const _URL_USERINFO = /([a-zA-Z][a-zA-Z0-9+.-]*:\/\/)[^\s:@/]+:[^\s@/]+@/g;

/**
 * PII / API-key / URL-credential redaction (TS port of Python's
 * `voice_typer.server.security.redact_pii`).
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
 *   - `sk-`/`pk-`/`key-`  → `***`  (only when input ≥ 20 chars)
 *   - URL userinfo        → stripped
 */
export function redactPii(text: string): string {
	if (typeof text !== "string" || text.length === 0) return text;
	let out = text;
	for (const [pat, repl] of _PII_PATTERNS) {
		out = out.replace(pat, repl);
	}
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
		} catch {
			return;
		}
	}
	if (size <= maxSize) {
		_setCachedFileSize(filePath, size);
		return;
	}
	const backup = `${filePath}.1`;
	try {
		try {
			fs.unlinkSync(backup);
		} catch (e) {
			const code = (e as NodeJS.ErrnoException).code;
			if (code !== "ENOENT") throw e;
		}
		fs.renameSync(filePath, backup);
		_clearCachedFileSize(filePath);
		// Reset the per-path "perms verified" flag on rotation.
		_permsVerified.delete(filePath);
	} catch (e) {
		console.warn("[logging] rotateIfNeeded failed:", e);
	}
}

/**
 * Append a single line to filePath, rotating first if the file has
 * grown past maxBytes. Best-effort: any I/O error is swallowed.
 *
 * Deferred rotation: the rotateIfNeeded call is wrapped in
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
		// Defer rotation to the next event-loop tick.
		setImmediate(() => {
			try {
				rotateIfNeeded(filePath, maxBytes);
			} catch (e) {
				console.warn(
					`[logging] deferred rotateIfNeeded failed for ${filePath}:`,
					e,
				);
			}
		});
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
			}
		}
		const prevSize = _getCachedFileSize(filePath);
		if (prevSize !== null) {
			_setCachedFileSize(filePath, prevSize + Buffer.byteLength(line, "utf-8"));
		}
	} catch (e) {
		console.warn(`[logging] appendLogLine failed for ${filePath}:`, e);
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
	const h = d.getHours() % 12 || 12;
	const m = String(d.getMinutes()).padStart(2, "0");
	const s = String(d.getSeconds()).padStart(2, "0");
	return `${DIM}${h}:${m}:${s}${RESET}`;
}
