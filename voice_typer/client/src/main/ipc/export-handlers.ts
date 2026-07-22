/**
 * GDPR right-to-export IPC handlers.
 *
 * Extracted from `index.ts` (REF-2). Registers:
 *   - history:export     (history records → JSON or CSV with SEC-015 formula injection defense)
 *   - vocabulary:export  (vocabulary entries → JSON or CSV)
 *   - templates:export   (NEW-PRIV-007: trigger→output pairs → JSON)
 *   - config:export      (NEW-PRIV-007: full config → JSON; API keys redacted by the backend)
 */
import fs from "node:fs";
import { dialog, ipcMain } from "electron";
import { mainT } from "../i18n";

/**
 * R6-F9: validated format set for `history:export` and
 * `vocabulary:export`. The renderer type unions `"json" | "csv"` but
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
 * PVT-14: hard cap on the number of template entries exported via
 * `templates:export`. Templates (trigger → output pairs) are far fewer
 * than history rows in practice (typically tens to low hundreds), so a
 * 1k cap is generous for legitimate use while still defending against a
 * compromised renderer pinning CPU + disk on a fabricated 10M-entry
 * payload (same threat model as `MAX_EXPORT_ROWS`). See PVT-14 in the
 * comprehensive review.
 */
const MAX_TEMPLATES_EXPORT_ROWS = 1_000;

/**
 * PVT-14: hard cap on the serialized byte size of the config blob
 * exported via `config:export`. Config is a flat-ish dict (settings,
 * preferences, hotkeys) and is typically a few KB; 1 MB is well above
 * any legitimate size. The cap defends against a compromised renderer
 * passing a fabricated multi-GB object that would pin CPU + disk during
 * `JSON.stringify` + `fs.writeFileSync` (same threat model as
 * `MAX_EXPORT_ROWS`). See PVT-14 in the comprehensive review.
 */
const MAX_CONFIG_EXPORT_BYTES = 1 * 1024 * 1024;

/**
 * SEC-015: CSV formula injection defense.  Cells starting
 * with =, +, -, @, TAB, or CR are interpreted as formulas by
 * Excel/LibreOffice when the user opens the exported file.
 * Prefix them with a single quote so the spreadsheet treats
 * them as literal text.  Also wrap in double quotes (with
 * embedded quotes doubled) to prevent injection via newlines
 * or commas.
 */
function csvEscape(s: string): string {
	let v = String(s ?? "");
	if (/^[=+\-@\t\r]/.test(v)) {
		v = `'${v}`;
	}
	return `"${v.replace(/"/g, '""')}"`;
}

export function registerExportHandlers(): void {
	// ── History export ──────────────────────────────────────────────
	ipcMain.handle(
		"history:export",
		async (
			_event,
			{
				data,
				format,
			}: { data: Record<string, unknown>[]; format: "json" | "csv" },
		) => {
			// R6-F9: validate format against the allowlist BEFORE using it
			// in the dialog filter or the file path. Rejects unknown
			// formats early with a structured error instead of letting
			// the renderer pass through an arbitrary string.
			if (!VALID_FORMATS.has(format)) {
				return { success: false, error: "Invalid format" };
			}
			// R6-F9: cap the row count so a compromised renderer can't
			// pin the CPU + disk on a fabricated 10M-row payload.
			const rows = Array.isArray(data)
				? data.length > MAX_EXPORT_ROWS
					? data.slice(0, MAX_EXPORT_ROWS)
					: data
				: [];

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

			try {
				if (format === "csv") {
					const header = Object.keys(rows[0] ?? {})
						.map(csvEscape)
						.join(",");
					const csvRows = rows.map((r) =>
						Object.values(r)
							.map((v) => csvEscape(v as string))
							.join(","),
					);
					fs.writeFileSync(filePath, [header, ...csvRows].join("\n"), "utf-8");
				} else {
					fs.writeFileSync(filePath, JSON.stringify(rows, null, 2), "utf-8");
				}
				return { success: true, path: filePath };
			} catch (e: unknown) {
				return { success: false, error: (e as Error).message };
			}
		},
	);

	// ── Vocabulary export ──────────────────────────────────────────
	ipcMain.handle(
		"vocabulary:export",
		async (
			_event,
			{
				data,
				format,
			}: { data: Record<string, unknown>; format: "json" | "csv" },
		) => {
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
			const entries =
				rawEntries.length > MAX_EXPORT_ROWS
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

			try {
				if (format === "csv") {
					// SEC-015: CSV formula injection defense (see history:export).
					const csvRows: string[] = ["original,correction"];
					for (const entry of entries as Array<Record<string, string>>) {
						csvRows.push(
							`${csvEscape(entry.original ?? "")},${csvEscape(entry.correction ?? "")}`,
						);
					}
					fs.writeFileSync(filePath, csvRows.join("\n"), "utf-8");
				} else {
					fs.writeFileSync(filePath, JSON.stringify(entries, null, 2), "utf-8");
				}
				return { success: true, path: filePath };
			} catch (e: unknown) {
				return { success: false, error: (e as Error).message };
			}
		},
	);

	// ── Templates export (NEW-PRIV-007: GDPR right-to-export) ──────
	// Previously only history and vocabulary were exportable.  Templates
	// (trigger → output pairs) are user data under GDPR Art. 15 (right
	// of access) and Art. 20 (right to data portability).  This handler
	// writes the templates list to a JSON file chosen by the user.
	ipcMain.handle(
		"templates:export",
		async (_event, { data }: { data: unknown }) => {
			// PVT-14: cap the entry count so a compromised renderer
			// can't pin the CPU + disk on a fabricated 10M-entry
			// payload (same threat model as history:export /
			// vocabulary:export). Templates are normally a list of
			// {trigger, output} pairs; if the renderer sends
			// something else (an object, a primitive), we still
			// serialize it but only after coercing arrays through
			// the cap.
			let templatesData: unknown = data;
			if (Array.isArray(data)) {
				templatesData =
					data.length > MAX_TEMPLATES_EXPORT_ROWS
						? data.slice(0, MAX_TEMPLATES_EXPORT_ROWS)
						: data;
			}

			const { canceled, filePath } = await dialog.showSaveDialog({
				title: mainT("dialog.export.templates"),
				defaultPath: "voice-typer-templates.json",
				filters: [{ name: "JSON", extensions: ["json"] }],
			});

			if (canceled || !filePath) return { success: false };

			try {
				fs.writeFileSync(
					filePath,
					JSON.stringify(templatesData, null, 2),
					"utf-8",
				);
				return { success: true, path: filePath };
			} catch (e: unknown) {
				return { success: false, error: (e as Error).message };
			}
		},
	);

	// ── Config export (NEW-PRIV-007: GDPR right-to-export) ─────────
	// The user's full configuration (settings, preferences, hotkeys)
	// is personal data under GDPR Art. 15/20.  This handler writes the
	// config dict to a JSON file.  API keys are redacted by the Python
	// backend's get_config handler (SEC-003) so they don't leak via
	// this export path.
	ipcMain.handle(
		"config:export",
		async (_event, { data }: { data: unknown }) => {
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

			try {
				fs.writeFileSync(filePath, serialized, "utf-8");
				return { success: true, path: filePath };
			} catch (e: unknown) {
				return { success: false, error: (e as Error).message };
			}
		},
	);
}
