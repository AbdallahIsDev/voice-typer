import { resolve } from "node:path";
import tailwind from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, externalizeDepsPlugin } from "electron-vite";
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
					// SEC-026: split the preload into a main-only and a bubble-only
					// build. The bubble renderer gets a much smaller surface (only
					// bubble:level / bubble:show / bubble:hide / bubble:draggable /
					// bubble:position / bubble:drag*), so a compromised bubble can't
					// invoke python.call({type:"quit_app"}) or window_.close().
					index: resolve(__dirname, "src/preload/index.ts"),
					bubble: resolve(__dirname, "src/preload/bubble.ts"),
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
			rollupOptions: {
				input: {
					index: resolve(__dirname, "src/renderer/index.html"),
					bubble: resolve(__dirname, "src/renderer/bubble.html"),
				},
			},
		},
		plugins: [react(), tailwind(), cspEmissionPlugin()],
		resolve: {
			alias: {
				"@": resolve(__dirname, "src/renderer/src"),
				// @server removed — resolved outside renderer root and
				// crashed Vite HMR on locale switch. The JSON copy at
				// src/renderer/src/data/ is imported with a project-relative
				// path instead.
				// NEW-TS-016: removed non-existent barrel file aliases
				// (#components, #lib, #hooks). Code uses @/... instead.
				"#ui": resolve(__dirname, "src/renderer/src/components/ui"),
				"#utils": resolve(__dirname, "src/renderer/src/lib/utils.ts"),
			},
		},
	},
}));
