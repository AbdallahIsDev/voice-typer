import path from "node:path";
import { fileURLToPath } from "node:url";
import tailwind from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
// Dev-only Vite config for the TAURI shell (ADR-0020 §7 devUrl:
// http://localhost:1420). The stock `beforeDevCommand` builds a static
// out/renderer snapshot — no HMR — and the tauri CLI cannot execute it
// on Windows (CWD bug: "The system cannot find the path specified"), so
// `tauri dev` is launched with an override that blanks beforeDevCommand
// and expects THIS server to be listening on 1420 first.
//
// Mirrors the renderer section of electron.vite.renderer.ts (same
// plugins + the shared aliases.ts single source of truth) so the
// Tauri webview renders identically to the Electron dev build.
// Server block follows the OFFICIAL Tauri v2 Vite template
// (v2.tauri.app/start/frontend/vite): clearScreen off, strict port,
// TAURI_DEV_HOST-aware host/HMR, and src-tauri excluded from the
// watcher (the tauri CLI owns Rust rebuilds).
// CSP: reuses cspEmissionPlugin, whose serve-mode CSP_DEV already
// allows `connect-src ws://localhost:*` for the HMR websocket and
// `unsafe-eval`/`unsafe-inline` for the React-Refresh preamble.
import { defineConfig } from "vite";

import { aliases } from "./aliases";
import { cspEmissionPlugin } from "./csp-plugin";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// https://vite.dev/config/
const host = process.env.TAURI_DEV_HOST;

export default defineConfig({
	// prevent Vite from obscuring rust errors (official Tauri template)
	clearScreen: false,
	root: path.resolve(__dirname, "src/renderer"),
	plugins: [react(), tailwind(), cspEmissionPlugin()],
	resolve: {
		alias: { ...aliases },
	},
	server: {
		// devUrl is pinned to 1420 — fail loudly instead of drifting
		// to 1421 (strictPort is the official template contract).
		port: 1420,
		strictPort: true,
		host: host || false,
		hmr: host
			? {
					protocol: "ws",
					host,
					port: 1421,
				}
			: undefined,
		watch: {
			// the tauri CLI watches src-tauri itself (cargo rebuild);
			// watching it here too would double-trigger reloads.
			ignored: ["**/src-tauri/**"],
		},
	},
	// Expose TAURI_ENV_* platform variables to the renderer via
	// import.meta.env (official Tauri template contract).
	envPrefix: ["VITE_", "TAURI_ENV_*"],
	build: {
		// Tauri uses Chromium (WebView2) on Windows and WebKit elsewhere.
		target:
			process.env.TAURI_ENV_PLATFORM === "windows" ? "chrome105" : "safari13",
		// don't minify for debug builds
		minify: !process.env.TAURI_ENV_DEBUG ? "esbuild" : false,
		// produce sourcemaps for debug builds
		sourcemap: !!process.env.TAURI_ENV_DEBUG,
	},
});
