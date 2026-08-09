import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import os from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const __dirname = dirname(fileURLToPath(import.meta.url));
//renamed ``root`` → ``clientDir`` and kept
// ``projectRoot`` for the repo root.  The old names (``root`` vs
// ``projectRoot``) were confusing — ``root`` sounded like the repo
// root but was actually the client/ directory.
const clientDir = resolve(__dirname, "..");
const projectRoot = resolve(__dirname, "..", "..", "..");
const svgPath = resolve(__dirname, "logo.svg");

const sizes = {
	favicon: [16, 32, 48],
	electron: [512],
	ico: [16, 24, 32, 48, 64, 128, 256],
	tray: [16, 24, 32, 48, 64],
};

async function generateIcons(svg, label, suffix) {
	const resourcesDir = resolve(clientDir, "resources");
	const publicDir = resolve(clientDir, "src", "renderer", "public");

	// Electron resources
	await sharp(Buffer.from(svg))
		.resize(512, 512)
		.png()
		.toFile(resolve(resourcesDir, `icon${suffix}.png`));
	console.log(`Created resources/icon${suffix}.png (512x512) [${label}]`);

	await sharp(Buffer.from(svg))
		.resize(256, 256)
		.png()
		.toFile(resolve(resourcesDir, `icon${suffix}-256.png`));
	console.log(`Created resources/icon${suffix}-256.png [${label}]`);

	// PNG favicons
	for (const size of sizes.favicon) {
		await sharp(Buffer.from(svg))
			.resize(size, size)
			.png()
			.toFile(resolve(publicDir, `favicon${suffix}-${size}.png`));
	}
	if (!suffix) {
		// Write a theme-aware favicon.svg that uses prefers-color-scheme media query
		// instead of currentColor, which doesn't reliably work in SVG favicons.
		const faviconSvg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" fill="none">
  <style>
    .bar { fill: #1a1a1a; }
    @media (prefers-color-scheme: dark) {
      .bar { fill: #f0f0f0; }
    }
  </style>
  <rect class="bar" x="15" y="48" width="14" height="32" rx="7"/>
  <rect class="bar" x="43" y="32" width="14" height="64" rx="7"/>
  <rect class="bar" x="71" y="16" width="14" height="96" rx="7"/>
  <rect class="bar" x="99" y="40" width="14" height="48" rx="7"/>
</svg>`;
		writeFileSync(resolve(publicDir, "favicon.svg"), faviconSvg);
		console.log("Created public/favicon.svg (theme-aware)");
		await sharp(Buffer.from(svg))
			.resize(180, 180)
			.png()
			.toFile(resolve(publicDir, "apple-touch-icon.png"));
	}
	console.log(`Created public favicons${suffix && " (dark)"}`);
}

async function generateIco(pngPath, icoPath) {
	const { execSync } = await import("node:child_process");
	//previously this hardcoded a venv python path as the
	// FIRST candidate (``~/.voice-typer/venv/Scripts/python.exe``) —
	// a path that almost never exists on a developer's machine (it
	// only exists inside an installed app's bundled venv, not in a
	// source checkout).  The order is now:
	//   1. The current project's venv (``.venv/bin/python`` or
	//      ``.venv/Scripts/python.exe`` relative to the repo root).
	//   2. ``python3`` from PATH (the most common dev environment).
	//   3. ``python`` from PATH (Windows often only has ``python``).
	//   4. The legacy app venv path (only present inside an installed
	//      app, kept as a last-resort fallback for build pipelines
	//      that run inside the installed app's directory).
	// The first one that exists AND can import PIL wins.
	const projectVenvPython =
		process.platform === "win32"
			? resolve(projectRoot, ".venv", "Scripts", "python.exe")
			: resolve(projectRoot, ".venv", "bin", "python");
	const legacyAppVenvPython =
		process.platform === "win32"
			? resolve(os.homedir(), ".voice-typer", "venv", "Scripts", "python.exe")
			: resolve(os.homedir(), ".voice-typer", "venv", "bin", "python3");
	const candidates = [
		projectVenvPython,
		"python3",
		"python",
		legacyAppVenvPython,
	];
	const icoScript = `
from PIL import Image
img = Image.open("${pngPath.replace(/\\/g, "/")}")
img.save("${icoPath.replace(/\\/g, "/")}", format="ICO", sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
print("ICO generated")
`;
	let lastErr = null;
	for (const py of candidates) {
		// Skip venv candidates (paths starting with . or /) that don't
		// exist on this platform — they would just produce a noisy error.
		if ((py.startsWith(".") || py.startsWith("/")) && !existsSync(py)) continue;
		try {
			execSync(`"${py}" -c "${icoScript.replace(/"/g, '\\"')}"`, {
				stdio: "pipe",
			});
			return; // success
		} catch (e) {
			lastErr = e;
			// Try next candidate
		}
	}
	throw new Error(
		`Failed to generate ICO: no working Python+PIL found. ` +
			`Tried: ${candidates.join(", ")}. ` +
			`Install Pillow with: pip install Pillow. ` +
			`Last error: ${lastErr?.message ?? "unknown"}`,
	);
}

/**
 * Regenerate the Tauri tray state icons
 * (``src-tauri/icons/tray/{idle,recording,transcribing,error}.png`` +
 * ``tray-mic-template.png``) from the microphone bar shape + the shared
 * state palette.
 *
 * Standalone function so the tray set can be regenerated WITHOUT
 * touching the Electron / server / bundle-icon sets (``node
 * generate-icons.mjs --tray``) — the repeatable wrapper
 * ``scripts/build/generate_tray_icons.py`` calls exactly this path.
 * The four state names + palette MUST stay in sync with
 * ``src-tauri/src/tray.rs`` (``is_allowed_icon_name``) and the Python
 * host (``voice_typer/server/tray_icon.py::_make_icon``) — the drift
 * guards in ``tests/tauri/test_tray_icons.py`` enforce that.
 */
async function generateTauriTrayIcons(tauriIconsDir) {
	// Microphone bar shape (mirrors the pystray server-asset traySvg
	// above). White bars on transparent — tinted per state below.
	const traySvg = `<svg width="148" height="148" viewBox="0 0 148 148" fill="none" xmlns="http://www.w3.org/2000/svg"><rect x="18.5" y="55.5" width="18.5" height="37" rx="9.25" fill="white"/><rect x="49.3333" y="37" width="18.5" height="74" rx="9.25" fill="white"/><rect x="80.1667" y="18.5" width="18.5" height="111" rx="9.25" fill="white"/><rect x="111" y="45.0938" width="18.5" height="57.8125" rx="9.25" fill="white"/></svg>`;

	// Tauri tray state icons (src-tauri/icons/tray/). The Rust host
	// (`src-tauri/src/tray.rs::load_tray_icon`) whitelists icon
	// names to EXACTLY `idle` / `recording` / `transcribing` /
	// `error` (matching the Python `tray.py::_APP_STATE_TO_ICON_NAME`
	// mapping). Each PNG is the base microphone bar shape colorized
	// per AppState — mirrors the Python `_make_icon` palette at
	// `tray_icon.py:302-309` so the visual states are identical
	// across the pystray (Python) and Tauri (Rust) hosts.
	//
	// Color palette (RGBA, alpha = 255 for full opacity):
	//   idle         — (120, 120, 120)   gray
	//   recording    — (46, 204, 113)    bright green (color-blind safe)
	//   transcribing — (52, 152, 219)    blue
	//   error        — (231, 76, 60)     red
	//
	// The bars are rendered on a transparent background so the OS
	// tray chrome (light/dark menubar, taskbar notch) shows through.
	// On macOS, `icon_as_template(true)` is set in the Rust
	// TrayIconBuilder chain — the OS applies the menubar color and
	// uses the alpha channel as a mask, so the per-state color is
	// only visible on Windows/Linux. macOS users get the bar SHAPE
	// (which is identical across states — state is shown via the
	// tooltip).
	const trayStateColors = {
		idle: { r: 120, g: 120, b: 120 },
		recording: { r: 46, g: 204, b: 113 },
		transcribing: { r: 52, g: 152, b: 219 },
		error: { r: 231, g: 76, b: 60 },
	};
	const tauriTrayDir = resolve(tauriIconsDir, "tray");
	mkdirSync(tauriTrayDir, { recursive: true });
	// Render the bars DIRECTLY in the state color by injecting the
	// color into the SVG fill. (The previous approach — white bars +
	// sharp `tint` — silently produced IDENTICAL white icons for
	// every state: sharp's tint preserves luminance, so tinting pure
	// white is a no-op on the RGB channels. Injecting the fill color
	// is exact: bar pixels are (r,g,b) with the anti-aliased alpha
	// the SVG renderer produces on the rounded-rect edges.)
	const trayIconSize = 32;
	for (const [stateName, color] of Object.entries(trayStateColors)) {
		const coloredSvg = traySvg.replace(
			/fill="white"/g,
			`fill="rgb(${color.r}, ${color.g}, ${color.b})"`,
		);
		await sharp(Buffer.from(coloredSvg))
			.resize(trayIconSize, trayIconSize)
			.png()
			.toFile(resolve(tauriTrayDir, `${stateName}.png`));
	}
	console.log("Created src-tauri/icons/tray/{idle,recording,transcribing,error}.png");

	// macOS template icon (white bars + alpha). The Rust host calls
	// `.icon_as_template(true)` on macOS (gated by
	// `cfg!(target_os = "macos")` in `tray.rs`), which tells the OS
	// to render the icon as a single-color alpha mask. The OS
	// applies the menubar color (black on light menubar, white on
	// dark menubar) and uses the alpha channel as the shape mask.
	// The source PNG must be a single color with alpha — white bars
	// on transparent background is the conventional choice (matches
	// what Apple's own SF Symbols use for template images).
	//
	// This file is currently NOT loaded by the Rust host — the
	// state icons (idle/recording/transcribing/error) are loaded
	// instead, and `icon_as_template(true)` is applied to whatever
	// icon is currently set. The state icons' alpha channel (the bar
	// shapes) becomes the template mask; the color is ignored on
	// macOS. This file is shipped as a documented fallback for a
	// future "always use template on macOS regardless of state"
	// mode (where the state would be communicated via tooltip only).
	await sharp(Buffer.from(traySvg))
		.resize(trayIconSize, trayIconSize)
		.png()
		.toFile(resolve(tauriTrayDir, "tray-mic-template.png"));
	console.log("Created src-tauri/icons/tray/tray-mic-template.png (macOS template source)");
}

async function main() {
	const rawSvg = readFileSync(svgPath, "utf-8");
	// The source SVG uses currentColor — replace with explicit colors for rendering
	const lightSvg = rawSvg.replace(/currentColor/g, "black");
	const darkSvg = rawSvg.replace(/currentColor/g, "white");

	// Tauri host icons dir — computed once at the top so the --tray
	// fast path can target it directly.
	const tauriIconsDir = resolve(projectRoot, "src-tauri", "icons");

	// --tray: regenerate ONLY the Tauri tray state icons and exit.
	// Used by scripts/build/generate_tray_icons.py so a tray-icon
	// change is repeatable without touching the Electron / server /
	// bundle-icon sets.
	if (process.argv.includes("--tray")) {
		await generateTauriTrayIcons(tauriIconsDir);
		console.log("\nAll tray icons generated successfully.");
		return;
	}

	// Light icons (black logo on transparent)
	await generateIcons(lightSvg, "light", "");
	// Dark icons (white logo on transparent)
	await generateIcons(darkSvg, "dark", "-dark");

	// .ico generation
	const resourcesDir = resolve(clientDir, "resources");
	await generateIco(
		resolve(resourcesDir, "icon-256.png"),
		resolve(resourcesDir, "icon.ico"),
	);
	await generateIco(
		resolve(resourcesDir, "icon-dark-256.png"),
		resolve(resourcesDir, "icon-dark.ico"),
	);

	// Tray icons (transparent background, white bars for colorization)
	const traySvg = `<svg width="148" height="148" viewBox="0 0 148 148" fill="none" xmlns="http://www.w3.org/2000/svg"><rect x="18.5" y="55.5" width="18.5" height="37" rx="9.25" fill="white"/><rect x="49.3333" y="37" width="18.5" height="74" rx="9.25" fill="white"/><rect x="80.1667" y="18.5" width="18.5" height="111" rx="9.25" fill="white"/><rect x="111" y="45.0938" width="18.5" height="57.8125" rx="9.25" fill="white"/></svg>`;

	const trayDir = resolve(projectRoot, "voice_typer", "server", "assets");
	mkdirSync(trayDir, { recursive: true });
	for (const size of sizes.tray) {
		await sharp(Buffer.from(traySvg))
			.resize(size, size)
			.png()
			.toFile(resolve(trayDir, `tray-mic-${size}.png`));
	}
	await sharp(Buffer.from(traySvg))
		.resize(64, 64)
		.png()
		.toFile(resolve(trayDir, "tray-mic.png"));
	console.log("Created server/assets/tray-mic-*.png");

	// PLAT-024: Generate a multi-size ICO file for the tray icon so
	// Windows 11 can use the native ICO format (sharper than PNG under
	// per-monitor DPI scaling). The ICO contains 16/24/32/48/64 sizes.
	// tray_icon.py looks for ``tray-mic.png`` (the base icon) and
	// colorizes it at runtime per AppState; we generate the ICO from
	// the same base PNG so the runtime conversion path can be skipped
	// on Windows when the base ICO is present.
	await generateIco(
		resolve(trayDir, "tray-mic.png"),
		resolve(trayDir, "tray-mic.ico"),
	);
	console.log("Created server/assets/tray-mic.ico (PLAT-024)");

	// Logo PNGs for Python server (transparent background)
	for (const size of [64, 256]) {
		await sharp(Buffer.from(lightSvg))
			.resize(size, size)
			.png()
			.toFile(resolve(trayDir, `logo-${size}.png`));
	}
	console.log("Created server/assets/logo-*.png");

	// Tauri host icons (src-tauri/icons/). These are referenced by
	// `bundle.icon` in tauri.conf.json AND by `TrayIconBuilder` at
	// runtime via `app.default_window_icon()`. Without them,
	// `tauri::generate_context!()` (the proc macro in main.rs)
	// panics at compile time with "failed to open icon
	// src-tauri/icons/32x32.png".
	//
	// Sizes:
	//   - 32x32.png      — small Linux window icon (X11 _NET_WM_ICON fallback)
	//   - 128x128.png    — standard window icon (Windows + Linux)
	//   - 128x128@2x.png — Retina/HiDPI window icon (macOS + Linux Wayland)
	//   - icon.png       — large 512x512 source icon (macOS .iconset)
	//
	// All four use the same black-on-transparent light SVG so the
	// brand mark is consistent across the dock/taskbar/menubar.
	mkdirSync(tauriIconsDir, { recursive: true });
	await sharp(Buffer.from(lightSvg)).resize(32, 32).png()
		.toFile(resolve(tauriIconsDir, "32x32.png"));
	await sharp(Buffer.from(lightSvg)).resize(128, 128).png()
		.toFile(resolve(tauriIconsDir, "128x128.png"));
	await sharp(Buffer.from(lightSvg)).resize(256, 256).png()
		.toFile(resolve(tauriIconsDir, "128x128@2x.png"));
	await sharp(Buffer.from(lightSvg)).resize(512, 512).png()
		.toFile(resolve(tauriIconsDir, "icon.png"));
	console.log("Created src-tauri/icons/{32x32,128x128,128x128@2x,icon}.png");

	// Tauri tray state icons (see generateTauriTrayIcons above — the
	// comment block moved there with the function).
	await generateTauriTrayIcons(tauriIconsDir);

	console.log("\nAll icons generated successfully.");
}

main().catch(console.error);
