/**
 * Navigation hook + shared store.
 *
 * SHARED-STATE FIX: navigation state lives in a module-level Zustand
 * store, NOT per-instance `useState`. Previously each `useNavigation()`
 * call site (App.tsx, Home, Settings, History, Dashboard,
 * AudioSettingsSection) created its own independent `currentPage` +
 * history — so a page that navigated itself (e.g. the Settings
 * "Re-run setup wizard" button, Home's "View all" / "Open mic
 * settings" links, Dashboard's "Start dictation") updated ONLY its own
 * local state and App.tsx's router never re-rendered. Every such
 * in-page navigation was a dead button in production.
 *
 * With a single shared store, `navigate()` from any component updates
 * the same `currentPage` App.tsx reads, so in-page navigation works and
 * App's route guard / document.title / focus effects react to it.
 *
 * Persistence is preserved: the store is initialised from localStorage
 * once at module load and every transition is written back, so the user
 * returns to where they left off after closing/reopening the app.
 *
 * `_resetNavigationForTest` is the test seam (same pattern as
 * `_resetSoundManagerForTests` / `_resetFileSizeCacheForTest`): it
 * re-reads localStorage into the store so a test can seed a persisted
 * page and then reset the shared state deterministically.
 */

import { useEffect } from "react";
import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import { isKnownPage } from "@/router/routes";
import type { Page } from "@/types/ipc";

// Persist the current page + nav history to localStorage
// so the user returns to where they left off after closing/reopening
// the app.  Previously the app always started at 'home' and the
// user's navigation history was lost.
const STORAGE_KEY_NAV = "vt_nav_state";

// Cap the in-memory nav history to avoid unbounded growth
// if a user navigates hundreds of times in a single session. 50 is
// generous (browsers cap tab history at ~50 for similar reasons) and
// the slice preserves back/forward semantics: when the cap is hit the
// oldest entry is dropped, the current pointer is kept pointing at the
// same logical entry (its index shifts down by 1).
const MAX_NAV_HISTORY = 50;

// The runtime page registry — the set of known page names
// and the `isKnownPage` type guard — now lives in `router/routes.ts`
// (single source of truth, mirrored from the `Page` union in
// `types/ipc.ts`). Previously this file maintained its own
// `KNOWN_PAGES` Set, which was one of four parallel copies of the
// page registry and had to be hand-kept in sync with the type union.
// See `router/routes.ts` for the rationale.

interface NavState {
	page: Page;
	history: Page[];
	index: number;
}

function defaultNavState(): NavState {
	return { page: "home", history: ["home"], index: 0 };
}

function loadNavState(): NavState {
	try {
		const raw = localStorage.getItem(STORAGE_KEY_NAV);
		if (raw) {
			const parsed = JSON.parse(raw) as {
				page?: unknown;
				history?: unknown;
				index?: unknown;
			};
			// Validate `parsed.page` against the known Page
			// union so a corrupted localStorage payload (e.g. a user
			// hand-edited devtools, or a stale schema from an older
			// build) cannot inject an unknown page value into React
			// state. Previously any truthy `parsed.page` was accepted
			// and cast to `Page` — surfacing as a "Page not found"
			// loop on every render.
			if (
				isKnownPage(parsed.page) &&
				Array.isArray(parsed.history) &&
				parsed.history.length > 0 &&
				parsed.history.every(isKnownPage) &&
				typeof parsed.index === "number" &&
				parsed.index >= 0 &&
				parsed.index < parsed.history.length
			) {
				return {
					page: parsed.page,
					history: parsed.history as Page[],
					index: parsed.index,
				};
			}
		}
	} catch (e) {
		// localStorage may be unavailable (SSR, sandboxed renderer)
		// or the stored payload may be malformed JSON. Non-fatal —
		// fall through to the default nav state so the app still boots.
		console.warn(
			"[renderer:useNavigation] loadNavState failed, using default:",
			e,
		);
	}
	return defaultNavState();
}

function saveNavState(state: NavState): void {
	try {
		localStorage.setItem(STORAGE_KEY_NAV, JSON.stringify(state));
	} catch (e) {
		// localStorage may be unavailable (SSR, sandboxed
		// renderer) or quota may be exceeded. Non-fatal — the
		// in-memory nav state is still authoritative for the
		// current session; we just lose cross-session persistence.
		console.warn("[renderer:useNavigation] saveNavState failed:", e);
	}
}

/**
 * Optional navigation parameters for {@link NavStore.navigate}.
 *
 * ``consentField`` is a transient deep-link target consumed by the
 * Settings page: when a consent refusal elsewhere (microphone test,
 * level monitor, dictation gate) navigates to ``"settings"`` with the
 * consent field from the backend's ``client.consent_required``
 * envelope (e.g. ``"voice_biometric_consent"``), Settings jumps to the
 * Privacy tab and scrolls to / highlights the exact toggle instead of
 * dropping the user on whatever tab they last visited.
 */
export interface NavigateOptions {
	/**
	 * The Config consent-field name (e.g. ``"voice_biometric_consent"``)
	 * to deep-link to in Settings. Ignored when ``page !== "settings"``.
	 */
	consentField?: string;
	/**
	 * Cross-page Settings search deep-link target. Carries an
	 * optional row hint (e.g. the matched label string) so the
	 * destination Settings sub-page can scroll to + briefly
	 * highlight the matching row after the page mounts. Mirrors
	 * {@link consentField} — transient (one-shot, NOT persisted).
	 */
	settingsScrollTarget?: { rowHint?: string };
}

interface NavStore extends NavState {
	navigate: (page: Page, opts?: NavigateOptions) => void;
	replace: (page: Page) => void;
	goBack: () => void;
	goForward: () => void;
	/**
	 * Transient consent deep-link target (see {@link NavigateOptions}).
	 * NOT persisted to localStorage (it's a one-shot navigation intent,
	 * not part of the user's browsing state) and consumed by the
	 * Settings page via {@link consumeConsentField}.
	 */
	pendingConsentField: string | null;
	/**
	 * Transient cross-page Settings search deep-link target (see
	 * {@link NavigateOptions.settingsScrollTarget}). NOT persisted.
	 * Consumed by the Settings sub-page via
	 * {@link consumeSettingsScrollTarget}.
	 */
	pendingSettingsScrollTarget: { rowHint?: string } | null;
	/**
	 * Read-and-clear the pending consent deep-link target. Returns the
	 * field (or ``null``) and resets it to ``null`` so a stale target
	 * can't re-fire on a later Settings visit.
	 */
	consumeConsentField: () => string | null;
	/**
	 * Read-and-clear the pending Settings search deep-link target.
	 * Mirrors {@link consumeConsentField} — one-shot consumption.
	 */
	consumeSettingsScrollTarget: () => { rowHint?: string } | null;
}

const useNavStore = create<NavStore>()((set, get) => {
	/** Apply a new nav state + persist it to localStorage. */
	const apply = (next: NavState): void => {
		set(next);
		saveNavState(next);
	};

	return {
		...loadNavState(),
		pendingConsentField: null,
		pendingSettingsScrollTarget: null,
		navigate: (page, opts) => {
			const { page: current, history, index } = get();
			// Set the transient deep-link target BEFORE the
			// same-page early return below — a consent refusal fired
			// while the user is ALREADY on Settings must still arm the
			// pending field so the mounted Settings page (which
			// subscribes to ``pendingConsentField`` reactively) can
			// consume it and scroll to the toggle.
			if (opts?.consentField) {
				set({ pendingConsentField: opts.consentField });
			}
			// Same pattern for the Settings search deep-link target —
			// even if the user is already on the destination
			// Settings sub-page, arm the scroll/hint target so the
			// mounted page can consume it.
			if (opts?.settingsScrollTarget) {
				set({ pendingSettingsScrollTarget: opts.settingsScrollTarget });
			}
			// Redirect the legacy "settings" parent literal to
			// "settingsGeneral" so the user always lands on a real
			// Settings sub-page (never an empty parent). Mirrors
			// the onboarding-completed guard at App.tsx:131-140 —
			// uses `replace` so the history stack doesn't gain a
			// no-op "settings" entry between the previous page and
			// the resolved "settingsGeneral" target. Existing
			// call sites that still send "settings" (Ctrl+,
			// shortcut, tray menu, Python `navigate {path:
			// "/settings"}` IPC event) continue to work without
			// modification.
			if (page === "settings") {
				const target: Page = "settingsGeneral";
				if (target === current) return;
				const nextHistory = [...history];
				nextHistory[index] = target;
				apply({ page: target, history: nextHistory, index });
				return;
			}
			// No-op when navigating to the page we're already
			// on. Previously this still pushed a duplicate entry onto
			// the history stack and re-saved localStorage, polluting
			// the back/forward chain (Ctrl+Click on a sidebar entry
			// the user was already on would add a no-op step they had
			// to back through). The early return preserves the
			// existing history and index exactly.
			if (page === current) return;

			let nextHistory = [...history.slice(0, index + 1), page];
			let nextIndex = index + 1;

			// Cap the history so a long-lived session
			// doesn't accumulate hundreds of entries (each one is
			// serialized into localStorage on every navigate). Drop
			// from the front; shift the index so the current pointer
			// still points at the same logical entry.
			if (nextHistory.length > MAX_NAV_HISTORY) {
				const overflow = nextHistory.length - MAX_NAV_HISTORY;
				nextHistory = nextHistory.slice(overflow);
				nextIndex = Math.max(0, nextIndex - overflow);
			}

			apply({ page, history: nextHistory, index: nextIndex });
		},
		/**
		 * Replace the current history entry with `page` without
		 * pushing a new entry onto the stack. Mirrors
		 * `history.replaceState` in the browser API. Use this for
		 * route guards that should NOT appear in the back/forward
		 * history (e.g. the onboarding-completed guard bouncing a
		 * user from `onboarding` → `home` shouldn't leave a "home"
		 * entry sitting on top of the original "onboarding" entry,
		 * otherwise the user could press Back and land back in the
		 * wizard they just finished).
		 *
		 * If `page === currentPage`, this is a no-op (same rationale
		 * as `navigate`).
		 */
		replace: (page) => {
			const { page: current, history, index } = get();
			if (page === current) return;
			const nextHistory = [...history];
			nextHistory[index] = page;
			apply({ page, history: nextHistory, index });
		},
		goBack: () => {
			const { history, index } = get();
			if (index <= 0) return;
			const target = history[index - 1];
			// noUncheckedIndexedAccess: `target` is `Page | undefined`.
			// The index is bounded by the guard above and the history
			// is append-only, but TS still widens the read; explicit
			// guard keeps the setter / save-call signatures happy.
			if (target === undefined) return;
			apply({ page: target, history, index: index - 1 });
		},
		goForward: () => {
			const { history, index } = get();
			if (index >= history.length - 1) return;
			const target = history[index + 1];
			if (target === undefined) return;
			apply({ page: target, history, index: index + 1 });
		},
		consumeConsentField: () => {
			const field = get().pendingConsentField;
			if (field) {
				set({ pendingConsentField: null });
			}
			return field;
		},
		consumeSettingsScrollTarget: () => {
			const target = get().pendingSettingsScrollTarget;
			if (target) {
				set({ pendingSettingsScrollTarget: null });
			}
			return target;
		},
	};
});

/**
 * Test seam — re-read localStorage into the shared store.
 *
 * The store is a module-level singleton, so state survives across tests
 * in the same file unless reset. Tests that seed `vt_nav_state` and
 * then mount a component must call this AFTER seeding so the store
 * picks up the persisted page. @internal
 */
export function _resetNavigationForTest(): void {
	useNavStore.setState({
		...loadNavState(),
		pendingConsentField: null,
		pendingSettingsScrollTarget: null,
	});
	// Also reset the document-listener install flag so a test that
	// re-mounts App (or calls `useNavigation` again) doesn't skip the
	// listener install because of a stale `documentListenersInstalled`
	// from a prior test.
	documentListenersInstalled = false;
}

// ── Document listeners (mouse X1/X2 + Alt+Arrow) ───────────────────
//
// previously each `useNavigation` consumer registered its OWN
// `mouseup` + `keydown` listeners on `document`. With 6+ consumers
// (App, Home, Settings, History, Dashboard, AudioSettingsSection),
// that was 12+ listeners on `document` per app load — each one
// invoked on every mouseup / keydown event app-wide, even though the
// handler bodies only differ by which `goBack` / `goForward` closure
// they close over (and those are stable Zustand store actions that
// NEVER change identity, so all 6 closures were functionally
// identical).
//
// The listeners are now installed EXACTLY ONCE per app load via a
// module-level `documentListenersInstalled` flag. The handlers call
// `useNavStore.getState().goBack()` / `.goForward()` directly so they
// don't depend on any consumer's render closure. The first
// `useNavigation` consumer triggers the install via
// `ensureDocumentListeners()`; subsequent consumers no-op.
//
// The `useEffect` inside `useNavigation` still runs (for parity with
// the prior structure + to keep the test surface stable), but its
// body is a no-op when `documentListenersInstalled` is already true.
// This keeps the hook's call shape unchanged (rules-of-hooks
// compliant) while deduplicating the actual listener registration.
let documentListenersInstalled = false;

function ensureDocumentListeners(): void {
	if (documentListenersInstalled) return;
	if (typeof document === "undefined") return;
	documentListenersInstalled = true;

	// Mouse forward/back buttons (X1/X2) navigate like a browser.
	const mouseHandler = (e: MouseEvent) => {
		if (e.button === 3) {
			e.preventDefault();
			useNavStore.getState().goBack();
		} else if (e.button === 4) {
			e.preventDefault();
			useNavStore.getState().goForward();
		}
	};
	// Keyboard equivalent for back/forward navigation: Alt+ArrowLeft
	// goes back, Alt+ArrowRight goes forward — matching the behaviour
	// of every major browser so users with a keyboard-only workflow
	// get the same affordance the mouse X1/X2 buttons already provide.
	//
	// The handler is suppressed when focus is inside an editable
	// element (`<input>`, `<textarea>`, `<select>`, or
	// `contentEditable`) so a user editing a text field with arrow
	// keys doesn't get yanked to another page just because they
	// happened to hold Alt. The browser's own Alt+Arrow text-editing
	// shortcuts (e.g. Alt+Left/Right to move the caret by word on
	// macOS) keep working because we return before calling
	// `preventDefault()` in that branch.
	const keyHandler = (e: KeyboardEvent) => {
		if (!e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
		if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;

		const target = e.target as HTMLElement | null;
		const tag = target?.tagName?.toLowerCase() ?? "";
		const typing =
			tag === "input" ||
			tag === "textarea" ||
			tag === "select" ||
			target?.isContentEditable === true;
		if (typing) return;

		e.preventDefault();
		if (e.key === "ArrowLeft") {
			useNavStore.getState().goBack();
		} else {
			useNavStore.getState().goForward();
		}
	};

	document.addEventListener("mouseup", mouseHandler);
	document.addEventListener("keydown", keyHandler);
	// Note: we deliberately do NOT register a cleanup that removes
	// the listeners. The store + listeners are app-lifetime singletons
	// (the store survives hot-reload of any consumer; removing the
	// listeners on the FIRST consumer's unmount would break navigation
	// for all OTHER consumers still mounted). The
	// `_resetNavigationForTest` seam re-installs them in tests by
	// resetting `documentListenersInstalled`.
}

/**
 * Navigation hook: subscribes to the shared navigation store (current
 * page + browser-style history stack), persists state to localStorage,
 * and wires mouse forward/back buttons (X1/X2) + Alt+Arrow to act like
 * a browser's back/forward navigation.
 *
 * Every call site shares the SAME store, so a `navigate()` call from
 * any page re-renders App.tsx's router (see the module docstring).
 *
 * the 4 stable action selectors are consolidated into a single
 * `useShallow` subscription (mirrors `useConnection.ts:97-105`). Zustand
 * still runs each registered selector on every `set()` call, but
 * `useShallow` collapses the 4 action reads into ONE selector run + ONE
 * shallow-equal check (the action function references never change
 * identity, so the shallow-equal return object is stable across
 * unrelated state changes — this hook does NOT re-render when only
 * `page` / `history` / `index` change). Combined with the 3 value
 * selectors (`page`, `history`, `index`), the total selector run count
 * per `set()` is now 4 (down from 7). The document listeners are
 * installed exactly once per app load (see `ensureDocumentListeners`).
 */
export function useNavigation() {
	const currentPage = useNavStore((s) => s.page);
	const history = useNavStore((s) => s.history);
	const index = useNavStore((s) => s.index);
	// Transient consent deep-link target — consumed by the Settings
	// page. Rarely non-null (only immediately after a consent
	// deep-link navigate), so the extra re-render when it flips is
	// negligible.
	const pendingConsentField = useNavStore((s) => s.pendingConsentField);
	const pendingSettingsScrollTarget = useNavStore(
		(s) => s.pendingSettingsScrollTarget,
	);
	const {
		navigate,
		replace,
		goBack,
		goForward,
		consumeConsentField,
		consumeSettingsScrollTarget,
	} = useNavStore(
		useShallow((s) => ({
			navigate: s.navigate,
			replace: s.replace,
			goBack: s.goBack,
			goForward: s.goForward,
			consumeConsentField: s.consumeConsentField,
			consumeSettingsScrollTarget: s.consumeSettingsScrollTarget,
		})),
	);

	// Install the document-level listeners exactly once per app load.
	// The empty dep array means this runs on mount for EVERY consumer,
	// but `ensureDocumentListeners` short-circuits after the first
	// install — so only the first consumer actually registers the
	// listeners. Subsequent consumers' effects are no-ops. We still
	// call the effect (rather than calling `ensureDocumentListeners`
	// at module load time) so tests that reset the install flag via
	// `_resetNavigationForTest` can re-trigger the install on the
	// next mount.
	useEffect(() => {
		ensureDocumentListeners();
	}, []);

	return {
		currentPage,
		navigate,
		replace,
		goBack,
		goForward,
		canGoBack: index > 0,
		canGoForward: index < history.length - 1,
		pendingConsentField,
		consumeConsentField,
		pendingSettingsScrollTarget,
		consumeSettingsScrollTarget,
	};
}
