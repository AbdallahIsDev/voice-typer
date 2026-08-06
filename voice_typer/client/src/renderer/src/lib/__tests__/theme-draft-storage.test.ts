/**
 * unit tests for `lib/theme-draft-storage.ts` — localStorage
 * draft-backup helpers for the custom-theme colour picker (partial split).
 *
 * The module exposes three pure functions (no React / no module state):
 *   • `saveDraftToLS(data)`   — persists a CustomThemeData draft.
 *   • `loadDraftFromLS()`     — returns the stored draft, or null on
 *                               missing-key / corrupt-JSON / schema drift.
 *   • `clearDraftLS()`        — removes the stored draft.
 *
 * Behaviour under test:
 *   1. Round-trip save→load returns the same CustomThemeData.
 *   2. clearDraftLS evicts the draft (loadDraftFromLS returns null
 *      afterwards) — the "expired-draft eviction" path.
 *   3. loadDraftFromLS returns null when no draft is stored (the
 *      "missing key" guard).
 *   4. loadDraftFromLS returns null on corrupt JSON (the "corrupt-JSON
 *      guard" — JSON.parse throws, the catch swallows it).
 *   5. loadDraftFromLS survives schema-migration drift: a stored draft
 *      with EXTRA fields (forward-compat) or MISSING fields (older
 *      schema) is returned as-is — there is no validation, so callers
 *      must treat the result as untrusted. The test pins this contract
 *      so a future tightening (e.g. zod validation) is an intentional
 *      change, not a silent regression.
 *   6. saveDraftToLS swallows localStorage errors (non-fatal — backend
 *      save still proceeds).
 *   7. clearDraftLS swallows localStorage errors (non-fatal — leftover
 *      draft will be overwritten on the next save or rejected as stale
 *      on the next load).
 *
 * The test-setup.ts file installs an in-memory localStorage fallback on
 * Node 26+, and jsdom provides one on Node 24 — either way, `localStorage`
 * is available in the jsdom environment. We additionally `localStorage.clear()`
 * in `beforeEach` (the test-setup.ts `afterEach` also clears, but we want
 * a clean slate BEFORE each test, not just between them).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
	clearDraftLS,
	loadDraftFromLS,
	saveDraftToLS,
} from "@/lib/theme-draft-storage";
import type { CustomThemeData } from "@/themes";

const VALID_DRAFT: CustomThemeData = {
	light: {
		"--bg": "#ffffff",
		"--text": "#000000",
		"--accent": "#3b82f6",
	},
	dark: {
		"--bg": "#000000",
		"--text": "#ffffff",
		"--accent": "#60a5fa",
	},
};

describe("theme-draft-storage — round-trip save/load", () => {
	beforeEach(() => {
		localStorage.clear();
	});

	it("saveDraftToLS then loadDraftFromLS returns the same data (round-trip)", () => {
		saveDraftToLS(VALID_DRAFT);
		const loaded = loadDraftFromLS();

		expect(loaded).not.toBeNull();
		expect(loaded).toEqual(VALID_DRAFT);
		// Deep equality on the nested structures.
		expect(loaded?.light["--bg"]).toBe("#ffffff");
		expect(loaded?.dark["--accent"]).toBe("#60a5fa");
	});

	it("loadDraftFromLS returns null when no draft is stored", () => {
		expect(loadDraftFromLS()).toBeNull();
	});

	it("clearDraftLS evicts the stored draft (expired-draft eviction)", () => {
		saveDraftToLS(VALID_DRAFT);
		expect(loadDraftFromLS()).not.toBeNull();

		clearDraftLS();

		expect(loadDraftFromLS()).toBeNull();
	});

	it("clearDraftLS is idempotent (clearing when nothing is stored is a no-op)", () => {
		expect(() => clearDraftLS()).not.toThrow();
		expect(loadDraftFromLS()).toBeNull();
	});

	it("loadDraftFromLS returns null on corrupt JSON (corrupt-JSON guard)", () => {
		// Write garbage directly to localStorage to bypass saveDraftToLS's
		// JSON.stringify. The catch in loadDraftFromLS must swallow the
		// SyntaxError and return null.
		localStorage.setItem("vt_custom_theme_draft", "{not valid json");
		const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

		const loaded = loadDraftFromLS();

		expect(loaded).toBeNull();
		// loadDraftFromLS uses a bare `catch {}` (no console.warn) —
		// so warn should NOT have been called. This pins the contract:
		// a corrupt draft fails silently.
		expect(warnSpy).not.toHaveBeenCalled();

		warnSpy.mockRestore();
	});

	it("loadDraftFromLS returns the stored object on schema-migration drift (forward-compat: extra fields)", () => {
		// Simulate a future schema that added a `medium` variant we don't
		// know about yet. The current loader is permissive — it returns
		// the parsed object as-is, no validation.
		const futureDraft = {
			light: { "--bg": "#ffffff" },
			dark: { "--bg": "#000000" },
			medium: { "--bg": "#cccccc" }, // extra field
		};
		localStorage.setItem("vt_custom_theme_draft", JSON.stringify(futureDraft));

		const loaded = loadDraftFromLS();

		// No validation runs — the extra field is preserved verbatim.
		expect(loaded).not.toBeNull();
		expect(loaded?.light["--bg"]).toBe("#ffffff");
		expect(loaded?.dark["--bg"]).toBe("#000000");
		// The extra field is present (proves no validation / stripping).
		expect((loaded as unknown as Record<string, unknown>).medium).toEqual({
			"--bg": "#cccccc",
		});
	});

	it("loadDraftFromLS returns the stored object on schema-migration drift (back-compat: missing fields)", () => {
		// Simulate an older draft that only has `light` (no `dark`).
		// The current loader returns it as-is rather than rejecting.
		const legacyDraft = { light: { "--bg": "#ffffff" } };
		localStorage.setItem("vt_custom_theme_draft", JSON.stringify(legacyDraft));

		const loaded = loadDraftFromLS();

		expect(loaded).not.toBeNull();
		expect(loaded?.light["--bg"]).toBe("#ffffff");
		expect(loaded?.dark).toBeUndefined();
	});

	it("saveDraftToLS swallows localStorage errors (non-fatal — backend save proceeds)", () => {
		const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
		// jsdom's Storage defines setItem on the prototype (non-writable
		// instance slot), so a direct `localStorage.setItem = vi.fn(...)`
		// assignment is silently ignored. spyOn installs a real override
		// that survives the prototype lookup. HOWEVER, the in-memory
		// fallback installed by test-setup.ts on Node 26+ is a plain
		// object (not a `Storage` instance), so `Storage.prototype`
		// spying silently misses it — spy on whichever object actually
		// provides the methods.
		const storageTarget: Storage =
			localStorage instanceof Storage ? Storage.prototype : localStorage;
		const setItemSpy = vi
			.spyOn(storageTarget, "setItem")
			.mockImplementation(() => {
				throw new Error("QuotaExceededError");
			});

		// Must NOT throw — the backend save still proceeds even if the
		// crash-recovery draft can't be persisted.
		expect(() => saveDraftToLS(VALID_DRAFT)).not.toThrow();
		expect(warnSpy).toHaveBeenCalledTimes(1);
		expect(warnSpy.mock.calls[0]?.[0]).toContain(
			"[theme-draft-storage] saveDraftToLS failed:",
		);

		setItemSpy.mockRestore();
		warnSpy.mockRestore();
	});

	it("clearDraftLS swallows localStorage errors (non-fatal)", () => {
		const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
		// Same dual-target spying as the setItem test above: spy on the
		// prototype under jsdom (Node 24 CI), on the memory-fallback
		// instance under Node 26+ local dev.
		const storageTarget: Storage =
			localStorage instanceof Storage ? Storage.prototype : localStorage;
		const removeItemSpy = vi
			.spyOn(storageTarget, "removeItem")
			.mockImplementation(() => {
				throw new Error("SecurityError");
			});

		expect(() => clearDraftLS()).not.toThrow();
		expect(warnSpy).toHaveBeenCalledTimes(1);
		expect(warnSpy.mock.calls[0]?.[0]).toContain(
			"[theme-draft-storage] clearDraftLS failed:",
		);

		removeItemSpy.mockRestore();
		warnSpy.mockRestore();
	});

	it("saveDraftToLS overwrites a prior draft (latest-wins)", () => {
		saveDraftToLS(VALID_DRAFT);
		const next: CustomThemeData = {
			light: { "--bg": "#f0f0f0" },
			dark: { "--bg": "#101010" },
		};
		saveDraftToLS(next);

		const loaded = loadDraftFromLS();
		expect(loaded).toEqual(next);
		expect(loaded?.light["--bg"]).toBe("#f0f0f0");
	});
});

describe("theme-draft-storage — localStorage key contract", () => {
	beforeEach(() => {
		localStorage.clear();
	});

	it("saveDraftToLS persists under the canonical key (single source of truth)", () => {
		saveDraftToLS(VALID_DRAFT);

		// The key is not exported, but the contract is: it MUST be
		// "vt_custom_theme_draft" so the main process's preload bridge
		// (and any future rehydration path) can find it. Pinning the
		// string here catches an accidental rename.
		expect(localStorage.getItem("vt_custom_theme_draft")).not.toBeNull();
		expect(
			JSON.parse(localStorage.getItem("vt_custom_theme_draft") ?? "null"),
		).toEqual(VALID_DRAFT);
	});

	it("clearDraftLS removes only the draft key (other keys untouched)", () => {
		localStorage.setItem("vt_custom_theme_draft", JSON.stringify(VALID_DRAFT));
		localStorage.setItem("unrelated_key", "preserved");

		clearDraftLS();

		expect(localStorage.getItem("vt_custom_theme_draft")).toBeNull();
		expect(localStorage.getItem("unrelated_key")).toBe("preserved");
	});
});
