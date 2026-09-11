/**
 * Purity pins for the `useThemeSettings` state updaters.
 *
 * React requires setState updater functions to be PURE, StrictMode
 * double-invokes them in development, and an interrupted/replayed
 * render can re-invoke one with a different base state in production.
 * The custom-colour edit handler used to tuck all of its side effects
 * (document theme-var writes, the theme-colour cache invalidation, the
 * localStorage draft write, and the debounced backend-save arming)
 * INSIDE the `setCustomDraft` updater, so a double-invocation doubled
 * every one of them.
 *
 * These tests render the hook inside <StrictMode> (React's own testing
 * pattern for exposing impure updaters) and assert that ONE colour
 * edit produces EXACTLY ONE of each side effect:
 *   - one `saveDraftToLS` localStorage write,
 *   - one `updateConfigDebounced("custom_theme", …, 300)` save arming,
 *   - one `applyThemeVars` document write.
 *
 * They also pin the behavioural contract the refactor must preserve:
 *   - two edits in the same tick COMPOSE (both colours land in the
 *     draft, the ref mirror keeps the second edit from clobbering the
 *     first the way the old functional-updater form composed them);
 *   - a colour edit before the draft exists (null config / pre-init)
 *     is a no-op (no side effects, no state change).
 *
 * Tests run on LINUX (sandbox).
 */
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { type ChangeEvent, type ReactNode, StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Spy on the localStorage draft-backup helpers so "one LS write per
// edit" is assertable. loadDraftFromLS returns null so the hook's init
// effect seeds the default draft instead of restoring a stored one.
vi.mock("@/lib/theme-draft-storage", () => ({
	saveDraftToLS: vi.fn(),
	loadDraftFromLS: vi.fn(() => null),
	clearDraftLS: vi.fn(),
}));

// Partial-mock @/themes: keep the real data helpers (deriveCustomVars,
// DEFAULT_CUSTOM_* maps) but spy on applyThemeVars, the document-write
// side effect that used to run inside the state updater.
vi.mock("@/themes", async (importOriginal) => {
	const actual = await importOriginal<typeof import("@/themes")>();
	return {
		...actual,
		applyThemeVars: vi.fn(),
	};
});

import { saveDraftToLS } from "@/lib/theme-draft-storage";
import { applyThemeVars } from "@/themes";
import type { VoiceTyperConfig } from "@/types/config";
import { _themeColorCache } from "./themeColorCache";
import type { UseThemeSettingsReturn } from "./useThemeSettings";
import { useThemeSettings } from "./useThemeSettings";

function StrictModeWrapper({ children }: { children: ReactNode }) {
	return <StrictMode>{children}</StrictMode>;
}

const makeConfig = (): VoiceTyperConfig =>
	({
		theme_preset: "custom",
		custom_theme: null,
	}) as unknown as VoiceTyperConfig;

interface HookCallbacks {
	updateConfig: ReturnType<typeof vi.fn>;
	updateConfigDebounced: ReturnType<typeof vi.fn>;
}

interface RenderedThemeHook extends HookCallbacks {
	result: { current: UseThemeSettingsReturn };
}

function renderThemeHook(config: VoiceTyperConfig | null): RenderedThemeHook {
	const callbacks: HookCallbacks = {
		updateConfig: vi.fn(),
		updateConfigDebounced: vi.fn(),
	};
	const { result } = renderHook(
		() =>
			useThemeSettings({
				config,
				// Cast the mocks to the hook's callback contracts (the
				// vitest Mock generic is not directly assignable; the
				// untyped mock is still fully assertable on the return
				// value below).
				updateConfig: callbacks.updateConfig as unknown as (
					updates: Partial<VoiceTyperConfig>,
				) => void,
				updateConfigDebounced: callbacks.updateConfigDebounced as unknown as (
					key: keyof VoiceTyperConfig,
					value: unknown,
					delayMs?: number,
				) => void,
			}),
		{ wrapper: StrictModeWrapper },
	);
	return { result, ...callbacks };
}

// Trigger a custom-colour edit through the hook's public surface —
// `handleColorInputChange(varName)` returns the input onChange handler
// which forwards to the internal custom-colour edit path.
function editColor(
	result: RenderedThemeHook["result"],
	varName: string,
	hex: string,
): void {
	act(() => {
		result.current.handleColorInputChange(varName)({
			target: { value: hex },
		} as ChangeEvent<HTMLInputElement>);
	});
}

describe("useThemeSettings, one side-effect batch per colour edit (StrictMode purity)", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		_themeColorCache.clear();
	});

	afterEach(() => {
		cleanup();
		_themeColorCache.clear();
	});

	it("one edit fires exactly ONE localStorage write, ONE debounced-save arming, and ONE applyThemeVars call", async () => {
		const { result, updateConfigDebounced } = renderThemeHook(makeConfig());
		await waitFor(() => {
			expect(result.current.customDraft).not.toBeNull();
		});

		editColor(result, "--background", "#123456");

		// The core purity pin: exactly one LS draft write per edit. The
		// pre-fix updater form double-fired this under StrictMode's
		// double-invocation (and could re-fire under a replayed render).
		expect(saveDraftToLS).toHaveBeenCalledTimes(1);
		// Exactly one debounced backend-save arming per edit.
		expect(updateConfigDebounced).toHaveBeenCalledTimes(1);
		expect(updateConfigDebounced).toHaveBeenCalledWith(
			"custom_theme",
			expect.objectContaining({
				light: expect.objectContaining({ "--background": "#123456" }),
			}),
			300,
		);
		// Exactly one document theme-var write per edit.
		expect(applyThemeVars).toHaveBeenCalledTimes(1);
		expect(applyThemeVars).toHaveBeenCalledWith(
			"custom",
			expect.any(Boolean),
			expect.anything(),
		);
	});

	it("a second edit fires the side-effect batch exactly once MORE (no accumulation)", async () => {
		const { result, updateConfigDebounced } = renderThemeHook(makeConfig());
		await waitFor(() => {
			expect(result.current.customDraft).not.toBeNull();
		});

		editColor(result, "--background", "#111111");
		editColor(result, "--background", "#222222");

		expect(saveDraftToLS).toHaveBeenCalledTimes(2);
		expect(updateConfigDebounced).toHaveBeenCalledTimes(2);
		expect(applyThemeVars).toHaveBeenCalledTimes(2);
		// The LAST edit wins in the persisted draft (each write carries
		// the full updated draft).
		expect(saveDraftToLS).toHaveBeenLastCalledWith(
			expect.objectContaining({
				light: expect.objectContaining({ "--background": "#222222" }),
			}),
		);
	});

	it("two same-tick edits COMPOSE, both colours land in the draft", async () => {
		const { result } = renderThemeHook(makeConfig());
		await waitFor(() => {
			expect(result.current.customDraft).not.toBeNull();
		});

		// No act() between the two calls: the second must compose off the
		// first (the old functional-updater form composed them; the ref
		// mirror preserves that contract outside the updater).
		editColor(result, "--background", "#111111");
		editColor(result, "--foreground", "#222222");

		await waitFor(() => {
			expect(result.current.customDraft?.light["--background"]).toBe("#111111");
			expect(result.current.customDraft?.light["--foreground"]).toBe("#222222");
		});
		// Both writes happened (the first is not lost to a stale base).
		expect(saveDraftToLS).toHaveBeenCalledTimes(2);
	});

	it("invalidates the theme-colour cache entries for the custom/default presets", async () => {
		const { result } = renderThemeHook(makeConfig());
		await waitFor(() => {
			expect(result.current.customDraft).not.toBeNull();
		});
		// Seed the module-level cache the way a prior render would.
		_themeColorCache.set("custom", { light: {}, dark: {} });
		_themeColorCache.set("default", { light: {}, dark: {} });

		editColor(result, "--background", "#123456");

		expect(_themeColorCache.has("custom")).toBe(false);
		expect(_themeColorCache.has("default")).toBe(false);
	});

	it("an edit before the draft exists is a NO-OP (null config → no side effects, no state)", () => {
		const { result, updateConfig, updateConfigDebounced } =
			renderThemeHook(null);

		editColor(result, "--background", "#123456");

		expect(result.current.customDraft).toBeNull();
		expect(saveDraftToLS).not.toHaveBeenCalled();
		expect(updateConfigDebounced).not.toHaveBeenCalled();
		expect(updateConfig).not.toHaveBeenCalled();
		expect(applyThemeVars).not.toHaveBeenCalled();
	});
});
