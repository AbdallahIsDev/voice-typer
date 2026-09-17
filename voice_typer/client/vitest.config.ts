/// <reference types="vitest" />

import { defineConfig } from "vitest/config";
import { aliases } from "./aliases";

// BUILD-N11: vitest configuration for the renderer + shared modules.
//
// Renderer tests use jsdom so React Testing Library can mount components
// that depend on `window` / `document`. The alias block mirrors the
// production build configs (vite.tauri.config.ts, vite.config.ts):
// only the `@`, `#ui`, and `#utils` aliases are kept here because they
// are the only ones with real backing files.
//
// Globals are intentionally OFF: every test file imports
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
		// Keep per-file V8 isolation ON (vitest default) so the module
		// registry, the jsdom window globals, and vi mock state never leak
		// across test files that share a reused worker thread.
		// `isolate: false` caused nondeterministic cross-file pollution on CI.
		pool: "threads",
		isolate: true,
		// clear mock.calls / mock.results before every test.
		// `clearMocks: true` resets only the call history, it does NOT
		// reset implementations (so `vi.fn(() => x)` keeps its impl) and
		// does NOT restore originals (so `vi.spyOn(obj, "m")` stays
		// spied). This is the safe middle ground between "do nothing"
		// (mock state leaks across tests when files share a worker
		// thread) and `resetMocks: true` / `restoreMocks: true` (which
		// would wipe `vi.fn` defaults mid-test and break `vi.spyOn`
		// spies that rely on `.mockImplementation` set in `beforeEach`).
		clearMocks: true,
		environment: "jsdom",
		setupFiles: ["./src/renderer/src/test-setup.ts"],
		// Generous timeouts: many tests wait on async IPC round-trips
		// through the mocked `window.python.call` bridge, and CI runners
		// are often slow under load.
		testTimeout: 10000,
		hookTimeout: 20000,
		include: [
			"src/renderer/src/**/*.{test,spec}.{ts,tsx}",
			// Shared cross-scope modules (export-format,
			// python-call-error-code, ...) carry their own
			// contract/parity guards under src/shared/__tests__.
			"src/shared/**/*.{test,spec}.ts",
		],
		coverage: {
			provider: "istanbul",
			reporter: ["text", "html"],
			include: ["src/renderer/src/**/*.{ts,tsx}", "csp-plugin.ts"],
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
			],
			// Use a floor on coverage so deletions / untested branches
			// surface in CI rather than silently rotting. Thresholds are
			// deliberately conservative, raising them is encouraged as
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
