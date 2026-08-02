/// <reference types="vitest" />

import { defineConfig } from "vitest/config";
import { aliases } from "./aliases";

// BUILD-N11: vitest configuration for the renderer + main process.
//
// Renderer tests use jsdom so React Testing Library can mount components
// that depend on `window` / `document`. The alias block mirrors the
// production build configs (electron.vite.config.ts, vite.config.ts):
// only the `@`, `#ui`, and `#utils` aliases are kept here because they
// are the only ones with real backing files. The stale `#components`,
// `#lib`, and `#hooks` aliases (which pointed at non-existent barrel
//files) were removed in  — code uses `@/components/...`,
// `@/lib/...`, and `@/hooks/...` instead.
//
// Main-process tests (`src/main/**`) opt into a node environment via the
// per-file `// @vitest-environment node` directive (see
// `src/main/__tests__/bootstrap.test.ts`). They test fs-based code paths
// that have no DOM dependency and would error out under jsdom's
// `window` shims.
//
//Globals are intentionally OFF (): every test file imports
// `describe`/`it`/`expect`/`vi` explicitly from "vitest", so the global
// injection just adds hidden coupling to vitest's runtime types and
// makes it harder to swap the test runner. Turning it off surfaces any
// accidental implicit-globals usage at compile time.
export default defineConfig({
	test: {
		// PERF-007: use worker threads instead of child process forks.
		// Threads share memory (no IPC serialization) and avoid the ~200ms
		// per-file fork overhead. On a 237-file suite this cuts wall time
		// by 2-4x. Worker threads are safe here because:
		//   - jsdom is thread-safe (each thread gets its own DOM)
		//   - vi.mock/hoisted mocks are per-file, not global
		//   - the test-setup.ts afterEach cleanup resets DOM + localStorage
		// Main-process tests that use `// @vitest-environment node` are
		// also thread-safe (fs operations are synchronous).
		pool: "threads",
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
			provider: "istanbul",
			reporter: ["text", "html"],
			// vitest 4.x dropped the `all: true` option for the v8
			// provider — it only reports executed files, so untested
			// files are excluded from the denominator and the threshold
			// can't detect "new file without tests". Switching to the
			// istanbul provider (`@vitest/coverage-istanbul`) which
			// instruments all files matching the `include` glob and
			// reports 0% coverage for untested files, making the
			// threshold a true gate.
			// The expanded `include` glob below guarantees that
			// every file matching the patterns is counted.
			include: [
				"src/renderer/src/**/*.{ts,tsx}",
				"src/main/**/*.ts",
				"src/preload/**/*.ts",
				// csp-plugin.ts lives at the client root (not under src/) but is
				// exercised via main-process tests + production build. Include it
				// explicitly so its branches count toward the threshold.
				"csp-plugin.ts",
			],
			exclude: [
				"**/*.test.{ts,tsx}",
				"**/*.spec.{ts,tsx}",
				// Storybook stories are visual fixtures, not unit-testable code.
				"**/*.stories.tsx",
				// Test helpers / fixtures are imported only by tests; counting
				// them against production coverage would penalize the threshold
				// for code that never ships.
				"**/__tests__/helpers/**",
				// The vitest setup file wires jsdom polyfills; it runs before
				// every test but is not production code.
				"**/test-setup.ts",
				// Type-only / declaration files have no runtime to cover.
				"**/*.d.ts",
				// Bundle entrypoints are exercised end-to-end via
				// electron-vite builds, not via unit coverage.
				"src/main/index.ts",
				"src/preload/index.ts",
			],
			//floor coverage so deletions / untested branches
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
		alias: { ...aliases },
	},
});
