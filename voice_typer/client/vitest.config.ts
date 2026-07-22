/// <reference types="vitest" />

import { resolve } from "node:path";
import { defineConfig } from "vitest/config";

// BUILD-N11: vitest configuration for the renderer + main process.
//
// Renderer tests use jsdom so React Testing Library can mount components
// that depend on `window` / `document`. The alias block mirrors the
// production build configs (electron.vite.config.ts, vite.config.ts):
// only the `@`, `#ui`, and `#utils` aliases are kept here because they
// are the only ones with real backing files. The stale `#components`,
// `#lib`, and `#hooks` aliases (which pointed at non-existent barrel
// files) were removed in PVT-075 — code uses `@/components/...`,
// `@/lib/...`, and `@/hooks/...` instead.
//
// Main-process tests (`src/main/**`) opt into a node environment via the
// per-file `// @vitest-environment node` directive (see
// `src/main/__tests__/bootstrap.test.ts`). They test fs-based code paths
// that have no DOM dependency and would error out under jsdom's
// `window` shims.
//
// Globals are intentionally OFF (PVT-075): every test file imports
// `describe`/`it`/`expect`/`vi` explicitly from "vitest", so the global
// injection just adds hidden coupling to vitest's runtime types and
// makes it harder to swap the test runner. Turning it off surfaces any
// accidental implicit-globals usage at compile time.
export default defineConfig({
	test: {
		environment: "jsdom",
		setupFiles: ["./src/renderer/src/test-setup.ts"],
		// Generous timeouts: many tests wait on async IPC round-trips
		// through the mocked `window.python.call` bridge, and CI runners
		// are often slow under load. 10s per test / 20s per hook keeps
		// the suite from flapping while still catching deadlocks.
		testTimeout: 10000,
		hookTimeout: 20000,
		include: [
			"src/renderer/src/**/*.{test,spec}.{ts,tsx}",
			"src/main/**/*.{test,spec}.ts",
			"src/preload/**/*.{test,spec}.ts",
		],
		coverage: {
			provider: "v8",
			reporter: ["text", "html"],
			include: [
				"src/renderer/src/**/*.{ts,tsx}",
				"src/main/**/*.ts",
				"src/preload/**/*.ts",
			],
			exclude: [
				"**/*.test.{ts,tsx}",
				"**/*.spec.{ts,tsx}",
				// Type-only / declaration files have no runtime to cover.
				"**/*.d.ts",
				// Bundle entrypoints are exercised end-to-end via
				// electron-vite builds, not via unit coverage.
				"src/main/index.ts",
				"src/preload/index.ts",
				"src/preload/bubble.ts",
			],
			// PVT-075: floor coverage so deletions / untested branches
			// surface in CI rather than silently rotting. Thresholds are
			// deliberately conservative — raising them is encouraged as
			// the suite grows, but lowering them requires justification.
			thresholds: {
				lines: 70,
				functions: 70,
				branches: 60,
				statements: 70,
			},
		},
	},
	resolve: {
		alias: {
			"@": resolve(__dirname, "src/renderer/src"),
			"#ui": resolve(__dirname, "src/renderer/src/components/ui"),
			"#utils": resolve(__dirname, "src/renderer/src/lib/utils.ts"),
		},
	},
});
