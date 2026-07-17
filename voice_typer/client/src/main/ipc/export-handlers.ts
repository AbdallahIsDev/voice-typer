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
			const filters =
				format === "csv"
					? [{ name: "CSV", extensions: ["csv"] }]
					: [{ name: "JSON", extensions: ["json"] }];

			const { canceled, filePath } = await dialog.showSaveDialog({
				title: "Export History",
				defaultPath: `voice-typer-history.${format}`,
				filters,
			});

			if (canceled || !filePath) return { success: false };

			try {
				if (format === "csv") {
					const header = Object.keys(data[0] ?? {})
						.map(csvEscape)
						.join(",");
					const rows = data.map((r) =>
						Object.values(r)
							.map((v) => csvEscape(v as string))
							.join(","),
					);
					fs.writeFileSync(filePath, [header, ...rows].join("\n"), "utf-8");
				} else {
					fs.writeFileSync(filePath, JSON.stringify(data, null, 2), "utf-8");
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
			const filters =
				format === "csv"
					? [{ name: "CSV", extensions: ["csv"] }]
					: [{ name: "JSON", extensions: ["json"] }];

			const { canceled, filePath } = await dialog.showSaveDialog({
				title: "Export Vocabulary",
				defaultPath: `voice-typer-vocabulary.${format}`,
				filters,
			});

			if (canceled || !filePath) return { success: false };

			try {
				if (format === "csv") {
					// SEC-015: CSV formula injection defense (see history:export).
					const rows: string[] = ["original,correction"];
					const vocab = data as Record<string, unknown>;
					const entries = (vocab.entries ?? []) as Array<
						Record<string, string>
					>;
					for (const entry of entries) {
						rows.push(
							`${csvEscape(entry.original ?? "")},${csvEscape(entry.correction ?? "")}`,
						);
					}
					fs.writeFileSync(filePath, rows.join("\n"), "utf-8");
				} else {
					const vocab = data as Record<string, unknown>;
					fs.writeFileSync(
						filePath,
						JSON.stringify(vocab.entries ?? [], null, 2),
						"utf-8",
					);
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
			const { canceled, filePath } = await dialog.showSaveDialog({
				title: "Export Templates",
				defaultPath: "voice-typer-templates.json",
				filters: [{ name: "JSON", extensions: ["json"] }],
			});

			if (canceled || !filePath) return { success: false };

			try {
				fs.writeFileSync(filePath, JSON.stringify(data, null, 2), "utf-8");
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
			const { canceled, filePath } = await dialog.showSaveDialog({
				title: "Export Configuration",
				defaultPath: "voice-typer-config.json",
				filters: [{ name: "JSON", extensions: ["json"] }],
			});

			if (canceled || !filePath) return { success: false };

			try {
				fs.writeFileSync(filePath, JSON.stringify(data, null, 2), "utf-8");
				return { success: true, path: filePath };
			} catch (e: unknown) {
				return { success: false, error: (e as Error).message };
			}
		},
	);
}
