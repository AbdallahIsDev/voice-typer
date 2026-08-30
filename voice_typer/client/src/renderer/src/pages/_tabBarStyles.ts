// Shared sticky tab-bar class names.
//
// (UI/UX overhaul 2026-08-20): the Models page tab switcher was moved
// into the page flow (point 2) and no longer uses the sticky bar;
// `tabPageHeaderClassName` is now consumed by Settings only. The
// indicator styling (`tabPageIndicatorClassName`) is still shared by
// both pages' SegmentedControls.
//
// Settings renders a sticky SegmentedControl tab bar at the top of the
// main content area; both pages had drifted in visual treatment:
//
// Settings.tsx (old):
// wrapper: `sticky top-0 left-0 right-0 z-40 bg-(--bg-subtle) border-b border-border`
// inner:   `mx-auto w-full max-w-4xl px-16 py-1.5`
// SegmentedControl indicator: `bg-input/50`
// SegmentedControl label:     `flex-1 text-center`
// SegmentedControl className:  `w-full`
//
// Models.tsx (old):
// wrapper: `sticky left-0 right-0 top-0 z-50` (no bg, no border)
// inner:   `mx-auto w-full max-w-4xl px-16 py-1.5`
// SegmentedControl indicator: `bg-(--bg) border border-border/5`
// SegmentedControl label:     `flex-1 text-center`
// SegmentedControl className:  `bg-(--bg-subtle) rounded-lg w-full`
//
// The differences: z-index (40 vs 50), wrapper bg + border (Settings had
// it, Models didn't), indicator style (input/50 vs bg+border), and the
// SegmentedControl className (Models added bg-subtle + rounded-lg).
//
// This module exports two constants — `tabPageHeaderClassName` (the
// wrapper + inner divs collapsed into one className string applied to
// the outer sticky element) and `tabPageIndicatorClassName` (the
// SegmentedControl indicator/label/className props collapsed into a
// single set of overrides). Both pages now use the same values, so a
// future tweak to one propagates to the other automatically.
//
// The constants live under `pages/` (not `components/common/PageTabs.tsx`
// as the finding originally suggested) because the latter is owned by
// Fix-C; keeping the file under `pages/_tabBarStyles.ts` keeps it within
// this sub-agent's file scope while still providing a single source of
// truth for the two pages that need it.

/**
 * Class names for the sticky tab-bar wrapper used by Settings and Models.
 *
 * Standardises:
 *  - `sticky top-0 z-50` — same z-index on both pages so a transient
 *    overlay (e.g. a Radix Portal tooltip or dropdown) doesn't get
 *    hidden behind one page's tab bar and shown above the other's.
 *  - `bg-(--bg-subtle) border-b border-border` — the tab bar gets its
 *    own subtle background + bottom border so it reads as a distinct
 *    sticky region even when the page content scrolls under it.
 *    Models previously had no bg/border (the tab bar inherited the
 *    page bg, so the tabs visually merged into the content when
 *    scrolled).
 *  - `left-0 right-0` — explicit horizontal anchoring (was already on
 *    both pages; kept for clarity).
 *
 * The inner max-w-4xl + px-16 + py-1.5 wrapper is NOT included here —
 * each page renders it inline (the inner wrapper sometimes also
 * contains a search field or other controls that vary by page).
 */
export const tabPageHeaderClassName =
	"sticky left-0 right-0 top-0 z-50 bg-(--bg-subtle) border-b border-border/5";

/**
 * Standard SegmentedControl visual overrides for the Models page tab
 * bar. Apply to the `<SegmentedControl>` component as:
 *
 *   ```tsx
 *  <SegmentedControl
 *    variant="tabs"
 *    ...
 *    indicatorClassName={tabPageIndicatorClassName}
 *    labelClassName="flex-1 text-center"
 *    className="w-full"
 *  />
 *  ```
 *
 * The indicator uses `bg-(--bg) border border-border/5` — the SAME
 * border token as the model cards beneath the tab bar
 * (`ModelGroupAccordion` uses `rounded-lg border border-border/5
 * bg-(--bg-subtle)`). The active segment therefore reads as the same
 * card/surface treatment as the model list it controls, rather than a
 * darker `border-border/5` outlier. (2026-08-21: previously /75;
 * changed to the card token so the segmented control and its cards
 * share one border language.)
 */
export const tabPageIndicatorClassName = "bg-(--bg) border border-border/5";
