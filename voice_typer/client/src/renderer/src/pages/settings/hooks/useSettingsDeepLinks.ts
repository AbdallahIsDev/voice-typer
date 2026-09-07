// Settings deep-link consumption + scroll/highlight hook.
//
// Extracted from `pages/Settings.tsx` (page-root slimming): the consent
// deep-link (``client.consent_required`` path) and the cross-page search
// deep-link machinery — two near-twin "consume → scroll-to-row → ring"
// effects plus their shared one-shot guard, ring-lifetime timer, and
// max-lifetime safety net — was the page's largest cohesive EFFECT
// block. It lives here so the page root stays layout + wiring.
//
// The two scroll effects share ONE parameterized helper
// (`pages/settings/lib/scrollToRowWithHighlight.ts`) — the bounded
// retry loop, the one-shot guard, the scrollIntoView call, and the
// ring-lifetime timer live there once; each effect supplies only its
// row matcher and how the ring is applied (consent: state-driven,
// consumed by PrivacySettingsSection; search: imperative ring classes).
//
// IMPORTANT: the effects inside this hook must run in the ORIGINAL page
// order (consume → consume → scroll → scroll → safety net) relative to
// the page's surface-scroll restore effect — the consent consumption
// zeroes the saved privacy-page scroll offset BEFORE the restore effect
// reads it. The page must call this hook BEFORE `useSettingsSurfaceScroll`.

import { useEffect, useRef, useState } from "react";
import { useGlobalSearch } from "@/hooks/useGlobalSearch";
import { useNavigation } from "@/hooks/useNavigation";
import { scrollToRowWithHighlight } from "@/pages/settings/lib/scrollToRowWithHighlight";
import type { VoiceTyperConfig } from "@/types/config";
import type { Page } from "@/types/ipc";

export interface UseSettingsDeepLinksOptions {
	/** The loaded config (or `null` while loading) — gates the scroll effects. */
	config: VoiceTyperConfig | null;
	/** The active Settings surface page literal (route-switch prop). */
	page: Page;
	/**
	 * The page's per-surface scroll-position memory (owned by the page,
	 * shared with `useSettingsSurfaceScroll`). The consent consumption
	 * effect zeroes the Privacy surface's saved offset so the restore
	 * behavior doesn't fight the deep-link scroll.
	 */
	scrollPositionsRef: React.RefObject<Record<string, number>>;
}

export interface UseSettingsDeepLinksReturn {
	/** The consent field currently highlighted on the Privacy page (or null). */
	focusedConsentField: string | null;
	/** The search rowHint currently being scrolled to + ringed (or null). */
	searchScrollHint: string | null;
}

/**
 * Consent + search deep-link handling for the Settings page: consumes
 * the pending targets from the nav store, scrolls the target row into
 * view (bounded retry), and rings it for the highlight lifetime. See
 * the file header for the extraction rationale and ordering contract.
 */
export function useSettingsDeepLinks({
	config,
	page,
	scrollPositionsRef,
}: UseSettingsDeepLinksOptions): UseSettingsDeepLinksReturn {
	// Consent deep-link (``client.consent_required`` path). A consent
	// refusal elsewhere (mic test / level monitor / dictation gate)
	// navigates here with ``{ consentField }`` (see NavigateOptions in
	// useNavigation.ts); the field is staged in the nav store as
	// ``pendingConsentField`` and consumed ONCE on the Privacy section
	// page — the only surface that renders the consent toggles.
	const [focusedConsentField, setFocusedConsentField] = useState<string | null>(
		null,
	);
	// One-shot scroll guard + ring-lifetime timer for the consent
	// deep-link highlight (see the scroll effect below). Also reused
	// for the cross-page Settings search deep-link highlight — both
	// share the same ring-lifetime mechanism since only one deep-link
	// target can be active at a time.
	const scrolledTargetRef = useRef<string | null>(null);
	const highlightTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
	// Cross-page search deep-link target hint (consumed on section-page
	// mount + on page change). The Settings search may fire this from
	// any section page — the source page sets it via
	// `navigate(bestPage, { settingsScrollTarget: { rowHint } })`,
	// the destination page consumes it here.
	const [searchScrollHint, setSearchScrollHint] = useState<string | null>(null);
	const {
		pendingConsentField,
		consumeConsentField,
		pendingSettingsScrollTarget,
		consumeSettingsScrollTarget,
	} = useNavigation();
	const clearQuery = useGlobalSearch((s) => s.clearQuery);

	// Consume the pending consent deep-link target: clear any active
	// search filter (so the consent row is visible) and arm the
	// highlight state. The nav store routed the user to
	// "settingsPrivacy"; we just arm the highlight here.
	useEffect(() => {
		if (!pendingConsentField) return;
		const field = consumeConsentField();
		if (!field) return;
		clearQuery();
		scrollPositionsRef.current.settingsPrivacy = 0;
		setFocusedConsentField(field);
	}, [
		pendingConsentField,
		consumeConsentField,
		clearQuery,
		scrollPositionsRef,
	]);

	// Consume the pending cross-page Settings search deep-link target.
	// Mirrors the consent-deep-link consumption: clear the search filter
	// (so the matched row is visible) + arm the highlight state with the
	// rowHint (the matched label string) so the scroll effect can find
	// + ring the matching element by visible-text content.
	useEffect(() => {
		if (!pendingSettingsScrollTarget) return;
		const target = consumeSettingsScrollTarget();
		if (!target) return;
		clearQuery();
		const hint = target.rowHint;
		if (hint) setSearchScrollHint(hint);
	}, [pendingSettingsScrollTarget, consumeSettingsScrollTarget, clearQuery]);

	// Scroll the deep-linked consent row into view once it's rendered
	// (Privacy section page active + config loaded). The row is rendered
	// by PrivacySettingsSection with a ``data-consent-field`` attribute;
	// the shared scrollToRowWithHighlight helper retries until found
	// (bounded) in case the lazy page / config fetch is still settling.
	// The scroll is ONE-SHOT per deep-link target (the helper's shared
	// ``scrolledTargetRef`` guard) so a config identity change — e.g. the
	// user toggling the just-highlighted consent — doesn't re-trigger a
	// smooth re-center. The highlight ring's lifetime starts when the
	// row is actually found, so a slow ``get_config`` can't clear the
	// ring before the row renders. The ring itself is state-driven:
	// PrivacySettingsSection rings the row whose field matches
	// ``focusedConsentField``, so expiring just clears the state.
	useEffect(() => {
		if (!focusedConsentField || !config || page !== "settingsPrivacy") return;
		return scrollToRowWithHighlight({
			shared: {
				scrolledTarget: scrolledTargetRef,
				highlightTimer: highlightTimerRef,
			},
			target: focusedConsentField,
			// Match by attribute VALUE rather than interpolating the
			// field into a selector — the field comes from the backend
			// envelope, and value-filtering avoids any selector
			// injection edge.
			matchFn: () =>
				Array.from(
					document.querySelectorAll<HTMLElement>("[data-consent-field]"),
				).find(
					(node) =>
						node.getAttribute("data-consent-field") === focusedConsentField,
				),
			onExpire: () => setFocusedConsentField(null),
		});
	}, [focusedConsentField, config, page]);

	// Cross-page Settings search deep-link scroll + highlight. Mirrors
	// the consent-deep-link scroll via the SAME shared helper but
	// matches by VISIBLE TEXT (the rowHint string) rather than by
	// attribute value — the search deep-link carries the matched label
	// text (translated at the moment the user typed), so we walk
	// rendered SettingRow elements and pick the first whose label text
	// contains the hint. The match is intentionally substring +
	// case-insensitive so a partial hint (e.g. trailing whitespace)
	// still resolves. The ring is applied imperatively (ring classes on
	// the matched element) rather than via state.
	useEffect(() => {
		if (!searchScrollHint || !config) return;
		const hint = searchScrollHint.toLowerCase();
		return scrollToRowWithHighlight({
			shared: {
				scrolledTarget: scrolledTargetRef,
				highlightTimer: highlightTimerRef,
			},
			target: searchScrollHint,
			matchFn: () =>
				// SettingRow renders the row label inside a <span> with class
				// `text-(--text-primary)`. We walk all rows on the page and
				// pick the first whose label text contains the hint.
				Array.from(
					document.querySelectorAll<HTMLElement>("[data-settings-row-label]"),
				).find((node) => (node.textContent ?? "").toLowerCase().includes(hint)),
			onFound: (el) =>
				el.classList.add(
					"ring-2",
					"ring-ring",
					"ring-offset-2",
					"ring-offset-background",
				),
			onExpire: (el) => {
				setSearchScrollHint(null);
				el.classList.remove(
					"ring-2",
					"ring-ring",
					"ring-offset-2",
					"ring-offset-background",
				);
			},
		});
	}, [searchScrollHint, config]);

	// Max-lifetime safety net: even if the target row never renders
	// (e.g. an unknown ``consent_field`` or a stale search hint), the
	// highlight can't linger indefinitely.
	useEffect(() => {
		if (!focusedConsentField && !searchScrollHint) return;
		const timer = setTimeout(() => {
			setFocusedConsentField(null);
			setSearchScrollHint(null);
			scrolledTargetRef.current = null;
		}, 5000);
		return () => clearTimeout(timer);
	}, [focusedConsentField, searchScrollHint]);

	return { focusedConsentField, searchScrollHint };
}
