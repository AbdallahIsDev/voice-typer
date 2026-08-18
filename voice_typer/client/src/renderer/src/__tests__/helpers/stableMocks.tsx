/**
 * Shared stable-mocks harness for page/component test files.
 *
 * Every page test used to carry a near-identical preamble: a `vi.hoisted`
 * singleton block (mockCall / mockPythonEvent / showSnack / navigate / …)
 * plus the same seven `vi.mock` registrations (usePython, useSnackbar,
 * useLastUpdated, useNavigation, @hugeicons/*, sonner, next-themes). This
 * module owns both halves so a file's preamble collapses to one import +
 * a destructure + one `vi.mock` line per module it needs.
 *
 * ── Why NOT the renderLoopGuard dynamic-import factory trick ───────────
 * The render-loop guard helper registers its mocks with self-contained
 * factories that dynamic-import the helper (vitest re-evaluates mock
 * factory bodies in the IMPORTING test file's scope, so factories cannot
 * close over helper-module bindings). That works there because the guard
 * tests never call `vi.resetModules()`. Settings and History DO — to
 * reset page module-level caches (`_cachedConfig`, useHistoryCache) and
 * re-import the page per test. A dynamic-import factory would then hit a
 * FRESH helper module instance and hand the page a NEW set of vi.fn
 * singletons, so the test file's assertions (bound to the old ones)
 * would silently never match.
 *
 * The pattern here instead keeps the `vi.mock` registrations IN THE TEST
 * FILE, one line each, delegating to this module's factory functions via
 * the imported binding. Vitest hoists the registration but defers the
 * factory CALL; by the time the mocked module is imported (page import /
 * dynamic `await import()` in the test body), this module has been
 * evaluated and the imported binding resolves to the ONE singleton
 * object. `vi.resetModules()` wipes the module registry but not the test
 * file's already-bound import references, so the identity of
 * `stableMocks.mockCall` etc. survives a reset + page re-import — proven
 * empirically (see the probe in this directory's git history).
 *
 * ── Usage ─────────────────────────────────────────────────────────────
 * ```ts
 * import { stableMocks, pythonMock, sonnerMock, resetStableMocks } from
 *   "@/__tests__/helpers/stableMocks";
 *
 * const { mockCall, mockPythonEvent } = stableMocks;
 *
 * vi.mock("@/hooks/usePython", () => pythonMock());
 * vi.mock("@/hooks/useSnackbar", () => snackbarMock());
 * vi.mock("sonner", () => sonnerMock());
 *
 * beforeEach(() => { resetStableMocks(); /* + per-file resets *\/ });
 * ```
 *
 * Keep the helper import ABOVE any static import of a page module so the
 * singletons are initialized before the page's mocked deps are first
 * imported.
 */
import { vi } from "vitest";

/**
 * The assertable vi.fn() singletons — the ONLY objects the factories
 * wire into the mocked modules, so `expect(stableMocks.mockCall)` always
 * observes what the page called, across resets and re-imports.
 */
export const stableMocks = {
	mockCall: vi.fn(),
	mockPythonEvent: vi.fn(),
	showSnack: vi.fn(),
	markUpdated: vi.fn(),
	mockNavigate: vi.fn(),
	mockToastError: vi.fn(),
	/** Consent deep-link channel (useNavigation) — default: no pending field. */
	mockPendingConsentField: vi.fn<() => string | null>(() => null),
	mockConsumeConsentField: vi.fn<() => string | null>(() => null),
	/** Cross-page Settings search deep-link channel (useNavigation) —
	 *  default: no pending target. Added by ADR-0021 alongside the
	 *  Settings sidebar nested-submenu redesign. */
	mockPendingSettingsScrollTarget: vi.fn<() => { rowHint?: string } | null>(
		() => null,
	),
	mockConsumeSettingsScrollTarget: vi.fn<() => { rowHint?: string } | null>(
		() => null,
	),
	/** Event-handler capture map for pages that register usePythonEvent
	 *  handlers and invoke them from tests (onboarding-model-step). */
	pythonEventHandlers: {} as Record<string, (data: unknown) => void>,
	// sonner toast fns — singletons so `import { toast } from "sonner"`
	// assertions (e.g. History) observe the page's calls.
	toastSuccess: vi.fn(),
	toastError: vi.fn(),
	toastWarning: vi.fn(),
	toastInfo: vi.fn(),
	toastDismiss: vi.fn(),
}; /** mockReset every singleton (skipping plain objects) and restore the
 *  per-test defaults. */
export function resetStableMocks() {
	for (const value of Object.values(stableMocks)) {
		if (typeof (value as { mockReset?: unknown }).mockReset === "function") {
			(value as { mockReset: () => void }).mockReset();
		}
	}
	for (const key of Object.keys(stableMocks.pythonEventHandlers)) {
		delete stableMocks.pythonEventHandlers[key];
	}
	stableMocks.mockPendingConsentField.mockReturnValue(null);
	stableMocks.mockConsumeConsentField.mockReturnValue(null);
	stableMocks.mockPendingSettingsScrollTarget.mockReturnValue(null);
	stableMocks.mockConsumeSettingsScrollTarget.mockReturnValue(null);
}

// ── Shape factories (one per mocked module) ───────────────────────────

/** `@/hooks/usePython`. `noopEvent` → usePythonEvent is a no-op
 *  (ModelsPage / Onboarding); `pythonPort` → expose a fake TCP port
 *  (data-pages live-region guards read it off usePython);
 *  `captureEvents` → usePythonEvent stores handlers in the given map
 *  (onboarding-model-step drives events from tests). */
export function pythonMock(
	opts: {
		noopEvent?: boolean;
		pythonPort?: number;
		captureEvents?: Record<string, (data: unknown) => void>;
	} = {},
) {
	const captureEvents = opts.captureEvents;
	return {
		usePython: () => ({
			call: stableMocks.mockCall,
			...(opts.pythonPort !== undefined ? { pythonPort: opts.pythonPort } : {}),
		}),
		usePythonEvent: captureEvents
			? (name: string, cb: (data: unknown) => void) => {
					captureEvents[name] = cb;
				}
			: opts.noopEvent
				? () => {}
				: stableMocks.mockPythonEvent,
	};
}

/** `@/hooks/useSnackbar`. `routeToSonner` → showSnack delegates to the
 *  toast singletons by type (Vocabulary page tests assert the sonner
 *  module received the call, mirroring the real useSnackbar). */
export function snackbarMock(opts: { routeToSonner?: boolean } = {}) {
	return {
		useSnackbar: () => ({
			showSnack: opts.routeToSonner
				? (message: string, type?: string) => {
						if (type === "error") stableMocks.toastError(message);
						else stableMocks.toastSuccess(message);
					}
				: stableMocks.showSnack,
		}),
		showUndoableToast: vi.fn(),
	};
}

/** `@/hooks/useLastUpdated`. `withRefresh` → include the refresh runner. */
export function lastUpdatedMock(opts: { withRefresh?: boolean } = {}) {
	return {
		useLastUpdated: () => ({
			agoLabel: "",
			markUpdated: stableMocks.markUpdated,
			refreshing: false,
			...(opts.withRefresh
				? {
						withRefresh: async <T,>(op: () => Promise<T>): Promise<T> => op(),
					}
				: {}),
		}),
	};
}

/** `@/hooks/useNavigation` (navigate + the consent + Settings-search
 *  deep-link channels). */
export function navigationMock() {
	return {
		useNavigation: () => ({
			navigate: stableMocks.mockNavigate,
			pendingConsentField: stableMocks.mockPendingConsentField(),
			consumeConsentField: stableMocks.mockConsumeConsentField,
			pendingSettingsScrollTarget:
				stableMocks.mockPendingSettingsScrollTarget(),
			consumeSettingsScrollTarget: stableMocks.mockConsumeSettingsScrollTarget,
		}),
	};
}

/** `@hugeicons/react` icon renderer. `spreadProps` → pass extra props
 *  through to the span (ModelsPage's variant). */
export function hugeiconsReactMock(opts: { spreadProps?: boolean } = {}) {
	return {
		HugeiconsIcon: (props: {
			children?: React.ReactNode;
			icon?: { name?: string };
		}) => {
			const { children, icon, ...rest } = props;
			return (
				<span
					data-testid="hugeicon"
					data-name={icon?.name}
					{...(opts.spreadProps ? rest : {})}
				>
					{children}
				</span>
			);
		},
	};
}

/** `@hugeicons/core-free-icons` — delegates to the canonical icon mock. */
export async function hugeiconsCoreMock() {
	const { createHugeiconsMock } = await import(
		"@/__tests__/helpers/hugeicons-mock"
	);
	return createHugeiconsMock();
}

/** `sonner`. `errorTo: "mockToastError"` → toast.error routes to the
 *  assertable singleton (ModelsPage's variant). */
export function sonnerMock(opts: { errorTo?: "mockToastError" } = {}) {
	const error = opts.errorTo
		? (...args: unknown[]) => stableMocks.mockToastError(...args)
		: stableMocks.toastError;
	return {
		toast: {
			success: stableMocks.toastSuccess,
			error,
			warning: stableMocks.toastWarning,
			info: stableMocks.toastInfo,
			dismiss: stableMocks.toastDismiss,
		},
		Toaster: () => null,
	};
}

/** `next-themes`. */
export function nextThemesMock() {
	return { useTheme: () => ({ theme: "light" as const }) };
}

/**
 * The models-page `get_config` response shape — the minimal config the
 * Models page (and Dashboard) reads, previously copy-pasted as a
 * `MOCK_CONFIG` const across ModelsPage.test.tsx, ModelsPage-nh29 and
 * the data-pages live-region guards. Returns a FRESH object per call so
 * a test that mutates its config can't leak into siblings.
 */
export function modelsConfigMock() {
	return {
		asr_backend: "whisper",
		model_size: "tiny",
		device: "cpu",
		language: "en",
		hotkey: "F2",
		huggingface_consent: true,
		openai_api_key: "",
		groq_api_key: "",
		deepgram_api_key: "",
		cloud_openai_consent: false,
		cloud_groq_consent: false,
		cloud_deepgram_consent: false,
	};
}
