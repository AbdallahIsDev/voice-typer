import path from "node:path";
import { fileURLToPath } from "node:url";
import tailwind from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
// Vite config for the TAURI shell (dev + production).
//
// Dev (ADR-0020 §7 devUrl: http://localhost:1420): `tauri-dev.mjs`
// starts this config's dev server; the committed `tauri.dev.conf.json`
// override blanks `build.beforeDevCommand` because the stock
// `cd voice_typer/client && ...` cannot resolve under the tauri CLI's
// CWD on Windows (reproduced 2026-08-30; the stock literal stays
// pinned in tauri.conf.json for CI builds, which DO resolve it).
//
// Production (`npm run build:renderer`, also `beforeBuildCommand` in
// tauri.conf.json): emits multi-page output (index + bubble) into
// `out/renderer`, the path tauri.conf.json `frontendDist` reads.
//
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

export default defineConfig(({ command }) => ({
	// prevent Vite from obscuring rust errors (official Tauri template)
	clearScreen: false,
	root: path.resolve(__dirname, "src/renderer"),
	plugins: [react(), tailwind(), cspEmissionPlugin()],
	resolve: {
		alias: { ...aliases },
	},
	server: {
		// devUrl is pinned to 1420, fail loudly instead of drifting
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
		// Matches tauri.conf.json `frontendDist`:
		// `../voice_typer/client/out/renderer`.
		outDir: path.resolve(__dirname, "out/renderer"),
		emptyOutDir: true,
		// Multi-page: the bubble window loads bubble.html from the
		// same dist. Mirrors the former electron.vite.renderer.ts inputs.
		rollupOptions: {
			input: {
				index: path.resolve(__dirname, "src/renderer/index.html"),
				bubble: path.resolve(__dirname, "src/renderer/bubble.html"),
			},
			// Isolate large vendor deps + the Tauri bridge so the
			// entry chunk stays small and vendor chunks fetch in parallel.
			output: {
				manualChunks: (moduleId: string) => {
					if (moduleId.includes("src/renderer/src/lib/tauri-bridge/")) {
						return "tauri-bridge";
					}
					if (
						moduleId.includes("node_modules/react-dom/") ||
						moduleId.includes("node_modules/react/")
					) {
						return "vendor-react";
					}
					if (moduleId.includes("node_modules/radix-ui/")) {
						return "vendor-radix";
					}
					if (moduleId.includes("node_modules/@hugeicons/react/")) {
						return "vendor-icons";
					}
					return undefined;
				},
			},
		},
		// WebView2 on Windows is Chromium (chrome105 is the official
		// Tauri v2 floor). The previous official-template `safari13`
		// fallback is too old for this codebase: React 19 + top-level
		// await (`await ensureTauriBridgeInstalled()`) both require a
		// modern target. chrome105 is supported by Windows WebView2
		// and recent macOS/Linux WebKit.
		target: "chrome105",
		// Production builds ship without sourcemaps (R6-F13).
		sourcemap: command === "serve",
		// don't minify for debug builds
		minify: !process.env.TAURI_ENV_DEBUG ? "esbuild" : false,
		// raise the chunk-size warning limit (radix-ui / hugeicons /
		// tauri-bridge chunks exceed Vite's 500 KB default).
		chunkSizeWarningLimit: 600,
		logLevel: process.stdout.isTTY ? "info" : "silent",
	},
}));
