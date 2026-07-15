import { resolve } from "node:path";
import { defineConfig, externalizeDepsPlugin } from "electron-vite";

// CI-05: main + preload build config (no renderer). Used by
// `npm run build:main` in CI platform build jobs. The renderer
// is built separately by `client-build` and shared via artifact.
export default defineConfig({
	main: {
		plugins: [externalizeDepsPlugin()],
		build: {
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
