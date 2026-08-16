/**
 * Share-stats image IPC handlers.
 *
 * The renderer captures the off-screen `StatsShareImage` as a PNG data
 * URL (via html-to-image) and hands it to the main process for the
 * platform operations a sandboxed renderer cannot do:
 *
 *   - `stats-image:save`    — write the PNG to the OS Downloads folder
 *                             instantly (mode: "downloads") or via the
 *                             native save dialog (mode: "saveAs").
 *   - `stats-image:copy`    — put the PNG on the OS clipboard
 *                             (`clipboard.writeImage`).
 *   - `stats-image:reveal`  — reveal a saved PNG in the OS file manager
 *                             (`shell.showItemInFolder`).
 *
 * All three return the canonical `{success, error?}` envelope
 * (see `../../shared/ipc-result.ts`).
 *
 * Security: the data URL and the reveal path cross the IPC boundary
 * untyped, so both are validated before use — the data URL must be a
 * base64 PNG (checked via the PNG signature bytes, not just the MIME
 * prefix) and capped in size, and the reveal path must be an existing
 * absolute `.png` file.
 */
import fs from "node:fs";
import path from "node:path";
import { app, clipboard, dialog, ipcMain, nativeImage, shell } from "electron";
import { withIpcEnvelope } from "../../shared/ipc-result";
import { mainT } from "../i18n";
import { StatsImageChannels } from "./channels";

/**
 * Cap on the accepted PNG data-URL payload. The share image is a fixed
 * 1200×630 card captured at 2× pixel ratio — typically 1-4 MB of PNG.
 * 25 MB is far above any legitimate capture (defends against a
 * compromised renderer passing a fabricated multi-GB base64 blob that
 * would pin the main process during `Buffer.from(base64)`).
 */
const MAX_PNG_DATA_URL_BYTES = 25 * 1024 * 1024;

/** PNG file signature — validated on the decoded bytes. */
const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47]);

/** A user-canceled save dialog is a silent no-op, not an error. */
type SaveResult =
	| { success: true; path: string }
	| { success: false; canceled?: boolean; error?: string };

/**
 * Decode a `data:image/png;base64,...` URL into a Buffer, validating
 * the MIME prefix, the base64 payload size, and the decoded PNG
 * signature. Returns `null` when any check fails.
 */
function decodePngDataUrl(dataUrl: unknown): Buffer | null {
	if (typeof dataUrl !== "string") return null;
	if (!dataUrl.startsWith("data:image/png;base64,")) return null;
	const b64 = dataUrl.slice("data:image/png;base64,".length);
	if (b64.length === 0 || b64.length > MAX_PNG_DATA_URL_BYTES) return null;
	let buf: Buffer;
	try {
		buf = Buffer.from(b64, "base64");
	} catch {
		return null;
	}
	if (
		buf.length < PNG_SIGNATURE.length ||
		!buf.subarray(0, 4).equals(PNG_SIGNATURE)
	) {
		return null;
	}
	return buf;
}

/**
 * Make a filesystem-safe default filename: strip path separators /
 * traversal, collapse whitespace, and guarantee a `.png` extension.
 * The renderer passes `voice-typer-stats` — this is defense in depth
 * against a compromised renderer injecting `..\evil.png` into the
 * Downloads path.
 */
function safePngFilename(raw: unknown): string {
	const base =
		typeof raw === "string"
			? raw
					// Neutralize path-traversal segments FIRST (a leading
					// `..` component in the filename would still escape the
					// Downloads dir via path.join even after the separator
					// strip below).
					.replace(/\.\./g, "-")
					.replace(/[\\/:*?"<>|\0]/g, "-")
					.replace(/\s+/g, "-")
					// Strip leading dots/dashes so the result is never a
					// hidden file or a bare `-`-prefixed name.
					.replace(/^[-.]+/, "")
					.trim()
			: "";
	const stem = base.slice(0, 80).replace(/\.png$/i, "") || "voice-typer-stats";
	return `${stem}.png`;
}

/**
 * Pick a non-colliding path in `dir`: if `dir/name` exists, append
 * ` (1)`, ` (2)`, … before the extension so an instant Downloads save
 * never silently overwrites a previous export.
 */
async function nonCollidingPath(
	dir: string,
	filename: string,
): Promise<string> {
	const ext = path.extname(filename);
	const stem = filename.slice(0, -ext.length);
	let candidate = path.join(dir, filename);
	for (let i = 1; ; i++) {
		try {
			await fs.promises.access(candidate);
		} catch {
			// Doesn't exist yet — safe to write.
			return candidate;
		}
		candidate = path.join(dir, `${stem} (${i})${ext}`);
	}
}

/**
 * Register the three share-stats image handlers. Idempotent (removes
 * any prior handler first) so tests can re-register freely.
 */
export function registerStatsImageHandlers(): void {
	ipcMain.removeHandler?.(StatsImageChannels.save);
	ipcMain.handle(
		StatsImageChannels.save,
		(
			_event,
			payload: { dataUrl?: unknown; defaultName?: unknown; mode?: unknown },
		) =>
			withIpcEnvelope<SaveResult>(async () => {
				const png = decodePngDataUrl(payload?.dataUrl);
				if (!png) {
					return { success: false, error: "Invalid PNG data" };
				}
				const filename = safePngFilename(payload?.defaultName);
				const mode = payload?.mode === "saveAs" ? "saveAs" : "downloads";

				if (mode === "saveAs") {
					const { canceled, filePath } = await dialog.showSaveDialog({
						title: mainT("dialog.export.statsImage"),
						defaultPath: filename,
						filters: [{ name: "PNG", extensions: ["png"] }],
					});
					if (canceled || !filePath) return { success: false, canceled: true };
					await fs.promises.writeFile(filePath, png);
					return { success: true, path: filePath };
				}

				// Instant save to the OS Downloads folder — no dialog.
				const downloads = app.getPath("downloads");
				const target = await nonCollidingPath(downloads, filename);
				await fs.promises.writeFile(target, png);
				return { success: true, path: target };
			}),
	);

	ipcMain.removeHandler?.(StatsImageChannels.copy);
	ipcMain.handle(
		StatsImageChannels.copy,
		(_event, payload: { dataUrl?: unknown }) =>
			withIpcEnvelope(async () => {
				const png = decodePngDataUrl(payload?.dataUrl);
				if (!png) return { success: false, error: "Invalid PNG data" };
				const image = nativeImage.createFromBuffer(png);
				if (image.isEmpty()) {
					return { success: false, error: "Invalid PNG data" };
				}
				clipboard.writeImage(image);
				return { success: true };
			}),
	);

	ipcMain.removeHandler?.(StatsImageChannels.reveal);
	ipcMain.handle(
		StatsImageChannels.reveal,
		(_event, payload: { path?: unknown }) =>
			withIpcEnvelope(async () => {
				const raw = payload?.path;
				if (typeof raw !== "string" || !path.isAbsolute(raw)) {
					return { success: false, error: "Invalid path" };
				}
				if (path.extname(raw).toLowerCase() !== ".png") {
					return { success: false, error: "Invalid path" };
				}
				try {
					await fs.promises.access(raw);
				} catch {
					return { success: false, error: "File not found" };
				}
				shell.showItemInFolder(raw);
				return { success: true };
			}),
	);
}
