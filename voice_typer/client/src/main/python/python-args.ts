/**
 * Resolve the Python backend executable + args for the current platform.
 *
 * Extracted from `index.ts` (REF-2).
 *
 *  + : in packaged builds, launch the embedded PyInstaller
 * backend from `process.resourcesPath` instead of the dev venv. Without
 * this, the macOS/Linux/Windows installers ship Electron only with
 * no Python backend, and the app silently fails to start.
 */
import fs from "node:fs";
import path from "node:path";
import { app } from "electron";
import { IPC_PORT } from "../constants";
import { log } from "../logging";
import { computeConfigDir } from "../single_instance";

/**
 * : resolve the bundled PyInstaller backend path for the current
 * platform, with a 2-path fallback (onefile + onedir variants) wrapped
 * in try/catch (so a broken symlink / permission error falls through to
 * the dev venv instead of crashing the spawn).
 *
 * Previously the Windows branch had this robust 2-path + try/catch
 * pattern ( / Wave 3) but macOS and Linux had single-path lookups
 * with no try/catch — if a future macOS/Linux PyInstaller spec changed
 * the output layout (e.g. added COLLECT() to switch from onefile to
 * onedir), the packaged build would silently fall through to the dev
 * venv path on those platforms. This helper applies the Windows
 * pattern uniformly.
 *
 * Returns the absolute path to the backend executable, or ``null`` if
 * no candidate exists (caller falls through to the dev venv).
 */
function resolveBundledBackend(platform: NodeJS.Platform): string | null {
	// Each candidate is a [dir, ...pathParts] tuple — we join them
	// against ``process.resourcesPath`` so the helper stays pure.
	const candidates: string[] = [];
	const resourcesPath = process.resourcesPath;
	if (platform === "darwin") {
		// macOS: PyInstaller .app bundle (current spec — onedir-style).
		candidates.push(
			path.join(
				resourcesPath,
				"voice-typer-backend.app",
				"Contents",
				"MacOS",
				"voice-typer",
			),
		);
		// Future-proof: if spec switches to a bare Mach-O executable
		// without the .app wrapper (PyInstaller onefile macOS output).
		candidates.push(
			path.join(resourcesPath, "voice-typer-backend", "voice-typer"),
		);
	} else if (platform === "linux") {
		// Linux: PyInstaller onedir (current spec).
		candidates.push(
			path.join(resourcesPath, "voice-typer-backend", "voice-typer"),
		);
		// Future-proof: PyInstaller onefile (single executable, no
		// sibling libs).
		candidates.push(
			path.join(resourcesPath, "voice-typer-backend", "VoiceTyper"),
		);
	} else if (platform === "win32") {
		// Windows: PyInstaller onefile (current spec).
		candidates.push(
			path.join(resourcesPath, "voice-typer-backend", "VoiceTyper.exe"),
		);
		// Future-proof: PyInstaller onedir (executable inside a
		// VoiceTyper/ subdirectory alongside sibling DLLs).
		candidates.push(
			path.join(
				resourcesPath,
				"voice-typer-backend",
				"VoiceTyper",
				"VoiceTyper.exe",
			),
		);
	}
	for (const candidate of candidates) {
		try {
			if (fs.existsSync(candidate)) {
				return candidate;
			}
		} catch (e) {
			// fs.existsSync can throw on broken symlinks / permission
			// errors — try the next candidate before falling through
			// to the dev venv. Log at debug so the failure
			// is observable in the diagnostic log without spamming the
			// default level (mirrors `relaunch-app.ts:158`).
			log.debug("[PY-ARGS] fs.existsSync failed (non-fatal):", e);
		}
	}
	return null;
}

export function pythonArgs(): [string, string[]] {
	// Each platform has its own `case` branch in the switch below —
	// they are independent so we don't accidentally clobber the others.
	// The dev-mode venv path at the bottom of this function is the
	// fallback for any platform that doesn't match (or whose bundled
	// backend is missing on disk).
	if (app.isPackaged) {
		//route all platforms through resolveBundledBackend so
		// macOS / Linux get the same 2-path + try/catch fallback that
		//Windows had ( / Wave 3).
		const bundled = resolveBundledBackend(process.platform);
		if (bundled !== null) {
			return [bundled, ["--port", String(IPC_PORT)]];
		}
	}

	//use computeConfigDir() so the venv path always matches the
	// Python backend's _config_dir()-based venv_pythonw() (_paths.py).
	// Previously hardcoded ~/.voice-typer/venv, which diverged on
	// non-Windows and fresh installs (config dir may be %APPDATA%
	// or ~/Library/Application Support rather than ~/.voice-typer).
	const base = path.join(computeConfigDir(), "venv");
	const exe =
		process.platform === "win32"
			? path.join(base, "Scripts", "pythonw.exe")
			: path.join(base, "bin", "python3");
	return [
		exe,
		["-m", "voice_typer.server.ipc_server", "--port", String(IPC_PORT)],
	];
}
