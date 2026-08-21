/**
 * GDPR right-to-export IPC handlers.
 *
 * Extracted from `index.ts` (REF-2). Registers:
 *   - history:export     (history records → JSON or CSV with SEC-015 formula injection defense)
 *   - vocabulary:export  (vocabulary entries → JSON or CSV)
 *   - templates:export   (: trigger→output pairs → JSON)
 *   - config:export      (: full config → JSON; API keys redacted by the backend)
 */
import crypto from "node:crypto";
import fs from "node:fs";
import { dialog, ipcMain } from "electron";
import type { ExportFormat } from "../../shared/export-format";
import { withIpcEnvelope } from "../../shared/ipc-result";
import { mainT } from "../i18n";
import { ExportChannels } from "./channels";

/**
 * R6-F9: validated format set for `history:export` and
 * `vocabulary:export`. The renderer's `ExportFormat` type (in
 * `src/shared/export-format.ts`) narrows to `"json" | "csv"` but
 * the IPC boundary is untyped at runtime — a compromised renderer (or
 * a hand-crafted `ipcRenderer.invoke` call from devtools) could pass
 * any string. Validating here prevents the format string from being
 * interpolated into the file extension (`voice-typer-history.${format}`)
 * or the dialog filter, both of which are mild injection surfaces.
 */
const VALID_FORMATS = new Set(["json", "csv"]);

/**
 * R6-F9: hard cap on the number of rows exported via `history:export`.
 * 100k rows is ~50 MB of JSON / ~20 MB of CSV — well within the
 * fs.writeFileSync budget but far enough above any realistic history
 * size that legitimate users never hit it. The cap defends against a
 * compromised renderer passing a fabricated 10M-row array (which would
 * pin the CPU + disk for minutes and produce a multi-GB export file).
 */
const MAX_EXPORT_ROWS = 100_000;

/**
 * : hard cap on the number of template entries exported via
 * `templates:export`. Templates (trigger → output pairs) are far fewer
 * than history rows in practice (typically tens to low hundreds), so a
 * 1k cap is generous for legitimate use while still defending against a
 * compromised renderer pinning CPU + disk on a fabricated 10M-entry
 * payload (same threat model as `MAX_EXPORT_ROWS`). See  in the
 * comprehensive review.
 */
const MAX_TEMPLATES_EXPORT_ROWS = 1_000;

/**
 * : hard cap on the serialized byte size of the config blob
 * exported via `config:export`. Config is a flat-ish dict (settings,
 * preferences, hotkeys) and is typically a few KB; 1 MB is well above
 * any legitimate size. The cap defends against a compromised renderer
 * passing a fabricated multi-GB object that would pin CPU + disk during
 * `JSON.stringify` + `fs.writeFileSync` (same threat model as
 * `MAX_EXPORT_ROWS`). See  in the comprehensive review.
 */
const MAX_CONFIG_EXPORT_BYTES = 1 * 1024 * 1024;

/**
 * SEC-015: CSV formula injection defense.  Cells starting
 * with =, +, -, @, TAB, or CR are interpreted as formulas by
 * Excel/LibreOffice when the user opens the exported file.
 * Prefix them with a single quote so the spreadsheet treats
 * them as literal text.  Then apply RFC 4180 quoting: only
 * wrap the cell in double quotes when it contains a comma,
 * double-quote, newline, or carriage return; double any
 * embedded double-quotes.
 *
 * Mirrors the Rust host's `csv_escape` in
 * `src-tauri/src/commands/export.rs` — the two implementations
 * produce identical bytes for the same input. Parity is enforced
 * by `src/main/__tests__/export-handlers-csv-escape.test.ts`.
 *
 * Accepts `unknown` and coerces internally (strings pass through;
 * null/undefined → empty string; numbers/booleans/bigints → their
 * `String()` form; objects → their `JSON.stringify` form).
 *
 * Exported so unit tests can exercise it directly without going
 * through the Electron-coupled `registerExportHandlers`.
 */
export function csvEscape(v: unknown): string {
	let s: string;
	if (typeof v === "string") {
		s = v;
	} else if (v == null) {
		s = "";
	} else if (
		typeof v === "number" ||
		typeof v === "boolean" ||
		typeof v === "bigint"
	) {
		s = String(v);
	} else {
		try {
			s = JSON.stringify(v) ?? "";
		} catch {
			s = String(v);
		}
	}
	// SEC-015: prefix formula-injection-prone cells with a single quote.
	if (/^[=+\-@\t\r]/.test(s)) {
		s = `'${s}`;
	}
	// RFC 4180 quoting: only wrap in double quotes when the cell
	// contains a comma, double-quote, newline, or carriage return.
	// Doubles any embedded double-quotes. Matches the Rust host's
	// `csv_escape` byte-for-byte for the same input.
	if (/[",\n\r]/.test(s)) {
		return `"${s.replace(/"/g, '""')}"`;
	}
	return s;
}

/**
 * Atomic file write helper for the Electron-side export IPC paths.
 *
 * Writes `content` to a sibling temp file first, then renames the
 * temp file into place. POSIX `rename(2)` is atomic (the destination
 * is either the OLD file or the NEW file, never a truncated half).
 * Node 10+ on Windows sets `MOVEFILE_REPLACE_EXISTING` on
 * `fs.promises.rename`, so the rename overwrites the destination
 * atomically on both platforms.
 *
 * The user-picked destination may be on a network drive, USB stick, or
 * sync-client-watched folder (Dropbox/OneDrive). A non-atomic
 * `fs.promises.writeFile(filePath, ...)` truncates the destination
 * first, so a crash or disk-full mid-write leaves a partial CSV/JSON
 * that opens but is missing rows. The temp-then-rename pattern
 * guarantees the destination is either the prior file or the new file
 * (never a truncated half).
 *
 * Mirrors the Rust host's `crate::migrate::atomic_write_bytes` (used
 * by `src-tauri/src/commands/export.rs:export_data`) and the Python
 * backend's `_secure_atomic_write`. Three-language parity for the
 * user-data export path (history, vocabulary, templates, config).
 *
 * Async (uses `fs.promises.writeFile` + `fs.promises.rename`) so the
 * 20 MB export write does not block the Electron main thread for
 * 100-500 ms. The 4 export IPC handlers run inside `ipcMain.handle`
 * (already async) and `await` this helper.
 *
 * The staging file path is unique per invocation
 * (`<filePath>.<pid>.<uuid>.tmp`) so two concurrent exports to the
 * same destination don't collide on the same staging file (the
 * previous deterministic `<filePath>.tmp` meant the second invocation
 * overwrote the first invocation's staging bytes mid-write, then the
 * first invocation's rename succeeded with the second invocation's
 * content — a silent data swap).
 *
 * On success the staging file is gone (renamed into place). On error
 * (any failure that is NOT a recoverable EEXIST/EPERM rename), the
 * staging file is best-effort cleaned up so the tmp directory doesn't
 * leak. On the EPERM-destination-unlink-failure path the staging file
 * is INTENTIONALLY preserved so the user can manually rename it for
 * recovery.
 *
 * Critical fix: the previous Windows-only sync implementation
 * unlinked the destination BEFORE renaming. If `unlinkSync(filePath)`
 * failed with anything other than ENOENT (EPERM/EACCES on a
 * sync-client-held destination, antivirus lock, or network share ACL),
 * the catch block deleted the NEW content's tmp file and re-threw —
 * the user lost BOTH the pre-existing export AND the new content. The
 * fix attempts `fs.promises.rename(tmpPath, filePath)` unconditionally
 * first (Node 10+ overwrites on both POSIX and Windows). The
 * unlink+rename fallback runs ONLY on EEXIST/EPERM (legacy
 * pre-Node-10 behavior or rare Windows lock that defeats
 * MOVEFILE_REPLACE_EXISTING), and the fallback's unlink-failure path
 * does NOT delete the tmp file — keeping it lets the user manually
 * rename the staging file to `<filePath>` for recovery.
 *
 * Exported so unit tests can exercise it directly without going
 * through the Electron-coupled `registerExportHandlers`.
 *
 * @param filePath Absolute destination path.
 * @param content  String or Buffer to write.
 * @param encoding Encoding used when `content` is a string (default
 *                 `"utf-8"`). Ignored for Buffer content.
 */
export async function atomicWriteFile(
	filePath: string,
	content: string | Buffer,
	encoding: BufferEncoding = "utf-8",
): Promise<void> {
	// Unique staging path (PID + UUID) so concurrent invocations
	// don't collide on the same `<filePath>.tmp` staging file.
	// The staging file is a sibling (same directory) so the
	// rename(2) syscall stays within the same filesystem — cross-
	// device renames fall back to copy+delete, which is non-atomic
	// but still strictly better than truncate-in-place.
	const tmpPath = `${filePath}.${process.pid}.${crypto.randomUUID()}.tmp`;
	// Write to the staging file first.
	if (typeof content === "string") {
		await fs.promises.writeFile(tmpPath, content, encoding);
	} else {
		await fs.promises.writeFile(tmpPath, content);
	}
	// Try `fs.promises.rename(tmpPath, filePath)` unconditionally
	// first. Node 10+ on Windows uses `MOVEFILE_REPLACE_EXISTING`
	// so the rename overwrites the destination atomically (matching
	// POSIX `rename(2)` semantics). This avoids the destination-
	// unlink-then-rename window that, on a non-ENOENT unlink
	// failure, used to lose BOTH the old destination AND the new
	// tmp file.
	try {
		await fs.promises.rename(tmpPath, filePath);
		return;
	} catch (e) {
		const code = (e as NodeJS.ErrnoException).code;
		// EEXIST/EPERM: Windows refused to overwrite the
		// destination (legacy pre-Node-10 behavior, or a
		// sync-client/antivirus lock that defeats
		// MOVEFILE_REPLACE_EXISTING). Fall through to the
		// unlink+rename fallback. Any other error (ENOSPC,
		// ENOENT on the tmp, EACCES on the parent dir) is
		// unrecoverable — clean up the staging file and re-throw
		// (the user has no way to recover it).
		if (code !== "EEXIST" && code !== "EPERM") {
			try {
				await fs.promises.unlink(tmpPath);
			} catch {
				/* best-effort cleanup */
			}
			throw e;
		}
		// Fall through to the unlink+rename fallback below.
	}
	// Fallback: unlink the destination first, then retry the rename.
	// The unlink-then-rename window is racy on Windows if another
	// process holds the file open, but at this point the atomic
	// rename already failed — this is the best-effort fallback.
	try {
		await fs.promises.unlink(filePath);
	} catch (e) {
		const code = (e as NodeJS.ErrnoException).code;
		if (code !== "ENOENT") {
			// Do NOT unlink `tmpPath` here. The destination
			// unlink failed (EPERM/EACCES), so the destination
			// may still exist — but the NEW content's staging
			// file is the user's lifeline. Throwing without
			// deleting tmp lets the user manually rename the
			// staging file → `<filePath>` for recovery,
			// preserving the new export content even though the
			// atomic swap failed.
			throw e;
		}
		// ENOENT on destination unlink is fine — the destination
		// didn't exist, so the retry rename below will succeed
		// atomically.
	}
	// Again, do NOT unlink `tmpPath` here — keep it so the user
	// can manually rename it for recovery. The destination may
	// have already been unlinked by the fallback above, so losing
	// the staging file too would be unrecoverable.
	await fs.promises.rename(tmpPath, filePath);
}

/**
 * Synchronous atomic-write helper retained for backwards compatibility
 * with existing unit tests (`export-handlers-atomic-write.test.ts`).
 *
 * @deprecated Use the async `atomicWriteFile` instead — the sync
 * variant blocks the Electron main thread for 100-500 ms on a 20 MB
 * export. The export IPC handlers have been migrated to `atomicWriteFile`;
 * this sync variant is kept ONLY so the existing regression tests for
 * the Windows-fallback data-loss path continue to exercise the
 * fallback logic. New code MUST use `atomicWriteFile`.
 *
 * Behavioural parity with `atomicWriteFile` is intentional: the same
 * rename-first → unlink+rename-fallback logic, the same EPERM
 * destination-unlink-failure preservation of the staging file. The
 * only difference is the deterministic staging path (`<filePath>.tmp`)
 * which the async helper replaces with a per-invocation unique path
 * (`<filePath>.<pid>.<uuid>.tmp`) to prevent concurrent-export
 * collisions.
 */
export function atomicWriteFileSync(
	filePath: string,
	content: string | Buffer,
	encoding: BufferEncoding = "utf-8",
): void {
	const tmpPath = `${filePath}.tmp`;
	if (typeof content === "string") {
		fs.writeFileSync(tmpPath, content, encoding);
	} else {
		fs.writeFileSync(tmpPath, content);
	}
	try {
		fs.renameSync(tmpPath, filePath);
		return;
	} catch (e) {
		const code = (e as NodeJS.ErrnoException).code;
		if (code !== "EEXIST" && code !== "EPERM") {
			try {
				fs.unlinkSync(tmpPath);
			} catch {
				/* best-effort cleanup */
			}
			throw e;
		}
	}
	try {
		fs.unlinkSync(filePath);
	} catch (e) {
		const code = (e as NodeJS.ErrnoException).code;
		if (code !== "ENOENT") {
			throw e;
		}
	}
	fs.renameSync(tmpPath, filePath);
}

/**
 * Build the deterministic temp path used by the deprecated sync
 * `atomicWriteFileSync`. Exposed for unit tests so they can predict
 * the staging file's location without duplicating the suffix-
 * derivation logic.
 *
 * NOTE: the async `atomicWriteFile` helper uses a per-invocation
 * unique staging path (`<filePath>.<pid>.<uuid>.tmp`) and does NOT
 * expose a temp-path helper — the UUID is generated inside the helper
 * and is not predictable from the destination path alone.
 */
export function _atomicWriteTempPath(filePath: string): string {
	return `${filePath}.tmp`;
}

export function registerExportHandlers(): void {
	// ── History export ──────────────────────────────────────────────
	// Idempotent registration: removeHandler is a no-op if no handler
	// is registered for the channel. Optional chaining tolerates test
	// mocks that don't expose `removeHandler`.
	ipcMain.removeHandler?.(ExportChannels.history);
	ipcMain.handle(
		ExportChannels.history,
		(
			_event,
			{
				data,
				format,
			}: { data: Record<string, unknown>[]; format: ExportFormat },
		) =>
			// VP-19: canonical envelope via withIpcEnvelope (throw → {success:false, error}).
			withIpcEnvelope(async () => {
				// R6-F9: validate format against the allowlist BEFORE using it
				// in the dialog filter or the file path. Rejects unknown
				// formats early with a structured error instead of letting
				// the renderer pass through an arbitrary string.
				if (!VALID_FORMATS.has(format)) {
					return { success: false, error: "Invalid format" };
				}
				// R6-F9: cap the row count so a compromised renderer can't
				// pin the CPU + disk on a fabricated 10M-row payload.
				const allRows = Array.isArray(data) ? data : [];
				const originalCount = allRows.length;
				const truncated = allRows.length > MAX_EXPORT_ROWS;
				const rows = truncated ? allRows.slice(0, MAX_EXPORT_ROWS) : allRows;

				const filters =
					format === "csv"
						? [{ name: "CSV", extensions: ["csv"] }]
						: [{ name: "JSON", extensions: ["json"] }];

				const { canceled, filePath } = await dialog.showSaveDialog({
					title: mainT("dialog.export.history"),
					defaultPath: `voice-typer-history.${format}`,
					filters,
				});

				if (canceled || !filePath) return { success: false };

				if (format === "csv") {
					// Compute the union of keys across ALL rows so a row
					// missing a key still gets a column (with empty value).
					// The previous `Object.keys(rows[0] ?? {})` header
					// silently dropped columns that only appeared in later
					// rows — and `Object.values(r)` produced a different
					// column count per row, misaligning values under the
					// wrong headers.
					const keys = [...new Set(rows.flatMap((r) => Object.keys(r)))];
					const header = keys.map(csvEscape).join(",");
					const csvRows = rows.map((r) =>
						keys.map((k) => csvEscape(r[k] ?? "")).join(","),
					);
					// Avoid spread-then-join (memory spike + V8 arg-count risk at 100k rows).
					const body = csvRows.join("\n");
					await atomicWriteFile(filePath, `${header}\n${body}`, "utf-8");
				} else {
					await atomicWriteFile(
						filePath,
						JSON.stringify(rows, null, 2),
						"utf-8",
					);
				}
				return {
					success: true,
					path: filePath,
					...(truncated
						? {
								truncated: true as const,
								originalCount,
								truncatedTo: MAX_EXPORT_ROWS,
							}
						: {}),
				};
			}),
	);

	// ── Vocabulary export ──────────────────────────────────────────
	ipcMain.removeHandler?.(ExportChannels.vocabulary);
	ipcMain.handle(
		ExportChannels.vocabulary,
		(
			_event,
			{ data, format }: { data: Record<string, unknown>; format: ExportFormat },
		) =>
			// VP-19: canonical envelope via withIpcEnvelope (throw → {success:false, error}).
			withIpcEnvelope(async () => {
				// R6-F9: validate format against the allowlist BEFORE using it
				// in the dialog filter or the file path (same rationale as
				// history:export above).
				if (!VALID_FORMATS.has(format)) {
					return { success: false, error: "Invalid format" };
				}
				// R6-F9: cap the vocabulary entries at MAX_EXPORT_ROWS so a
				// compromised renderer can't pin the CPU + disk on a
				// fabricated 10M-row payload (same rationale as history:export).
				const vocab = (data ?? {}) as Record<string, unknown>;
				const rawEntries = Array.isArray(vocab.entries)
					? (vocab.entries as unknown[])
					: [];
				const originalCount = rawEntries.length;
				const truncated = rawEntries.length > MAX_EXPORT_ROWS;
				const entries = truncated
					? rawEntries.slice(0, MAX_EXPORT_ROWS)
					: rawEntries;

				const filters =
					format === "csv"
						? [{ name: "CSV", extensions: ["csv"] }]
						: [{ name: "JSON", extensions: ["json"] }];

				const { canceled, filePath } = await dialog.showSaveDialog({
					title: mainT("dialog.export.vocabulary"),
					defaultPath: `voice-typer-vocabulary.${format}`,
					filters,
				});

				if (canceled || !filePath) return { success: false };

				if (format === "csv") {
					// SEC-015: CSV formula injection defense (see history:export).
					const csvRows: string[] = ["original,correction"];
					for (const entry of entries as Array<Record<string, string>>) {
						csvRows.push(
							`${csvEscape(entry.original ?? "")},${csvEscape(entry.correction ?? "")}`,
						);
					}
					await atomicWriteFile(filePath, csvRows.join("\n"), "utf-8");
				} else {
					await atomicWriteFile(
						filePath,
						JSON.stringify(entries, null, 2),
						"utf-8",
					);
				}
				return {
					success: true,
					path: filePath,
					...(truncated
						? {
								truncated: true as const,
								originalCount,
								truncatedTo: MAX_EXPORT_ROWS,
							}
						: {}),
				};
			}),
	);

	// ── Templates export (NEW-PRIV-007: GDPR right-to-export) ──────
	// Previously only history and vocabulary were exportable.  Templates
	// (trigger → output pairs) are user data under GDPR Art. 15 (right
	// of access) and Art. 20 (right to data portability).  This handler
	// writes the templates list to a JSON file chosen by the user.
	ipcMain.removeHandler?.(ExportChannels.templates);
	ipcMain.handle(
		ExportChannels.templates,
		(_event, { data }: { data: unknown }) =>
			// VP-19: canonical envelope via withIpcEnvelope (throw → {success:false, error}).
			withIpcEnvelope(async () => {
				// PVT-14: cap the entry count so a compromised renderer
				// can't pin the CPU + disk on a fabricated 10M-entry
				// payload (same threat model as history:export /
				// vocabulary:export). Templates are normally a list of
				// {trigger, output} pairs; if the renderer sends
				// something else (an object, a primitive), we still
				// serialize it but only after coercing arrays through
				// the cap.
				let templatesData: unknown = data;
				let truncated = false;
				let originalCount = 0;
				if (Array.isArray(data)) {
					originalCount = data.length;
					truncated = data.length > MAX_TEMPLATES_EXPORT_ROWS;
					templatesData = truncated
						? data.slice(0, MAX_TEMPLATES_EXPORT_ROWS)
						: data;
				}

				const { canceled, filePath } = await dialog.showSaveDialog({
					title: mainT("dialog.export.templates"),
					defaultPath: "templates.json",
					filters: [{ name: "JSON", extensions: ["json"] }],
				});

				if (canceled || !filePath) return { success: false };

				await atomicWriteFile(
					filePath,
					JSON.stringify(templatesData, null, 2),
					"utf-8",
				);
				return {
					success: true,
					path: filePath,
					...(truncated
						? {
								truncated: true as const,
								originalCount,
								truncatedTo: MAX_TEMPLATES_EXPORT_ROWS,
							}
						: {}),
				};
			}),
	);

	// ── Config export (NEW-PRIV-007: GDPR right-to-export) ─────────
	// The user's full configuration (settings, preferences, hotkeys)
	// is personal data under GDPR Art. 15/20.  This handler writes the
	// config dict to a JSON file.  API keys are redacted by the Python
	// backend's get_config handler (SEC-003) so they don't leak via
	// this export path.
	ipcMain.removeHandler?.(ExportChannels.config);
	ipcMain.handle(ExportChannels.config, (_event, { data }: { data: unknown }) =>
		// VP-19: canonical envelope via withIpcEnvelope (throw → {success:false, error}).
		withIpcEnvelope(async () => {
			// PVT-14: cap the serialized byte size so a compromised
			// renderer can't pin the CPU + disk on a fabricated
			// multi-GB config object. Config is typically a few KB,
			// so 1 MB is a generous ceiling. We stringify first to
			// measure, then refuse the write if the blob exceeds
			// the cap (unlike row-based caps, slicing a config
			// object would silently drop keys and produce a
			// misleading partial export — better to fail loud).
			let serialized: string;
			try {
				serialized = JSON.stringify(data, null, 2);
			} catch (e: unknown) {
				return {
					success: false,
					error: (e as Error).message,
				};
			}
			// Buffer.byteLength accounts for multi-byte UTF-8 chars
			// correctly (string .length is UTF-16 code units).
			if (Buffer.byteLength(serialized, "utf-8") > MAX_CONFIG_EXPORT_BYTES) {
				return {
					success: false,
					error: `Config export exceeds the ${MAX_CONFIG_EXPORT_BYTES}-byte cap`,
				};
			}

			const { canceled, filePath } = await dialog.showSaveDialog({
				title: mainT("dialog.export.config"),
				defaultPath: "voice-typer-config.json",
				filters: [{ name: "JSON", extensions: ["json"] }],
			});

			if (canceled || !filePath) return { success: false };

			await atomicWriteFile(filePath, serialized, "utf-8");
			return { success: true, path: filePath };
		}),
	);
}
