/**
 * Navigation hook + shared Zustand store (single source of truth so in-page
 * navigate() updates App router). Persists vt_nav_state to localStorage.
 */

import { useEffect } from "react";
import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import { isKnownPage } from "@/router/routes";
import type { Page } from "@/types/ipc";

const STORAGE_KEY_NAV = "vt_nav_state";

const MAX_NAV_HISTORY = 50;

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
			if (
				isKnownPage(parsed.page) &&
				Array.isArray(parsed.history) &&
				parsed.history.length > 0 &&
				parsed.history.every(isKnownPage) &&
				typeof parsed.index === "number" &&
				parsed.index >= 0 &&
				parsed.index < parsed.history.length
			) {
				// Privacy (C-BG-1): never restore a cold start onto the
				if (parsed.page === "microphone") {
					return defaultNavState();
				}
				return {
					page: parsed.page,
					history: parsed.history as Page[],
					index: parsed.index,
				};
			}
		}
	} catch (e) {
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
		console.warn("[renderer:useNavigation] saveNavState failed:", e);
	}
}

/**
 * Optional navigation parameters for {@link NavStore.navigate}.
 *
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
	 */
	pendingConsentField: string | null;
	/**
	 * Transient cross-page Settings search deep-link target (see
	 * {@link NavigateOptions.settingsScrollTarget}). NOT persisted.
	 */
	pendingSettingsScrollTarget: { rowHint?: string } | null;
	/**
	 * Read-and-clear the pending consent deep-link target. Returns the
	 * field (or ``null``) and resets it to ``null`` so a stale target
	 */
	consumeConsentField: () => string | null;
	/**
	 * Read-and-clear the pending Settings search deep-link target.
	 * Mirrors {@link consumeConsentField}, one-shot consumption.
	 */
	consumeSettingsScrollTarget: () => { rowHint?: string } | null;
}

const useNavStore = create<NavStore>()((set, get) => {
	/**
	 * Apply a new nav state + persist it to localStorage.
	 */
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
			if (opts?.consentField) {
				set({ pendingConsentField: opts.consentField });
			}
			if (opts?.settingsScrollTarget) {
				set({ pendingSettingsScrollTarget: opts.settingsScrollTarget });
			}
			if (page === current) return;

			let nextHistory = [...history.slice(0, index + 1), page];
			let nextIndex = index + 1;

			if (nextHistory.length > MAX_NAV_HISTORY) {
				const overflow = nextHistory.length - MAX_NAV_HISTORY;
				nextHistory = nextHistory.slice(overflow);
				nextIndex = Math.max(0, nextIndex - overflow);
			}

			apply({ page, history: nextHistory, index: nextIndex });
		},
		/**
		 * pushing a new entry onto the stack. Mirrors
		 * route guards that should NOT appear in the back/forward
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
 * Test seam, re-read localStorage into the shared store.
 *
 */
export function _resetNavigationForTest(): void {
	useNavStore.setState({
		...loadNavState(),
		pendingConsentField: null,
		pendingSettingsScrollTarget: null,
	});
	documentListenersInstalled = false;
}

let documentListenersInstalled = false;

function ensureDocumentListeners(): void {
	if (documentListenersInstalled) return;
	if (typeof document === "undefined") return;
	documentListenersInstalled = true;

	const mouseHandler = (e: MouseEvent) => {
		if (e.button === 3) {
			e.preventDefault();
			useNavStore.getState().goBack();
		} else if (e.button === 4) {
			e.preventDefault();
			useNavStore.getState().goForward();
		}
	};
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
}

/**
 * Navigation hook: subscribes to the shared navigation store (current
 * and wires mouse forward/back buttons (X1/X2) + Alt+Arrow to act like
 */
export function useNavigation() {
	const currentPage = useNavStore((s) => s.page);
	const history = useNavStore((s) => s.history);
	const index = useNavStore((s) => s.index);
	// Transient consent deep-link target, consumed by the Settings
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
