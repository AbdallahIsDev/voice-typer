/**
 * PVT-076 — shared `renderApp` helper.
 *
 * Mounting the real `App.tsx` shell requires stubbing ~10 hooks + child
 * components so the render graph doesn't pull in the entire renderer
 * (which would force every test to mock every page's IPC dependencies).
 * That boilerplate was duplicated across `__tests__/App.test.tsx`,
 * `__tests__/App-ux-fixes.test.tsx`, and several `rw0-rewrite`/`
 * rw1-rewrite` files.
 *
 * `renderApp` centralises the stubbing. Tests that just need to assert
 * on App-level routing / chrome can call:
 *
 *   const { getByText } = await renderApp({ currentPage: "settings" });
 *
 * Tests that need to assert on a SPECIFIC child page's behaviour should
 * mount that page directly (e.g. `render(<SettingsPage />)`) —
 * `renderApp` deliberately stubs the child pages so App-level tests
 * stay focused.
 *
 * The stubs live in a `vi.mock` factory, which vitest hoists to the top
 * of the file. As a result, `renderApp` MUST be imported BEFORE any
 * test-level `vi.mock` for the same modules — otherwise vitest will
 * warn about the duplicate mock registration. In practice, tests import
 * `renderApp` and let it own the App-level mocks, then add their own
 * `vi.mock` calls only for modules `renderApp` doesn't already cover.
 */
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { vi } from "vitest";

import type { VoiceTyperConfig } from "@/types/config";
import type { Page } from "@/types/ipc";
import { makeConfig } from "./fixtures";

// `vi.hoisted` runs before any `vi.mock` factory, so the values it
// returns are available inside factory bodies. We use it to expose
// mutable state (the configured page, the mocked `call` fn, etc.) so
// tests can reconfigure without re-importing.
//
// Explicit function-type annotations on the `vi.fn()` calls keep
// vitest's mock-type inference from collapsing to `Mock<Constructable
// | Procedure>` (the constructor-or-procedure union), which doesn't
// satisfy `mockImplementation`'s expected `(...args: any[]) => any`
// parameter. See FIX-18 / PVT-076.
const hoisted = vi.hoisted(() => ({
	mockCall: vi.fn() as unknown as ReturnType<typeof vi.fn>,
	mockPythonEvent: vi.fn() as unknown as ReturnType<typeof vi.fn>,
	mockNavigate: vi.fn() as unknown as ReturnType<typeof vi.fn>,
	currentPage: "home" as Page,
	mockConfig: null as VoiceTyperConfig | null,
}));

export const appRenderMocks = {
	mockCall: hoisted.mockCall,
	mockPythonEvent: hoisted.mockPythonEvent,
	mockNavigate: hoisted.mockNavigate,
};

// ── Hoisted module mocks (these run at import time) ────────────────────
// Only mock the hooks/chrome that App.tsx pulls in directly. The child
// pages are mocked separately inside `renderApp` (per call) so each test
// can pick the stubs it needs.

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: hoisted.mockCall }),
	usePythonEvent: hoisted.mockPythonEvent,
}));

vi.mock("@/hooks/useConnection", () => ({
	useConnection: () => ({
		recordingState: "idle" as const,
		connectionStatus: "connected" as const,
		lastError: null,
		handleRetryConnection: vi.fn(),
	}),
}));

vi.mock("@/hooks/useTheme", () => ({
	useTheme: () => ({
		themeMode: "system" as const,
		handleThemeChange: vi.fn(),
		reloadThemeFromConfig: vi.fn(),
		textSize: 14,
		setTextSize: vi.fn(),
	}),
}));

vi.mock("@/hooks/useSoundFeedback", () => ({
	useSoundFeedback: () => {},
}));

vi.mock("@/components/feedback/ErrorBoundary", () => ({
	ErrorBoundary: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

vi.mock("@/components/ui/sonner", () => ({
	Toaster: () => null,
}));

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: () => <span data-testid="hugeicon" />,
}));

vi.mock("sonner", () => ({
	toast: {
		success: vi.fn(),
		error: vi.fn(),
		warning: vi.fn(),
		info: vi.fn(),
		dismiss: vi.fn(),
	},
	Toaster: () => null,
}));

vi.mock("next-themes", () => ({
	useTheme: () => ({ theme: "light" as const }),
}));

// ── Render options ─────────────────────────────────────────────────────

export interface RenderAppOptions {
	/** Which page App should render. Defaults to "home". */
	currentPage?: Page;
	/** Override the config returned by `get_config`. */
	config?: VoiceTyperConfig;
	/**
	 * Stub the navigation callback so tests can assert which page App
	 * tried to switch to. Pass a `vi.fn<(page: Page) => void>()` to
	 * inspect call args; defaults to a no-op `vi.fn()`.
	 */
	navigate?: (page: Page) => void;
}

/**
 * Mount `App.tsx` with the standard set of hook/chrome stubs and return
 * the testing-library queries. The child page for `options.currentPage`
 * is rendered as a trivial stub (`<div data-testid="page-{name}">`)
 * unless the caller provides their own `vi.mock("@/pages/...", ...)`.
 */
export async function renderApp(options: RenderAppOptions = {}) {
	const page = options.currentPage ?? "home";
	hoisted.currentPage = page;
	hoisted.mockConfig = options.config ?? makeConfig();
	if (options.navigate) {
		hoisted.mockNavigate.mockImplementation(options.navigate);
	}

	// Wire `get_config` / `set_config` to the hoisted mock config so
	// any child component that calls `usePython().call("get_config")`
	// gets a valid response without each test having to set it up.
	hoisted.mockCall.mockImplementation((type: string) => {
		if (type === "get_config") return Promise.resolve(hoisted.mockConfig);
		if (type === "set_config") return Promise.resolve({ success: true });
		return Promise.resolve({});
	});

	// Lazy-import App AFTER the mocks are registered (vi.mock is hoisted
	// to the top of the file regardless of where the import statement
	// sits, but importing lazily makes the test's intent clearer).
	const { default: App } = await import("@/App");
	const result = render(<App />);

	return {
		...result,
		/** The mocked `usePython().call` — reset between tests with `mockReset()`. */
		mockCall: hoisted.mockCall,
		/** The mocked `usePythonEvent` — reset between tests with `mockReset()`. */
		mockPythonEvent: hoisted.mockPythonEvent,
		/** The mocked `onNavigate` callback passed to App. */
		mockNavigate: hoisted.mockNavigate,
	};
}

/** Reset all hoisted mocks. Call in `afterEach` to prevent cross-test leakage. */
export function resetAppMocks(): void {
	hoisted.mockCall.mockReset();
	hoisted.mockPythonEvent.mockReset();
	hoisted.mockNavigate.mockReset();
	hoisted.mockConfig = null;
	hoisted.currentPage = "home";
}
