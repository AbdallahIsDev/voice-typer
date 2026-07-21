import { resolve } from "node:path";
import { defineConfig, externalizeDepsPlugin } from "electron-vite";

// CI-05: main + preload build config (no renderer). Used by
// `npm run build:main` in CI platform build jobs. The renderer
// is built separately by `client-build` and shared via artifact.
//
// R6-F13 (security): keep in sync with electron.vite.config.ts —
// sourcemaps disabled in `main` and `preload` so production builds
// don't leak IPC handler addresses, the TCP auth flow, the
// ALLOWED_COMMANDS surface, or the preload IPC bridge surface to
// anyone who unzips the .asar. See electron.vite.config.ts for the
// full rationale.
export default defineConfig({
	main: {
		plugins: [externalizeDepsPlugin()],
		build: {
			// R6-F13: never ship main-process sourcemaps.
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
			// R6-F13: never ship preload sourcemaps — they expose every
			// exposed IPC channel name + arg shape.
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
});
