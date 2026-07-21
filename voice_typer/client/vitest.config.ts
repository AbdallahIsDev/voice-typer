/// <reference types="vitest" />

import { resolve } from "node:path";
import { defineConfig } from "vitest/config";

// BUILD-N11: vitest configuration for the renderer + main process.
//
// Renderer tests use jsdom so React Testing Library can mount components
// that depend on `window` / `document`. The alias block mirrors
// vite.config.ts so imports like `#components` work in tests.
//
// Main-process tests (`src/main/**`) opt into a node environment via the
// per-file `// @vitest-environment node` directive (see
// `src/main/__tests__/bootstrap.test.ts`). They test fs-based code paths
// that have no DOM dependency and would error out under jsdom's
// `window` shims.
export default defineConfig({
	test: {
		environment: "jsdom",
		globals: true,
		setupFiles: ["./src/renderer/src/test-setup.ts"],
		include: [
			"src/renderer/src/**/*.{test,spec}.{ts,tsx}",
			"src/main/**/*.{test,spec}.ts",
		],
		coverage: {
			provider: "v8",
			reporter: ["text", "html"],
			include: ["src/renderer/src/**/*.{ts,tsx}"],
			exclude: ["**/*.test.{ts,tsx}", "**/*.spec.{ts,tsx}"],
		},
	},
	resolve: {
		alias: {
			"@": resolve(__dirname, "src/renderer/src"),
			"#components": resolve(__dirname, "src/renderer/src/components/index.ts"),
			"#ui": resolve(__dirname, "src/renderer/src/components/ui"),
			"#lib": resolve(__dirname, "src/renderer/src/lib/index.ts"),
			"#utils": resolve(__dirname, "src/renderer/src/lib/utils.ts"),
			"#hooks": resolve(__dirname, "src/renderer/src/hooks/index.ts"),
		},
	},
});
