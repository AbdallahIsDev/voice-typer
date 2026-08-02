import { resolve } from "node:path";
import tailwind from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, externalizeDepsPlugin } from "electron-vite";
import { aliases } from "./aliases";
import { cspEmissionPlugin } from "./csp-plugin";

// CI-05: electron.vite.renderer.ts and electron.vite.main.ts are CI-only
// copies of the renderer and main+preload sections respectively.
// Keep them in sync when modifying this file.
//
// R6-F13 (security): sourcemaps are disabled in production builds to avoid
// leaking main-process source (IPC handlers, ALLOWED_COMMANDS, TCP plumbing,
// auth tokens, internal API surface) to anyone who unzips the .asar. Vite's
// default is to emit sourcemaps for production bundles when not configured,
// which would expose the entire main-process module graph. The renderer
// keeps sourcemaps ONLY in dev (command === "serve") for the React DevTools
// + stack-trace debugging experience — production renderer builds ship
// without sourcemaps too, since renderer code reaches end-user machines
// inside the .asar and a sourcemap would expose component structure +
// any inlined secrets (e.g. theme tokens, fallback strings).
export default defineConfig(({ command }) => ({
	main: {
		plugins: [externalizeDepsPlugin()],
		build: {
			// R6-F13: never ship main-process sourcemaps — they expose
			// IPC handler addresses, the TCP auth flow, and the
			// ALLOWED_COMMANDS surface to anyone who unzips the app.
			sourcemap: false,
			rollupOptions: {
				input: { index: resolve(__dirname, "src/main/index.ts") },
				output: {
					format: "cjs",
				},
			},
		},
	},
	preload: {
		plugins: [externalizeDepsPlugin()],
		build: {
			// R6-F13: preload bundles expose the IPC bridge surface
			// (channel names + arg shapes). Shipping sourcemaps would
			// hand an attacker a map of every exposed IPC channel,
			// making sandbox-escape reconnaissance trivial.
			sourcemap: false,
			rollupOptions: {
				input: {
					// SEC-026: single preload entry for both main and bubble
					// windows.  At runtime the preload inspects the window
					// location to determine which APIs to expose — the full
					// surface for the main renderer, or only the bubble
					// subset for the bubble window.  A separate bubble entry
					// would force Rollup to create a shared chunk that
					// Electron's sandbox preloadRequire cannot load.
					index: resolve(__dirname, "src/preload/index.ts"),
				},
			},
		},
	},
	renderer: {
		root: resolve(__dirname, "src/renderer"),
		build: {
			// R6-F13: renderer sourcemaps ON in dev (so React DevTools
			// + Vite HMR + stack traces work), OFF in production (so
			// component structure + inlined strings don't leak via
			// the .asar). Vite emits sourcemaps for the dev server
			// automatically; this flag controls production emission.
			sourcemap: command === "serve",
			//raise the chunk-size warning limit from Vite's
			// default 500 KB to 600 KB so the renderer bundle's
			// larger chunks (radix-ui, hugeicons) don't print a noisy
			// warning on every build. The 1.4 MB tauri-bridge chunk
			// still warns — investigating why a bridge module pulls
			// in 1.4 MB of shared deps is deeper work (Remaining).
			chunkSizeWarningLimit: 600,
			rollupOptions: {
				input: {
					index: resolve(__dirname, "src/renderer/index.html"),
					bubble: resolve(__dirname, "src/renderer/bubble.html"),
				},
				// split large vendor deps into separate chunks so the
				// initial renderer bundle (entry + app code) is smaller and
				// downloads/parse in parallel with vendor chunks. Without
				// manualChunks, Rollup emits a single ~2 MB index.js
				// containing react + react-dom + radix-ui + @hugeicons/react
				// + all app code — the browser can't start parsing app code
				// until the entire monolith downloads.
				//
				// The function-form manualChunks routes each module id into
				// a named chunk. Each matched module (and its dep graph) is
				// extracted into the named chunk. The entry chunk imports
				// these vendor chunks synchronously, so they're still loaded
				// on the INITIAL page load (not lazy) — the win is parallel
				// fetch + smaller per-chunk parse cost, not deferred loading.
				//
				// react / react-dom go in vendor-react (kept eager —
				// the entry needs them to render). radix-ui (the umbrella
				// package re-exporting all Radix primitives) goes in
				// vendor-radix. @hugeicons/react (the icon runtime) goes
				// in vendor-icons.
				//
				// NOTE: keep in sync with electron.vite.renderer.ts (the
				// CI-only renderer build config). electron.vite.main.ts
				// has no renderer section so it doesn't need this.
				output: {
					manualChunks: (moduleId: string) => {
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
		},
		plugins: [react(), tailwind(), cspEmissionPlugin()],
		resolve: {
			alias: {
				...aliases,
				// @server removed — resolved outside renderer root and
				// crashed Vite HMR on locale switch. The JSON copy at
				// src/renderer/src/data/ is imported with a project-relative
				// path instead.
				//removed non-existent barrel file aliases
				// (#components, #lib, #hooks). Code uses @/... instead.
			},
		},
	},
}));
