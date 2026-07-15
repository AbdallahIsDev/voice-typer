import { resolve } from "node:path";
import tailwind from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "electron-vite";
import { cspEmissionPlugin } from "./csp-plugin";

// CI-05: renderer-only build config. Used by `npm run build:renderer` in
// the CI `client-build` job. The renderer output is platform-independent
// and can be shared across platform build jobs, saving ~30s per build.
export default defineConfig({
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
		plugins: [react(), tailwind(), cspEmissionPlugin()],
		resolve: {
			alias: {
				"@": resolve(__dirname, "src/renderer/src"),
				"#ui": resolve(__dirname, "src/renderer/src/components/ui"),
				"#utils": resolve(__dirname, "src/renderer/src/lib/utils.ts"),
			},
		},
	},
});
