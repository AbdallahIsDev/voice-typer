import { resolve } from "node:path";
import tailwind from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "electron-vite";
import { cspEmissionPlugin } from "./csp-plugin";

// CI-05: renderer-only build config. Used by `npm run build:renderer` in
// the CI `client-build` job. The renderer output is platform-independent
// and can be shared across platform build jobs, saving ~30s per build.
//
// R6-F13 (security): keep in sync with electron.vite.config.ts —
// renderer sourcemaps ON in dev (command === "serve") so React DevTools +
// Vite HMR + stack traces work, OFF in production so component structure
// + inlined strings don't leak via the .asar.
export default defineConfig(({ command }) => ({
	renderer: {
		root: resolve(__dirname, "src/renderer"),
		build: {
			// R6-F13: dev gets sourcemaps for debugging; production
			// builds ship without them to avoid leaking renderer
			// source structure inside the .asar.
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
				"#ui": resolve(__dirname, "src/renderer/src/components/ui"),
				"#utils": resolve(__dirname, "src/renderer/src/lib/utils.ts"),
			},
		},
	},
}));
