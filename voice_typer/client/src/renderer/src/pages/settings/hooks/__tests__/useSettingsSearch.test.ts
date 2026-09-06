/**
 * Focused tests for `useSettingsSearch` — the extracted Settings search
 * derivations + label-based auto-switch hook.
 *
 * Pins the ONE-match-predicate contract: a label matches when the LABEL
 * contains the query (case-insensitive substring), and the SAME predicate
 * feeds the empty-banner sentinel, the cross-section result groups, and
 * the auto-switch. Also pins the memoized label universe: the
 * PrewarmAndUpdates row labels are folded into the Advanced page's set so
 * queries like "prewarm" route to the page where that component lives.
 *
 * The hook takes `query` / `activeSection` / `navigate` as plain params,
 * so these tests drive it directly via rerender — no store or routing
 * mocks required.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
	searchLabelMatches,
	useSettingsSearch,
} from "@/pages/settings/hooks/useSettingsSearch";

const nav = vi.fn();

beforeEach(() => {
	nav.mockReset();
});

afterEach(() => {
	vi.clearAllMocks();
});

describe("searchLabelMatches (the ONE shared match predicate)", () => {
	it("matches when the LABEL contains the query, case-insensitively", () => {
		expect(searchLabelMatches("LLM Polishing", "llm pol")).toBe(true);
		expect(searchLabelMatches("Prewarm Status", "PREWARM")).toBe(true);
		expect(searchLabelMatches("Theme", "")).toBe(true);
	});

	it("does NOT match the superstring direction (query contains the label)", () => {
		// The old split semantics navigated on q.includes(label); the
		// unified strict semantic must not.
		expect(searchLabelMatches("Theme", "the theme settings rows")).toBe(false);
	});

	it("does not match when the label lacks the query", () => {
		expect(searchLabelMatches("Overlay", "hotkey")).toBe(false);
	});
});

describe("useSettingsSearch — memoized label universe", () => {
	it("folds the PrewarmAndUpdates labels into the Advanced page's set", () => {
		const { result } = renderHook(() =>
			useSettingsSearch({
				query: "",
				activeSection: null,
				navigate: nav,
			}),
		);
		const labels = result.current.sectionLabelsByPage;
		// A row label that only exists on the PrewarmAndUpdates
		// component (rendered on the Advanced page).
		expect(labels.settingsAdvanced).toContain("Prewarm Status");
		// The section's own labels are still present.
		expect(labels.settingsGeneral.length).toBeGreaterThan(0);
	});
});

describe("useSettingsSearch — auto-switch navigation", () => {
	it("navigates to the best-matching section page on a post-mount query change", () => {
		const { rerender } = renderHook(
			(props: { query: string; activeSection: "settingsGeneral" | null }) =>
				useSettingsSearch({
					query: props.query,
					activeSection: props.activeSection,
					navigate: nav,
				}),
			{ initialProps: { query: "", activeSection: "settingsGeneral" } },
		);

		// The very first render is skipped (stale-query guard): the
		// mount itself never navigates.
		expect(nav).not.toHaveBeenCalled();

		// The first POST-MOUNT query change navigates. "Appearance"
		// is the Appearance section's own label — longer than any
		// other match, so it wins.
		rerender({ query: "appearance", activeSection: "settingsGeneral" });
		expect(nav).toHaveBeenCalledTimes(1);
		const [page, opts] = nav.mock.calls[0] ?? [];
		expect(page).toBe("settingsAppearance");
		expect(opts).toMatchObject({
			settingsScrollTarget: { rowHint: expect.any(String) },
		});
	});

	it("skips the mount effect run so a stale query cannot yank the user on mount", () => {
		// The stale-query-guard scenario: the global search store
		// still holds a query from a previous visit when the page
		// mounts. The mount effect run is skipped — no navigation
		// until the user actually CHANGES the query.
		const { rerender } = renderHook(
			(props: { query: string; activeSection: "settingsGeneral" | null }) =>
				useSettingsSearch({
					query: props.query,
					activeSection: props.activeSection,
					navigate: nav,
				}),
			{
				initialProps: {
					query: "appearance",
					activeSection: "settingsGeneral",
				},
			},
		);
		expect(nav).not.toHaveBeenCalled();

		// An identical re-render changes no effect deps — still no
		// navigation.
		rerender({ query: "appearance", activeSection: "settingsGeneral" });
		expect(nav).not.toHaveBeenCalled();

		// A genuine query change navigates normally.
		rerender({ query: "llm polishing", activeSection: "settingsGeneral" });
		expect(nav).toHaveBeenCalledTimes(1);
		const [page] = nav.mock.calls[0] ?? [];
		expect(page).toBe("settingsAI");
	});

	it("does NOT navigate when the query is shorter than 2 characters", () => {
		const { rerender } = renderHook(
			(props: { query: string; activeSection: "settingsGeneral" | null }) =>
				useSettingsSearch({
					query: props.query,
					activeSection: props.activeSection,
					navigate: nav,
				}),
			{ initialProps: { query: "", activeSection: "settingsGeneral" } },
		);
		rerender({ query: "a", activeSection: "settingsGeneral" });
		rerender({ query: "a", activeSection: "settingsGeneral" });
		expect(nav).not.toHaveBeenCalled();
	});

	it("does NOT navigate on the hub (activeSection null)", () => {
		const { rerender } = renderHook(
			(props: { query: string; activeSection: "settingsGeneral" | null }) =>
				useSettingsSearch({
					query: props.query,
					activeSection: props.activeSection,
					navigate: nav,
				}),
			{ initialProps: { query: "", activeSection: null } },
		);
		rerender({ query: "appearance", activeSection: null });
		rerender({ query: "appearance", activeSection: null });
		expect(nav).not.toHaveBeenCalled();
	});
});

describe("useSettingsSearch — empty-banner sentinel + cross-section groups", () => {
	it("reports no visible row when NO label matches the query anywhere", () => {
		const { result } = renderHook(() =>
			useSettingsSearch({
				query: "zzqqxx no such label",
				activeSection: "settingsGeneral",
				navigate: nav,
			}),
		);
		expect(result.current.hasAnyVisibleRow).toBe(false);
	});

	it("groups matches from OTHER section pages, excluding the active one", () => {
		const { result } = renderHook(() =>
			useSettingsSearch({
				query: "appearance",
				activeSection: "settingsGeneral",
				navigate: nav,
			}),
		);
		const groups = result.current.otherSectionGroups;
		expect(groups.length).toBeGreaterThan(0);
		expect(groups.some((g) => g.sectionPage === "settingsAppearance")).toBe(
			true,
		);
		// The active page's own group is filtered out inline by the
		// sections — it must not appear here either.
		expect(groups.some((g) => g.sectionPage === "settingsGeneral")).toBe(false);
	});

	it("returns no cross-section groups when the query is empty or matches nothing", () => {
		const empty = renderHook(() =>
			useSettingsSearch({
				query: "",
				activeSection: "settingsGeneral",
				navigate: nav,
			}),
		);
		expect(empty.result.current.otherSectionGroups).toEqual([]);

		const nomatch = renderHook(() =>
			useSettingsSearch({
				query: "zzqqxx",
				activeSection: "settingsGeneral",
				navigate: nav,
			}),
		);
		expect(nomatch.result.current.otherSectionGroups).toEqual([]);
	});

	it("auto-switch and the empty-banner sentinel agree on a superstring-only query", () => {
		// The regression contract: the superstring direction must not
		// navigate NOR keep the banner hidden.
		const { rerender, result } = renderHook(
			(props: { query: string; activeSection: "settingsGeneral" | null }) =>
				useSettingsSearch({
					query: props.query,
					activeSection: props.activeSection,
					navigate: nav,
				}),
			{ initialProps: { query: "", activeSection: "settingsGeneral" } },
		);
		act(() => {
			rerender({
				query: "the llm polishing rows",
				activeSection: "settingsGeneral",
			});
			rerender({
				query: "the llm polishing rows",
				activeSection: "settingsGeneral",
			});
		});
		expect(nav).not.toHaveBeenCalled();
		expect(result.current.hasAnyVisibleRow).toBe(false);
	});
});
