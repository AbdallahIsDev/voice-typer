import { resolve } from "node:path";
import tailwind from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, externalizeDepsPlugin } from "electron-vite";

export default defineConfig({
	main: {
		plugins: [externalizeDepsPlugin()],
		build: {
			rollupOptions: {
				input: { index: resolve(__dirname, "src/main/index.ts") },
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
	renderer: {
		root: resolve(__dirname, "src/renderer"),
		build: {
			rollupOptions: {
				input: {
					index: resolve(__dirname, "src/renderer/index.html"),
					bubble: resolve(__dirname, "src/renderer/bubble.html"),
				},
			},
		},
		plugins: [react(), tailwind()],
		resolve: {
			alias: {
				"@": resolve(__dirname, "src/renderer/src"),
				// NEW-TS-016: removed non-existent barrel file aliases
				// (#components, #lib, #hooks). Code uses @/... instead.
				"#ui": resolve(__dirname, "src/renderer/src/components/ui"),
				"#utils": resolve(__dirname, "src/renderer/src/lib/utils.ts"),
			},
		},
	},
});
