/**
 * Shared config-directory resolver for the Electron main process.
 *
 * Extracted from `single_instance.ts` during the O1 logs → logs/ migration
 * so the logging package can resolve the logs dir without a circular
 * dependency (`single_instance.ts` imports `log` from `./logging`).
 *
 * Mirrors `_config_dir()` in `voice_typer/server/config.py`:
 *   1. `VOICE_TYPER_CONFIG_DIR` env var (if set)
 *   2. Legacy `~/.voice-typer` (if it exists — migration path)
 *   3. Platform-appropriate path (`%APPDATA%/voice-typer` on Windows,
 *      `~/Library/Application Support/voice-typer` on macOS,
 *      `$XDG_DATA_HOME/voice-typer` on Linux)
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export function computeConfigDir(): string {
	const envOverride = process.env.VOICE_TYPER_CONFIG_DIR;
	if (envOverride) return envOverride;
	const legacy = path.join(os.homedir(), ".voice-typer");
	try {
		if (fs.existsSync(legacy)) return legacy;
	} catch (e) {
		// ignore — fs.existsSync may throw on permission denied or
		// if the homedir is unreachable. Fall through to the
		// platform-appropriate default path below.
		console.warn("[config-dir] legacy config dir probe failed:", e);
	}
	if (process.platform === "win32") {
		return path.join(process.env.APPDATA || os.homedir(), "voice-typer");
	}
	if (process.platform === "darwin") {
		return path.join(
			os.homedir(),
			"Library",
			"Application Support",
			"voice-typer",
		);
	}
	return path.join(
		process.env.XDG_DATA_HOME || path.join(os.homedir(), ".local", "share"),
		"voice-typer",
	);
}
