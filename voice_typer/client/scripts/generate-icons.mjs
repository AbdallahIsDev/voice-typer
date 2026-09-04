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

// ── Logo source + derived variants ─────────────────────────────────
// ``scripts/logo.svg`` is the single source of truth for the brand
// mark: rounded-rect chip + white glyph + brand-red indicator dot.
// EVERY icon set below is derived from that one file.
//
// Chrome colors (user decision 2026-09 — the fill stays the SAME in
// light and dark): the chip is the dark neutral #1a1b1e in both modes
// and the glyph is white in both — a constant dark-chipped mark on the
// taskbar / Alt-Tab / app icons / favicons regardless of OS theme.
// LIGHT and DARK each keep an INDEPENDENT chip + glyph constant below
// (both set to the same value today), so the two modes can be diverged
// later by editing ONE constant per mode and re-running the generators
// — no mode needs to be re-added from scratch.
//   - lightSvg — chip LIGHT_CHIP + glyph LIGHT_GLYPH: light-mode
//     chrome AND the static default (bundle/exe icons, shortcuts,
//     Task Manager / Alt+Tab when the app is closed).
//   - darkSvg  — chip DARK_CHIP + glyph DARK_GLYPH: dark-mode chrome.
//     Consumed by the Electron nativeTheme swap and the Tauri
//     ``theme_icon.rs`` window-icon swap while the app runs.
//   - tray glyph — the logo WITHOUT the background chip and WITHOUT
//     the indicator dot (just the glyph): the tray/notification mark
//     (user decision: the tray shows the bare icon — no background
//     chip, no red dot). The Python pystray host and the Tauri tray
//     state icons colorize this shape per AppState at render time.
const LIGHT_CHIP = "#1a1b1e"; // chip in the light-mode assets (user: the dark chip in BOTH modes)
const DARK_CHIP = "#1a1b1e"; // chip in the dark-mode assets
const LIGHT_GLYPH = "#ffffff"; // glyph in the light-mode assets
const DARK_GLYPH = "#ffffff"; // glyph in the dark-mode assets

/** Brand mark with an explicit chip + glyph fill. Re-targets the
 *  canonical logo.svg's background <rect> and glyph <path> fills so a
 *  chrome color can be changed in ONE place (the constants above)
 *  while logo.svg stays a static, rasterizable mark for `tauri icon`.
 *  Fails loud, not silent: if logo.svg formatting drifts so a fill
 *  can't be re-targeted, the generated icons would silently ship the
 *  wrong chrome. */
function variantSvg(raw, chip, glyph) {
	let out = raw.replace(
		/<rect[^>]*rx="48"[^>]*\/>/,
		`<rect width="256" height="256" rx="48" fill="${chip}"/>`,
	);
	out = setGlyphFill(out, glyph);
	if (!out.includes(`<rect width="256" height="256" rx="48" fill="${chip}"/>`)) {
		throw new Error(
			`variantSvg: chip fill not applied (${chip}) — logo.svg's <rect> drifted; ` +
				"update the rect regex",
		);
	}
	return out;
}

/** Re-fill the single glyph <path>'s fill attribute. Counts matches so
 *  a silently-failing replace (logo.svg's <path> drifted, or the fill
 *  regex no longer matches the source color) throws instead of shipping
 *  the wrong chrome — meaningful even when the target fill equals the
 *  source fill (today: white == white for both variants). */
function setGlyphFill(svg, fill) {
	let hits = 0;
	const out = svg.replace(
		/(<path[^>]*? d="[^"]*")\s*fill="[^"]*"(\s*\/>)/,
		(_m, p1, p2) => {
			hits += 1;
			return `${p1} fill="${fill}"${p2}`;
		},
	);
	if (hits !== 1) {
		throw new Error(
			`setGlyphFill: expected exactly 1 glyph <path>, replaced ${hits} — ` +
				"logo.svg's <path> drifted; update the path regex",
		);
	}
	return out;
}

/** The glyph <path> `d` from the canonical logo.svg. */
function glyphPath(svg) {
	const m = svg.match(/<path[^>]*\sd="([^"]+)"/);
	if (!m) throw new Error("logo.svg: glyph <path> not found — cannot derive icons");
	return m[1];
}

/** The logo with its background rect AND indicator dot removed — the
 *  tray/notification glyph (user decision: the tray shows the glyph
 *  alone — no background chip, no red dot). */
function trayGlyphSvg(svg) {
	const stripped = svg
		.replace(/^\s*<rect[^>]*\srx="48"[^>]*\/>\s*$/m, "")
		.replace(/^\s*<circle[^>]*\/>\s*$/m, "")
		.trim();
	// Fail loud, not silent: if logo.svg formatting ever drifts so the
	// background chip / indicator dot are no longer stripped, the tray
	// would silently regrow them. Guard both removals.
	if (/rx="48"/.test(stripped) || /<circle/.test(stripped)) {
		throw new Error(
			"trayGlyphSvg: background rect or indicator dot was not stripped " +
				"from logo.svg — update the strip regexes",
		);
	}
	return stripped;
}

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
		// Write a theme-aware favicon.svg that uses prefers-color-scheme
		// media query instead of currentColor, which doesn't reliably work
		// in SVG favicons. The chip + glyph flip with the OS theme; the
		// brand-red dot stays constant.
		const faviconSvg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" fill="none">
  <style>
    .bg { fill: ${LIGHT_CHIP}; }
    .glyph { fill: ${LIGHT_GLYPH}; }
    @media (prefers-color-scheme: dark) {
      .bg { fill: ${DARK_CHIP}; }
      .glyph { fill: ${DARK_GLYPH}; }
    }
  </style>
  <rect class="bg" width="256" height="256" rx="48"/>
  <path class="glyph" fill-rule="evenodd" clip-rule="evenodd" d="${glyphPath(svg)}"/>
  <circle cx="48" cy="48" r="16" fill="#E80000"/>
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
img = Image.open("${pngPath.replace(/\\\\/g, "/")}")
img.save("${icoPath.replace(/\\\\/g, "/")}", format="ICO", sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
print("ICO generated")
`;
	let lastErr = null;
	for (const py of candidates) {
		// Skip venv candidates (paths starting with . or /) that don't
		// exist on this platform — they would just produce a noisy error.
		if ((py.startsWith(".") || py.startsWith("/")) && !existsSync(py)) continue;
		try {
			execSync(`"${py}" -c "${icoScript.replace(/\"/g, '\\"')}"`, {
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
 * ``tray-mic-template.png``) from the app logo glyph + the shared
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
	// Tray glyph shape — the app logo WITHOUT its background rect and
	// WITHOUT the indicator dot (the bare glyph, per the user decision:
	// no background chip and no red dot in the tray). Derived from the
	// canonical ``scripts/logo.svg``, tinted per state below. Mirrors
	// the pystray server-asset tray glyph.
	const traySvg = trayGlyphSvg(readFileSync(svgPath, "utf-8"));

	// Tauri tray state icons (src-tauri/icons/tray/). The Rust host
	// (`src-tauri/src/tray.rs::load_tray_icon`) whitelists icon
	// names to EXACTLY `idle` / `recording` / `transcribing` /
	// `error` (matching the Python `tray.py::_APP_STATE_TO_ICON_NAME`
	// mapping). Each PNG is the logo glyph colorized per AppState —
	// mirrors the Python `_make_icon` palette at
	// `tray_icon.py:302-309` so the visual states are identical
	// across the pystray (Python) and Tauri (Rust) hosts.
	//
	// Color palette (RGBA, alpha = 255 for full opacity):
	//   idle         — (120, 120, 120)   gray
	//   recording    — (46, 204, 113)    bright green (color-blind safe)
	//   transcribing — (52, 152, 219)    blue
	//   error        — (231, 76, 60)     red
	//
	// The glyph is rendered on a transparent background so the OS
	// tray chrome (light/dark menubar, taskbar notch) shows through.
	// On macOS, `icon_as_template(true)` is set in the Rust
	// TrayIconBuilder chain — the OS applies the menubar color and
	// uses the alpha channel as a mask, so the per-state color is
	// only visible on Windows/Linux. macOS users get the glyph SHAPE
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
	// Render the glyph DIRECTLY in the state color by injecting the
	// color into the SVG fill (the tray glyph's only colored element is
	// the logo glyph — the background chip and indicator dot are
	// stripped from the tray shape, so the whole glyph carries the
	// state color). (The previous sharp `tint` approach silently
	// produced IDENTICAL icons for every state: tint preserves
	// luminance, so tinting the white glyph is a no-op on the RGB
	// channels. Injecting the fill color is exact; `setGlyphFill` counts
	// the replacement so a glyph whose fill no longer matches the regex
	// fails loud instead of silently shipping uncolored states.)
	const trayIconSize = 32;
	for (const [stateName, color] of Object.entries(trayStateColors)) {
		const coloredSvg = setGlyphFill(traySvg, `rgb(${color.r}, ${color.g}, ${color.b})`);
		await sharp(Buffer.from(coloredSvg))
			.resize(trayIconSize, trayIconSize)
			.png()
			.toFile(resolve(tauriTrayDir, `${stateName}.png`));
	}
	console.log("Created src-tauri/icons/tray/{idle,recording,transcribing,error}.png");

	// macOS template icon (white glyph + alpha). The Rust host calls
	// `.icon_as_template(true)` on macOS (gated by
	// `cfg!(target_os = "macos")` in `tray.rs`), which tells the OS
	// to render the icon as a single-color alpha mask. The OS
	// applies the menubar color (black on light menubar, white on
	// dark menubar) and uses the alpha channel as the shape mask.
	// The source PNG must be a single color with alpha — white glyph
	// on transparent background is the conventional choice (matches
	// what Apple's own SF Symbols use for template images).
	//
	// This file is currently NOT loaded by the Rust host — the
	// state icons (idle/recording/transcribing/error) are loaded
	// instead, and `icon_as_template(true)` is applied to whatever
	// icon is currently set. The state icons' alpha channel (the
	// glyph shapes) becomes the template mask; the color is ignored
	// on macOS. This file is shipped as a documented fallback for a
	// future "always use template on macOS regardless of state"
	// mode (where the state would be communicated via tooltip only).
	const whiteTraySvg = setGlyphFill(traySvg, "white");
	await sharp(Buffer.from(whiteTraySvg))
		.resize(trayIconSize, trayIconSize)
		.png()
		.toFile(resolve(tauriTrayDir, "tray-mic-template.png"));
	console.log("Created src-tauri/icons/tray/tray-mic-template.png (macOS template source)");
}

async function main() {
	const rawSvg = readFileSync(svgPath, "utf-8");
	// Brand chrome colors. BOTH the light and the dark variants carry
	// the SAME chip + glyph today — #1a1b1e chip + white glyph (user
	// decision 2026-09: the taskbar / Alt-Tab / app icons are a
	// constant dark-chipped mark in light and dark OS themes alike;
	// the brand-red dot is deliberately NOT inverted — it stays the
	// same red in both modes). Each mode keeps its own constants above
	// so the two looks can be diverged later by editing ONE line per
	// mode and re-running this script (+ `tauri icon` for the bundle
	// set) — no mode needs to be re-added from scratch.
	const lightSvg = variantSvg(rawSvg, LIGHT_CHIP, LIGHT_GLYPH);
	const darkSvg = variantSvg(rawSvg, DARK_CHIP, DARK_GLYPH);

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

	// Light icons (chip LIGHT_CHIP + glyph LIGHT_GLYPH — static default + light chrome)
	await generateIcons(lightSvg, "light", "");
	// Dark icons (dark chip + white glyph — dark chrome)
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

	// Tray icons (transparent background, logo glyph — white for the
	// Python host's runtime per-state colorization, which uses only
	// the alpha channel)
	const traySvg = setGlyphFill(trayGlyphSvg(rawSvg), "white");

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

	// Logo PNGs for Python server (the LIGHT variant — the logo WITH
	// its background chip — used e.g. for desktop shortcuts)
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
	// All four use the LIGHT variant (#1a1b1e chip + white glyph + red
	// dot — the SAME look as the dark variant today) — the static
	// default shown on the taskbar / Alt-Tab / Task Manager / pinned
	// shortcuts when the app is NOT running.
	// While the app RUNS, `src-tauri/src/theme_icon.rs` swaps the
	// main-window icon between this light asset and the dark variant
	// (`src-tauri/theme-icons/icon-dark-512.png`, written below)
	// following the OS theme (both currently identical).
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

	// Dark-variant window icon for `theme_icon.rs` (OS-theme-reactive
	// window icon — TR-4). This is the dark-mode chrome: dark chip +
	// white glyph + red dot. It lives OUTSIDE `icons/` on purpose:
	// `scripts/build/generate_tauri_icons.py::prune` deletes
	// everything under `icons/` except the `bundle.icon` keep-set.
	const themeIconsDir = resolve(projectRoot, "src-tauri", "theme-icons");
	mkdirSync(themeIconsDir, { recursive: true });
	await sharp(Buffer.from(darkSvg)).resize(512, 512).png()
		.toFile(resolve(themeIconsDir, "icon-dark-512.png"));
	console.log("Created src-tauri/theme-icons/icon-dark-512.png");

	// Tauri tray state icons (see generateTauriTrayIcons above — the
	// comment block moved there with the function).
	await generateTauriTrayIcons(tauriIconsDir);

	console.log("\nAll icons generated successfully.");
}

main().catch(console.error);
