/**
 * File-rotation primitive + low-level log-line helpers for the
 * Electron main-process loggers.
 *
 * AB-40: per-path "perms verified" cache + deferred rotation via setImmediate.
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

// AB-40: per-path "perms verified" cache.
const _permsVerified = new Set<string>();

/**
 * AB-40: reset the per-path "perms verified" cache. Exported for tests.
 * @internal
 */
export function _resetPermsVerifiedForTest(): void {
	_permsVerified.clear();
}

// ─── XZ-LOG-03: PII redaction (TS port of Python's redact_pii) ────────

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
		// AB-40: reset the per-path "perms verified" flag on rotation.
		_permsVerified.delete(filePath);
	} catch (e) {
		console.warn("[logging] rotateIfNeeded failed:", e);
	}
}

/**
 * Append a single line to filePath, rotating first if the file has
 * grown past maxBytes. Best-effort: any I/O error is swallowed.
 *
 * AB-40 (deferred rotation): the rotateIfNeeded call is wrapped in
 * setImmediate(...) so the rotation I/O does not block the current IPC
 * dispatch. The appendFileSync still runs synchronously (crash
 * durability preserved).
 *
 * AB-40 (perms cache): fs.chmodSync is skipped if the per-path
 * "perms verified" flag is set. Eliminates 30 sync chmods/sec churn.
 */
export function appendLogLine(
	filePath: string,
	line: string,
	maxBytes: number = DEFAULT_MAIN_LOG_MAX_BYTES,
): void {
	try {
		// AB-40: defer rotation to the next event-loop tick.
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
		// AB-40: skip chmod if already verified for this path.
		if (!_permsVerified.has(filePath)) {
			try {
				fs.chmodSync(filePath, 0o600);
				_permsVerified.add(filePath);
			} catch {
				/* best-effort — leave flag unset so next append retries */
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
