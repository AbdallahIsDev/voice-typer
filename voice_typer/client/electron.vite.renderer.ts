import { resolve } from "node:path";
import tailwind from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "electron-vite";
import { aliases } from "./aliases";
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
		// LOG-CLEAN: non-TTY (CI pipes, launcher log-file redirects) →
		// silence Vite build chatter; errors still print at every logLevel.
		logLevel: process.stdout.isTTY ? "info" : "silent",
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
				// keep in sync with electron.vite.config.ts —
				// split react/react-dom, radix-ui, @hugeicons/react, and
				// the Tauri <-> React bridge (src/renderer/src/lib/tauri-bridge/
				// + its @tauri-apps/api dep graph) into separate chunks for
				// parallel fetch + smaller per-chunk parse cost. See
				// electron.vite.config.ts for the full rationale. This CI-only
				// config MUST mirror the renderer section of
				// electron.vite.config.ts so the CI client-build job produces
				// byte-identical chunk layout to the local electron-vite build.
				output: {
					manualChunks: (moduleId: string) => {
						// Isolate the Tauri <-> React bridge
						// (src/renderer/src/lib/tauri-bridge/) and its
						// @tauri-apps/api dep graph (~1.4 MB) into a dedicated
						// chunk. MUST mirror electron.vite.config.ts — see the
						// rationale there.
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
		},
		plugins: [react(), tailwind(), cspEmissionPlugin()],
		resolve: {
			alias: { ...aliases },
		},
	},
}));
