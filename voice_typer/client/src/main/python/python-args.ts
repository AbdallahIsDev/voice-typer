/**
 * Resolve the Python backend executable + args for the current platform.
 *
 * Extracted from `index.ts` (REF-2).
 *
 * RW-5 + RW-4: in packaged builds, launch the embedded PyInstaller
 * backend from `process.resourcesPath` instead of the dev venv. Without
 * this, the macOS/Linux/Windows installers ship Electron only with
 * no Python backend, and the app silently fails to start.
 */
import fs from "node:fs";
import path from "node:path";
import { app } from "electron";
import { IPC_PORT } from "../constants";
import { computeConfigDir } from "../single_instance";

export function pythonArgs(): [string, string[]] {
	// Each platform has its own `case` branch in the switch below —
	// they are independent so we don't accidentally clobber the others.
	// The dev-mode venv path at the bottom of this function is the
	// fallback for any platform that doesn't match (or whose bundled
	// backend is missing on disk).
	if (app.isPackaged) {
		switch (process.platform) {
			case "darwin": {
				// macOS: PyInstaller .app bundle. The executable lives at
				// Contents/MacOS/voice-typer inside the .app. electron-builder's
				// mac.extraResources copies the bundle to
				// ${resourcesPath}/voice-typer-backend.app at packaging time.
				const macBackend = path.join(
					process.resourcesPath,
					"voice-typer-backend.app",
					"Contents",
					"MacOS",
					"voice-typer",
				);
				if (fs.existsSync(macBackend)) {
					return [macBackend, ["--port", String(IPC_PORT)]];
				}
				break;
			}
			case "linux": {
				// Linux: PyInstaller onedir. The executable lives at
				// voice-typer inside the bundle directory. electron-builder's
				// linux.extraResources copies the directory to
				// ${resourcesPath}/voice-typer-backend at packaging time.
				const linuxBackend = path.join(
					process.resourcesPath,
					"voice-typer-backend",
					"voice-typer",
				);
				if (fs.existsSync(linuxBackend)) {
					return [linuxBackend, ["--port", String(IPC_PORT)]];
				}
				break;
			}
			case "win32": {
				// RW-4 / Wave 3: Windows packaged backend lookup. CI's
				// build-windows job runs `pyinstaller
				// scripts/build/voice-typer.spec --distpath
				// voice_typer/dist` from the repo root, producing
				// voice_typer/dist/VoiceTyper.exe (onefile — current
				// spec has no COLLECT() call).
				// electron-builder's `win.extraResources` (see
				// electron-builder.yml) copies ../dist/ to
				// ${resourcesPath}/voice-typer-backend/ at packaging
				// time.
				//
				// Lookup order:
				//   1. ${resourcesPath}/voice-typer-backend/VoiceTyper.exe
				//      (PyInstaller onefile — current voice-typer.spec output)
				//   2. ${resourcesPath}/voice-typer-backend/VoiceTyper/VoiceTyper.exe
				//      (PyInstaller onedir — future-proof if spec adds COLLECT())
				//   3. Fall through to the dev-mode venv path below.
				//
				// The frozen exe is the IPC server entry point (spec entry
				// is voice_typer/server/ipc_server.py, whose main() uses
				// parse_known_args to accept --port) — we spawn it directly
				// with `--port <N>` (no `-m` flag, since the bundled exe
				// already imports voice_typer.server.ipc_server via its
				// entry script).
				const winBackendDir = path.join(
					process.resourcesPath,
					"voice-typer-backend",
				);
				const winOnefileExe = path.join(winBackendDir, "VoiceTyper.exe");
				const winOnedirExe = path.join(
					winBackendDir,
					"VoiceTyper",
					"VoiceTyper.exe",
				);
				try {
					if (fs.existsSync(winOnefileExe)) {
						return [winOnefileExe, ["--port", String(IPC_PORT)]];
					}
					if (fs.existsSync(winOnedirExe)) {
						return [winOnedirExe, ["--port", String(IPC_PORT)]];
					}
				} catch {
					// fs.existsSync can throw on broken symlinks /
					// permission errors — fall through to the dev venv.
				}
				break;
			}
			// (no default — fall through to the dev-mode venv path below)
		}
	}

	// RW-15: use computeConfigDir() so the venv path always matches the
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
