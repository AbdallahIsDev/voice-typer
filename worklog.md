# Voice Typer — Multi-Agent Work Log

---
Task ID: 0-orchestration
Agent: orchestrator (main)
Task: Frontend component audit + Settings sidebar redesign (per user prompt, override B)

Override record:
- User explicitly chose option "B" in chat: override AGENTS.md §0 (INVESTIGATION_MODE / FIX_EXISTING split).
- All other AGENTS.md constraints remain in force: E1, E6, E10, E14, E15, E16, E18, W0, W2, C-BRAND-1, C-I18N-1/2, C-STYLE-1, C-ARCH-1, C-TEST-1/2/3/4/5, C-LOG-1/2.
- Investigation AND implementation will run in this single session as the user authorized.
- Decisions will be recorded in `docs/adr/` per project convention (rejected alternatives included).

Work Log:
- Read full AGENTS.md (964 lines) — confirmed binding rules and Hard "Don'ts".
- Cloned repo: `git clone https://github.com/AbdallahIsDev/voice-typer.git` → `/home/z/my-project/voice-typer/`.
- Established baseline test counts (E2) below before any change.

Stage Summary:
- Repo cloned and ready.
- AGENTS.md read; all binding constraints acknowledged.
- Override B logged for §0 only.

## E2 Baseline (session start)
- Repo freshly cloned; `npm ci` clean (904 packages, 0 vulnerabilities per package.json `//overrides_note`).
- `npm run typecheck` → exit 0 (clean) on `tsconfig.web.json` + `tsconfig.node.json`.
- Test count: 323 test files in `src/renderer/src/**`. Full Vitest suite exceeds the 10-min tool ceiling, so during regression I will run targeted subsets per module touched (Sidebar / Settings / Dashboard / etc.) per AGENTS.md validation pipeline.
- Existing shadcn UI primitives on disk: accordion, alert-dialog, button, checkbox, dialog, dropdown-menu, input, kbd, number-input-stepper, segmented-control, select, slider, sonner, switch, tooltip.
- shadcn `components.json` configured: style `radix-luma`, baseColor `zinc`, icon library `hugeicons`, aliases `@/components`, `@/components/ui`, `@/lib/utils`.

---
Task ID: 3-A
Agent: investigation-A (Sidebar + Settings + Layout + Settings search + Segmented Control)
Task: Read-only audit of the Sidebar/Settings/Layout partition per the orchestrator's file-disjoint split. Produce a structured Markdown report covering (1) current Sidebar architecture, (2) current Settings architecture, (3) Segmented Control audit + all call sites, (4) SearchField audit + coupling, (5) router/routes audit, (6) shadcn Sidebar capability comparison (W0 web-verified), (7) per-component KEEP/REPLACE/REFACTOR/REMOVE decisions, (8) concrete Settings-sidebar redesign recommendation, (9) risks & gotchas (AGENTS.md constraints, test impact, IPC contract).

Work Log:
- Read full AGENTS.md (964 lines) — noted binding rules: E1, E6, E10, E12, E14, E15, E16, E18, W0, W2, C-BRAND-1, C-I18N-1/2, C-STYLE-1, C-ARCH-1, C-TEST-1..5, C-LOG-1/2.
- Read worklog.md (30 lines) for orchestrator context — confirmed partition scope + shadcn primitives inventory.
- Audited Sidebar.tsx (454 lines): 3 nav groups (Main/Power/System), 10 nav items, roving-tabindex ArrowUp/Down/Home/End, aria-current="page", HotkeyTooltip on collapsed items, ThemeSwitch footer, Logo-as-button in collapsed mode. No nested submenu today.
- Audited Logo.tsx, ThemeSwitch.tsx, TitleBar.tsx (sidebar-toggle wiring via `onToggleSidebar`), ConnectionStatusScreen.tsx.
- Audited Settings.tsx (578 lines): 4 tabs (`general | aiAudio | appearance | privacy`) persisted in localStorage `voice-typer-settings-tab`. Search via `SearchField` + label-score auto-switch. Consent deep-link via `pendingConsentField` from `useNavigation()`. Tab UI uses `<SegmentedControl variant="tabs">`.
- Audited `_tabBarStyles.ts` — shared sticky-tab styling co-consumed by Settings + Models.
- Audited `settingsTabLabels.ts` — confirms exactly 4 SettingsTab keys; no extra tabs in code.
- Audited `components/settings/{GeneralSettingsSection, RecordingSettingsSection, AudioSettingsSection, AiEnhancementSettingsSection, ModelSettingsSection, ThemeSettingsSection, PrivacySettingsSection, TroubleshootingSettingsSection, DiagnosticsSettingsSection, ResourcesSettingsSection, PrewarmAndUpdates, SettingsSkeleton, useSettingsConfig, useThemeSettings, themeColorCache, types}.tsx`. Each `<*SettingsSection />` takes `isVisible` predicate + config props; `SettingsSection` (card wrapper) + `SettingRow` (label/info/control) shared primitives.
- Audited `segmented-control.tsx` (469 lines): dual-mode (`variant="tabs"` → role=tablist+roving-tabindex+ArrowLeft/Right+RTL; `variant="default"` → role=radiogroup+sr-only radios). Found 7 production call sites (Settings tabs, Models tabs, TimeRangeSelector, ThemeSettingsSection color-scheme, RecordingSettingsSection recording-mode, GeneralSettingsSection tray-click + bubble-position + bubble-behavior). CANNOT be removed — used heavily inside individual settings rows.
- Audited `SearchField.tsx` (82 lines): pure controlled input (value + onChange) wrapped in `role="search"` landmark. Decoupled from Settings tab logic — the auto-switch lives in Settings.tsx:215-234 (`handleSearchChange`) using `getTabLabels()` + `getPrewarmAndUpdatesLabels()`.
- Audited `router/routes.ts` (58 lines) + `useNavigation.ts` (425 lines): NO react-router, NO TanStack Router. Custom Zustand store `useNavStore` with `Page` union (11 literals: home/history/templates/vocabulary/models/microphone/analytics/settings/onboarding/about/privacy). `navigate(page, opts?)` with `consentField` option already wired for transient deep-link pattern. Persistence to localStorage `vt_nav_state`. Mouse X1/X2 + Alt+Arrow back/forward.
- Audited App.tsx (622 lines): `renderPage()` switch at line 421 wires `<SettingsPage />` for `case "settings"`. Sidebar collapsed state owned by App.tsx `useState(false)` + auto-collapse on `(max-width: 640px)` via `useMediaQuery`. Ctrl+B bound via `useGlobalKeyboardShortcuts`.
- Read Sidebar tests (3 files, 944 lines total): `__tests__/a11y-rewrite/Sidebar-aria-current.test.tsx` asserts aria-current="page" on active item + roving-tabindex ArrowUp/Down/Home/End + aria-keyshortcuts (`Control+h` Home, `Control+,` Settings). `components/__tests__/Sidebar.test.tsx` + `components/layout/__tests__/Sidebar.test.tsx` assert 10 nav labels, 3 group sections, aria-label, ThemeSwitch wiring, collapsed-logo button navigates home.
- Read Settings tests (3 files, 904 lines total): `pages/__tests__/Settings.test.tsx` asserts PERF-002 batched writes + consent deep-link jumps to Privacy tab + highlight ring on `[data-consent-field]`. `Settings-empty-state.test.tsx` asserts "No settings match" banner appears/disappears on filter. `Settings-render-loop-guard.test.tsx` guards against the call-identity render-loop OOM.
- W0 web-verified shadcn Sidebar API at https://ui.shadcn.com/docs/components/base/sidebar (page_reader): `SidebarProvider`, `Sidebar` (side/variant/collapsible), `SidebarHeader/Footer/Content`, `SidebarGroup[Label/Action/Content]`, `SidebarMenu`, `SidebarMenuItem`, `SidebarMenuButton` (`isActive`, `render` polymorphic), `SidebarMenuAction`, `SidebarMenuBadge`, `SidebarMenuSub`, `SidebarMenuSubItem`, `SidebarMenuSubButton`, `SidebarMenuSkeleton`, `SidebarRail`, `SidebarInset`, `SidebarTrigger`, `useSidebar()` hook (`state`/`open`/`setOpen`/`openMobile`/`setOpenMobile`/`isMobile`/`toggleSidebar`). Designed for URL routing + mobile sheet mode; SIDEBAR_KEYBOARD_SHORTCUT = "b" (cmd/ctrl+b).
- Verified i18n `settings.tabs.{general,aiAudio,appearance,privacy}` keys exist in ALL 8 locale files (`en/ar/de/es/fr/hi/ru/zh`) — properly translated (C-I18N-1/2 satisfied for the 4 child labels).
- Composed capability matrix comparing Voice Typer Sidebar ↔ shadcn Sidebar (15 features).
- Wrote per-component decisions: Sidebar (KEEP CUSTOM, extend with submenu), SegmentedControl (KEEP CUSTOM — used in 7 call sites, not Settings-only), SearchField (KEEP — pure controlled input, decoupled), Settings tab bar (REFACTOR — remove from Settings, move 4 tabs to nested Sidebar submenu), routes.ts (REFACTOR — extend Page union + ROUTES with 4 Settings sub-page literals).
- Wrote final architectural recommendation: hybrid approach — extend existing custom Sidebar with a nested submenu capability (re-using the existing `Button` + `HotkeyTooltip` + roving-tabindex pattern), extend `Page` union + `useNavigation` to support `/settings/general` etc. as flat page literals (NOT real URLs — the custom store has no URL router), preserve `pendingConsentField` transient pattern as the cross-page Settings search + deep-link mechanism (already proven).
- Final report returned to orchestrator inline (chat).

Stage Summary:
- Audit partition complete; 0 files modified (investigation-only per scope).
- All findings cited with `file:line` references in the returned report.
- W0 satisfied: shadcn Sidebar API verified via web-search + page_reader against the official docs URL.
- W2 satisfied: KEEP-CUSTOM recommendations are justified by real Voice Typer constraints (Electron desktop app, no URL router, custom Zustand nav store, HotkeyTooltip with Kbd chips, 7 SegmentedControl call sites, custom theme tokens) — not mechanical deferral.
- C-BRAND-1 noted: Settings redesign MUST continue to use `APP_NAME` from `@/branding` for any brand display (no hardcoded "Voice Typer"). Already satisfied today by Sidebar.tsx:16,260.
- C-I18N-1/2 noted: 4 child tab labels (`settings.tabs.*`) already exist in all 8 locales — no new keys needed for the labels themselves. Any NEW affordances added by the redesign (e.g. "Settings sub-navigation" aria-label, expand/collapse tooltip) MUST be added to all 8 locale files.
- C-ARCH-1 noted: App.tsx stays ≤ ~300 lines wiring-only (currently 622 — already over the soft limit, but the redesign does NOT add logic to App.tsx; it modifies the `renderPage()` switch).
- C-STYLE-1 noted: no task IDs / session prefixes to appear in any new source code; only in this worklog entry.
- Test impact identified: Sidebar tests will need updating to assert the new nested submenu (4 Settings children). Settings tests will need updating to reflect the moved tab UI + the new cross-page search deep-link mechanism. Existing consent-deep-link test (`pages/__tests__/Settings.test.tsx:370`) already validates the transient field pattern — extend it.
- IPC contract: NO IPC impact. Settings routing is purely renderer-side (Page union is a TS type, not a server-registered command). The `set_config` allowlist (SEC-002), the `_COMMAND_REGISTRY`, `ALLOWED_COMMANDS`, and `PythonRequest`/`PythonPushEvent` unions are untouched by this redesign.
- Ready for handoff to implementation sub-agents.

---
Task ID: 3-B
Agent: investigation-B (Dashboard + Activity chart + StatCards + Share + Bubble + Onboarding + Home)
Task: Read-only audit of the Dashboard/Chart/Bubble/Home/Onboarding partition per the orchestrator's file-disjoint split. Produce a structured Markdown report covering (1) Activity chart library/visual/interactions/a11y/tests + KEEP/REPLACE/REFACTOR decision, (2) StatCards + DashboardStatCard + QuickInfoCard + ActivityList audit, (3) ShareStatsDialog + StatsShareImage audit, (4) TimeRangeSelector, (5) DashboardSkeleton, (6) Bubble audit (per-component), (7) Home page audit (RecordingStatusPill / MicToggleButton / LastTranscriptionPreview), (8) Onboarding audit (Stepper + 7 step components), (9) per-component decision matrix, (10) AGENTS.md-constraint risks + test impact.

Work Log:
- Read full AGENTS.md (964 lines) — binding rules E1/E6/E10/E12/E14/E15/E16/E18/W0/W2/C-BRAND-1/C-I18N-1/2/C-STYLE-1/C-ARCH-1/C-TEST-5/C-LOG-1/2 acknowledged.
- Read worklog.md (69 lines) for orchestrator + Sub-Agent A context: shadcn primitives inventory (accordion, alert-dialog, button, checkbox, dialog, dropdown-menu, input, kbd, number-input-stepper, segmented-control, select, slider, sonner, switch, tooltip — NO card/skeleton/progress/tabs/chart on disk), components.json `radix-luma`/zinc/hugeicons config, vitest threads pool, locale files (en/ar/de/es/fr/hi/ru/zh).
- W0 web-verified shadcn Chart API at https://ui.shadcn.com/docs/components/chart via z-ai page_reader: confirmed Chart is a thin wrapper over **recharts** (not bundled by Voice Typer today — `package.json` deps: recharts / d3 / visx / @nivo / chart.js / tremor / victory / plotly → NONE installed). Public API: `ChartContainer`, `ChartTooltip`, `ChartTooltipContent`, `ChartLegend`, `ChartLegendContent`, `ChartConfig` type (satisfies pattern `{ label, color | theme: { light, dark }, icon? }`), `accessibilityLayer` prop on the chart root, theming via `--chart-1..5` CSS custom properties (5 colors). Installed via `pnpm dlx shadcn@latest add chart`.
- Read Dashboard.tsx (401 lines): thin composition root — uses `PageHeading` + `DashboardStatCard` (×4) + `QuickInfoCard` (×6) + `ActivityChart` + `ShareStatsDialog` + `StatsShareImage` (off-screen capture target) + `EmptyState` + `KeyboardPermissionBanner` + `TimeRangeSelector` + `LastUpdatedIndicator`. Module-level `SHARE_IMAGE_CAPTURE_STYLE` constant (DJ-93 test asserts hoisted object identity). `computeTrend` helper for vs-previous-period arrow.
- Read SevenDayActivityChart.tsx (177 lines) — FULLY CUSTOM div-based bar chart (NOT SVG, NOT recharts, NOT d3). Range-aware (hourly 24-bar for "Today", daily N-bar for 7d/30d/all). Y-axis with `[max, mid, 0]` tick labels (mid dropped when duplicating max). Gridlines as `border-t` divs. Bars are non-interactive `<div>` with native `title=` tooltip per bar (NO React tooltip). `bg-accent/90 hover:bg-accent` fill (WCAG 1.4.11 — test asserts NOT `/60`). Zero-vs-missing distinction: zero → solid `h-1 bg-border/50`; missing → `h-1 border-t border-dashed border-border/30 bg-transparent`. Container `role="img"` + `aria-label={t("analytics.activityChartAria", {range, counts})}` (single AT announcement — bars carry no tabIndex/aria-label by design). `tChoice("analytics.dayCountTooltip", bar.count, {label})` for pluralized tooltip. Uses `HugeiconsIcon` (Activity03Icon) + theme tokens (`--bg-subtle`, `--text-muted`, `--text-primary`, `--border`). Fixed `h-36` plot height (NOT height-responsive — but the WIDTH is responsive via `flex-1`).
- Read TimeRangeSelector.tsx (37 lines) — thin wrapper over shared `SegmentedControl variant="default" radius="pill"` (Sub-Agent A confirmed KEEP CUSTOM — used in 7 call sites, not Settings-only). 4 ranges: today / 7d / 30d / all.
- Read DashboardSkeleton.tsx (90 lines) — pure `animate-pulse` Tailwind utility, hand-rolled. Mirrors loaded dashboard (heading + 4 stat cards + chart bars + quick-info row). `aria-label=t("analytics.loadingAria")` + `aria-busy="true"`. NOT using shadcn `Skeleton` (not on disk).
- Read useDashboardData.ts (408 lines) — owns the SINGLE source of truth: 500-row history sample → period / activity / correctionStats / streaks computed in one pass. `get_config` + `get_history` + `get_history_count` + `get_status` + `get_correction_usage` + `get_model_status` in Promise.all (PERF-005 batched). `callRef`/`markUpdatedRef` mirrors for stable identity ([] deps). `transcription_final` + `history_changed` debounced refresh (500ms), visibility-gated. Error → `toast.error` + `fetchError` state.
- Read streaks.ts (528 lines) — pure helpers: `localDateKey`, `parseUtcTimestamp` (handles SQLite `YYYY-MM-DD HH:MM:SS` UTC + Z/zone/ISO variants), `dateKey`, `computeDailyActivity` (7-day trailing), `computeStreaks` (current/max/activeDays), `computePeriodStats` (range-aware count/chars/wordCount/duration/activeDays/avgCharsPerDictation/longestSession/peakWeekday/prev), `computeCorrectionStats` (range-aware corrections/dictations/rate/prevCorrections), `buildActivityBars` (24-hourly for today, N-daily otherwise — with `isMissing` flag for future hours / sample-out-of-range days), `rangeDaySpan` RangeId → number|null. UTC-correct bucketing (the documented data-consistency fix).
- Read format.ts (72 lines) — pure helpers: `barHeight` (unused today), `dayAbbr`, `weekdayLabel` (0..6 → i18n keys), `dayLabel` (Today/Yesterday/Intl.DateTimeFormat fallback).
- Read StatCards.tsx (99 lines) — Home page's top-row 3-card strip (Dictations / Characters / Duration). Pure presentational. NOT shadcn Card. Uses `formatCompactNumber` (= `compactNumber(n, { plusSuffix: true, localeAware: true })`) + shared `formatDuration` from `@/lib/format` (W2 satisfied — was a duplicated inline copy before). `React.memo` wrapped. Has Storybook stories.
- Read DashboardStatCard.tsx (100 lines) — Analytics top-row 4 cards. Custom layout: icon + label row at top, value pushed down via `mt-auto`, optional `sublabel` + optional `TrendIndicator` (▲▼/– glyph + pct + role="img" + localized aria-label). Trend colors: emerald (up), destructive (down), muted (flat). NOT shadcn Card.
- Read ActivityList.tsx (298 lines) — recent-activity list (Home + Dashboard shared). `ActivityListRow` is `React.memo` with primitive-only props (item, copied boolean, lineClamp, onCopy, onDelete, onToggleFavorite). Actions use shadcn `Button variant="ghost" size="icon-xs"` (Copy/Star/Delete). Empty state inline. Outer container is custom div (`rounded-lg border bg-(--bg-subtle) divide-y divide-border/10`). NOT a list of shadcn Cards (correct — this is a list, not card grid).
- Read QuickInfoCard.tsx (66 lines) — Analytics secondary row + Current Setup section. `items-stretch` + `mt-auto` value-push. `muted` variant for the Current Setup section (smaller padding + `bg-(--bg-subtle)/50`). NOT shadcn Card.
- Read ShareStatsDialog.tsx (352 lines) — uses shadcn `Dialog`/`DialogContent size="lg"`/`DialogHeader`/`DialogTitle`/`DialogDescription`/`DialogTrigger asChild` (correct). Trigger is shadcn `Button variant="outline" size="icon"`. Export actions (Download/Copy/Save As) + 4 social targets (WhatsApp/Telegram/X/Facebook) are shadcn `Button variant="outline"`. Custom: `--preview-scale` CSS custom property written via callback ref + ResizeObserver so the 1200×630 image is fitted to the preview frame before first paint. SOCIAL_TARGETS table + `handleSocial` clipboard-fallback flow (web intents are text/URL-only on desktop). `APP_NAME` is dynamic (C-BRAND-1 satisfied). `STATS_IMAGE_FILENAME` constant.
- Read StatsShareImage.tsx (338 lines) — pure HTML/CSS 1200×630 share-card rendered off-screen, captured by `html-to-image`'s `toPng()` (already a dep — `html-to-image ^1.11.13`). NOT SVG, NOT canvas. ALL colors come from the `palette` prop (live theme tokens via `useThemePalette()`, with `FALLBACK_THEME_PALETTE` constant for tests). `legibleOn(palette.primary, palette.card, palette.foreground)` for accent legibility (WCAG 3:1). `mixHexColors` for card-surface lift. Private `StatCard` sub-component (NOT shadcn Card — html-to-image requires real DOM/CSS, not Radix shadow markup). One inline `<svg>` mic glyph (acceptable — bundled so html-to-image captures it). `direction: isRtl ? "rtl" : "ltr"` for RTL locales (ar/he). `React.memo` wrapped. C-BRAND-1 satisfied: `APP_NAME` import + `t("stats.shareImage.exportedFrom", { appName: APP_NAME })`.
- Read useStatsShare.ts (362 lines) — owns `toPng()` capture pipeline. `canShareStats({todayCount, totalCount})` shared helper (single source of truth for the share-button gating). `computeShareStats` pure function. Zero-data policy: no today activity → `wpmDisplay: "—"`, `fasterThanAvg: null`.
- Read Bubble.tsx (375 lines) — `<output aria-live="polite" aria-atomic="true">` wrapper + `aria-label=getBubbleAriaLabel(mode, errorMessage)` (state-aware). `<BubbleBridgeProvider>` wraps `<BubbleInner>`. `pillRef` + `useLayoutEffect` for BrowserWindow auto-resize (`offsetWidth+1` HiDPI rounding). `draggable`/`micButton`/`dismissable`/`bubbleBehavior` state from `bubble:config` IPC. Error-mode auto-hide (7s) for `show_on_record` behavior; sticky in `always_visible`. Dead keyboard-move handler is intentionally REMOVED — `focusable: false` BrowserWindow makes it dead code; product decision is mouse-drag-only.
- Read bubble-components.tsx (16 lines) — thin re-export shim (`export * from "./bubble"`). Legacy compatibility.
- Read bubble/index.ts (54 lines) — public surface of the bubble package: 5 components (BubbleVisualizer/MicButton/StopButton/DismissButton/ModeContent) + 5 hooks (useAudioLevels/useBubbleBridge/useBubbleLifecycle/useBubbleStateMachine/useThemeSync) + helpers + constants. All extracted from the former 823-line monolith.
- Read bubble/BubbleMicButton.tsx (84 lines) — native `<button type="button">` with `BUBBLE_BUTTON_CLASS` (NOT shadcn Button). Inline SVG mic/stop glyphs (`viewBox="0 0 24 24"`). `aria-label` + `title` populated. A11Y trade-off documented: bubble BrowserWindow is `focusable: false` (intentional — prevents stealing keyboard focus from user's text field) so the real `<button>` is mouse-only in production.
- Read bubble/BubbleStopButton.tsx (84 lines) — same pattern. Stop (filled square) + retry (circular arrow) SVGs. `tf()` translation-with-fallback helper for missing i18n keys.
- Read bubble/BubbleDismissButton.tsx (52 lines) — same pattern. '×' SVG.
- Read bubble/BubbleVisualizer.tsx (81 lines) — 7-bar spectrum. REC indicator + animated bars. `dotRefs` set via memoised ref setters. `DOT_INDICES` hoisted module constant (stable ref). Tailwind v4 `gap-0.75`/`w-0.75` (dynamic spacing scale). `bg-(--text-primary)` + `bg-destructive` tokens. `MIN_HEIGHT=5`, `MAX_HEIGHT=22`.
- Read bubble/BubbleModeContent.tsx (270 lines) — 8-way mode switch (transcribing / fading / idle / error / blocked / cancelling / permission_revoked / paste_failed / recording default). `<output aria-label=...>` for live transcript preview (semantic status region). 3 transcribing dots with staggered `animationDelay`. Idle sr-only announcement. `truncateTranscript` 60-char preview cap. DOM byte-identical to the pre-split inline ternary chain (test contract).
- Read bubble/constants.ts (196 lines) — `BubbleMode` union (9 modes), `AnimState`, `DOT_COUNT=7`, `MIN_HEIGHT=5`, `MAX_HEIGHT=22`, `DOT_WEIGHTS=[0.5,0.75,1.0,0.95,1.0,0.75,0.5]`, `DOT_INDICES`, `TRANSCRIBING_DOT_COUNT=3`, `FADEOUT_DURATION_MS=150`, `BUBBLE_BUTTON_CLASS` (shared className — `no-drag ms-1 h-6 w-6 rounded-full text-(--text-muted) hover:bg-(--surface-hover) hover:text-(--text-primary) focus-visible:ring-2 focus-visible:ring-ring`). `nextBubbleMode` pure reducer (IN-62 single source of truth). `parseSetStatePayload` defensive duck-typer.
- Read bubble/helpers.ts (87 lines) — `tf(key, fallback)` translation-with-fallback, `rmsToNorm(rms)` (×8 soft compressor), `getBubbleAriaLabel(mode, errorMessage)` (state-aware 7-deep switch — was inline ternary chain).
- Read bubble/useAudioLevels.ts (372 lines) — 60fps rAF direct-DOM height/opacity writes. Visibility + recording gates (loop stops when either closes). `prefers-reduced-motion` fallback (static mid-height bars). Dynamic `onLevel` IPC subscription (only while in recording mode — saves ~90% of the bubble's lifetime IPC marshalling). `MutationObserver` on `<html>` class/style for theme-preset switches (debounced via `queueMicrotask` — jsdom rAF setInterval 16ms broke the test). `bridge.on("level"/"show"/"setState")` consumers. `refreshBarColor` reads `--text-primary`/`--foreground` from getComputedStyle.
- Read Home.tsx (864 lines — over E3 ≤~300 soft limit, but pre-split was 949 — partial improvement; further split needed in a future wave). Composition root importing extracted `./home/{components,lib,hooks}/*`. `debouncedRefreshFromEvent` declared via `useCallback` + passed to BOTH `transcription_final` + `history_changed` (regression-test contract — kept in Home.tsx, not in hook). `StatusCard`/`MicToggleButton`/`RecordingStatusPill`/`LastTranscriptionPreview` extracted.
- Read home/components/MicToggleButton.tsx (93 lines) — native `<button type="button">` (NOT shadcn Button). `aria-pressed={isRecording}`. 84px round (`h-21 w-21`), `bg-destructive animate-glow-pulse` when idle, `bg-foreground/15` when recording. Pulse-ring overlay. Spinner overlay (`border-2 border-white/80 border-t-transparent animate-spin`) while `toggling`. `disabled` + `disabledReason` for aria-label substitution. `text-white` mic/stop icon (stays white in both themes — destructive bg).
- Read home/components/RecordingStatusPill.tsx (50 lines) — custom `<div>` (NOT `<output>`, intentionally — see comment: avoids triple-announcing transitions alongside Home's status line + App-level sr-only region). Colored dot (`h-2 w-2 rounded-full`, `backgroundColor={statusColor}` from `STATUS_COLORS` map) + uppercase label.
- Read home/components/LastTranscriptionPreview.tsx (73 lines) — custom container (`w-130 max-w-full rounded-[10px] bg-(--bg-subtle) px-4 py-3`). 2 actions: Undo + Re-paste — both shadcn `Button variant="ghost" size="sm"`. Comment: NO aria-live on this card (ancestor `<output aria-live="polite">` in Home.tsx is the single live region — second region would double-announce).
- Read home/lib/constants.ts (52 lines) — cache keys (`vt_home_recent_cache`, `vt_home_stats_cache`, `vt_first_recording_celebrated`), `FORCE_CANCEL_DELAY_MS=5000`, `LAST_TEXT_AUTO_CLEAR_MS=30000`, `STATUS_COLORS` (color-blind-safe palette aligned with tray icon — idle/recording/transcribing/loading/cancelling/error).
- Read home/lib/status.ts (53 lines) — `normalizeHotkey` (strip `<>` + uppercase), `statusLabelFor` (i18n key per state), `statusKeyFor` (state → key, error only surfaces with non-empty lastError).
- Read home/lib/cache.ts (99 lines) — `loadCachedRecent/Stats` + `persistRecent/Stats` (ref + localStorage; quota-exceeded non-fatal).
- Read home/hooks/useFirstRecordingCelebration.ts (71 lines) — `get_history({limit: 1})` check (was `get_today_stats count===1` — wrong: triggered on first dictation of ANY day, not lifetime first). LocalStorage flag persistence.
- Read Onboarding.tsx (584 lines — over E3 ≤~300 soft limit). Composition root: `useOnboardingWizard` + `usePermissionsProbe` + 7 step renderers + inline voice_biometric_consent gate on Done step. `progress = ((step+1)/total)*100` → hand-rolled progressbar (`role="progressbar"` + `aria-valuenow/min/max` + `aria-label`). `ConfirmDialog` (uses shadcn AlertDialog under the hood) for skip confirmation. Step gating: `isMicStepBlocked`, `isPermissionsBlocked`, `isConsentBlocked`, `isModelStepBlocked`.
- Read onboarding/components/WelcomeStep.tsx (85 lines) — 6-item ordered list + language picker (shadcn Select).
- Read onboarding/components/ConsentStep.tsx (126 lines) — 6 consent fields (voice_biometric / huggingface / cloud_openai / cloud_groq / cloud_deepgram / llm_polish). shadcn `Switch` per row + shadcn `Button` "Agree to All". Reuses `settings.privacy.*` i18n keys (single source of truth — wizard + Settings Privacy can't drift).
- Read onboarding/components/MicrophoneStep.tsx (131 lines) — shadcn Select for mic picker. "Default" badge + "BT" (Bluetooth/HFP) badge per option. No-mics branch: hint + Refresh button (shadcn Button variant="outline").
- Read onboarding/components/ModelStep.tsx (442 lines) — `role="radiogroup"` with two `<button role="radio">` (Local vs Cloud; biome-ignore lint/a11y/useSemanticElements comment justifies the pattern — native radio can't render card layout). Brand strip (FamilyLogo). Local: shadcn Select + HuggingFace consent checkbox (RAW `<input type="checkbox">`, NOT shadcn Checkbox — gap). Download progress: hand-rolled `role="progressbar"` (NOT shadcn Progress — not on disk). Cloud: shadcn Select + Input + RAW `<input type="checkbox">` for cloud consent (same gap).
- Read onboarding/components/PermissionsStep.tsx (152 lines) — shadcn Button. `<output role="alert">` for error state. `<output>` for needed-state with platform-specific instructions + `<pre>` for commands. Refresh + Test Hotkey buttons.
- Read onboarding/components/HotkeyStep.tsx (103 lines) — shadcn Select for hotkey presets. Optional inline test-hotkey affordance mirroring PermissionsStep.
- Read onboarding/components/DoneStep.tsx (76 lines) — pure text summary (backend / hotkey / model / mic). No primitives.
- Read onboarding/lib/constants.ts (44 lines) — `DONE_STEP_NAME="Done"`, `STEP_TITLE_KEY` map (raw backend enum → i18n key), `HOTKEY_DEFAULT` re-export from `@/components/hotkey/hotkey-utils`, `MODEL_DEFAULT="tiny"`, `TEST_HOTKEY_TIMEOUT_MS=10_000`, `HEADING_CLASS`, `ONBOARDING_MIC_TEST_DURATION_SEC=5`.
- Audited on-disk shadcn primitives: accordion, alert-dialog, button, checkbox, dialog, dropdown-menu, input, kbd, number-input-stepper, segmented-control, select, slider, sonner, switch, tooltip — confirmed (matches Sub-Agent A inventory). **MISSING primitives** confirmed: card.tsx, skeleton.tsx, progress.tsx, tabs.tsx, chart.tsx, radio-group.tsx, stepper.tsx (shadcn has no Stepper primitive at all).
- Audited chart-lib landscape: `package.json` has NO charting deps (no recharts/d3/visx/@nivo/chart.js/tremor/victory/plotly). node_modules confirms none installed. `index.css` already defines `--chart-1/2/3` (OKLCH) in :root + .dark (lines 125-127 + 174-176); all 13 theme presets (default, catppuccin, dracula, solarized, ayu, github, tokyo-night, amoled, nord, sepia, monokai, custom) define their own `--chart-1/2/3` palette. Note: shadcn Chart expects `--chart-1..5` (5 colors); Voice Typer only has 3.
- Audited other SVG usages in the renderer: HotkeyPicker.tsx (custom SVG path picker), Logo.tsx (brand mark), TitleBar.tsx (window controls), InfoTooltip.tsx (tooltip trigger), number-input-stepper.tsx (chevrons), ThemeSettingsSection.tsx (color swatches), StatsShareImage.tsx (inline mic glyph), BubbleMicButton/StopButton/DismissButton.tsx (button glyphs). NONE are charts; all are iconography/decoration.
- Audited chart-test surface: `Dashboard.test.tsx` (698 lines) asserts (via `fs.readFileSync` static-source checks + `setLocale` behavioral tests): BG-3 chart container role="img" + aria-label, bars are non-interactive `<div>` (no `<button>`), no per-bar tabIndex/aria-label, fill `bg-accent/90` (not /60), `analytics.activityChartAria` i18n key + `counts: ariaCounts` interpolation. BG-9 `formatDuration` shared via `@/lib/format` (no local copy). BG-10 `canShareStats` gating. DJ-93 `SHARE_IMAGE_CAPTURE_STYLE` hoisted constant. `dataPath`/`noDataDescription` i18n interpolation. `useDashboardData` `get_status` + `configDir`. `get_correction_usage`. Model state via `resolveActiveModel`. `tChoice` migration (binary plural → CLDR plural). Top stat card grid 4-card even division. Polish round (sublabel pruning, Activity icon stroke weight). `Dashboard-render-loop-guard.test.tsx` (78 lines) guards against the call-identity render-loop OOM. `streaks.test.ts` (327 lines) covers UTC parsing + period stats + zero-vs-missing distinction.
- Audited dashboard-component tests: `DashboardStatCard.test.tsx` (93 lines — renders icon/label/value, sublabel conditional, trend indicator aria-label, `min-h-24 mt-auto` layout). `QuickInfoCard.test.tsx` (76 lines — items-stretch + mt-auto + muted variant). `ShareStatsDialog.test.tsx` (373 lines — trigger button, opening, download/copy/save-as, social targets, toast actions, disabled state). `StatsShareImage.test.tsx` (133 lines — metric render, zero-data "—", palette theming, fallback palette, `APP_NAME` via i18n). `stats-share-image-memo.test.tsx` (123 lines — `React.memo` shallow-equal + `useMemo`-keyed stats).
- Audited bubble tests: `Bubble.test.tsx` (asserts 7 visualizer bars, mode transitions, idle empty div, aria-label, transcribing dots staggered animation, mic button visibility gating by config). `Bubble-axe.test.tsx` (axe-core a11y scan). `Bubble-transcript.test.tsx` (live partial transcript preview). `Bubble-keyboard-move.test.tsx` (dead-code guard — fails LOUDLY if `focusable: false` is removed without re-adding the keydown handler). `bubble_rAF_pause.test.tsx` (rAF gating on hidden). `bubble-raf-gating.test.tsx` (MutationObserver theme-switch debounce). `bubble-mid-flow-modes.test.tsx` (blocked/cancelling/permission_revoked/paste_failed mode transitions). `useAudioLevels-rAF-gating.test.tsx` + `useAudioLevels-reduced-motion.test.tsx` (+ `.ts` variant) (rAF + reduced-motion fallback). `bubble-fixes.test.tsx` (regression suite). `bubble-theme-tokens.test.tsx` (token-based colors). `useBubbleBridge.test.tsx` (bridge IPC single-listener pattern).
- Audited Home tests: `Home.test.tsx` (284 lines — mount, spinner, StatCards render, status line variants, navigation, recording state, hotkey chip). `Home-recording-flow-fixes.test.tsx` (842 lines — QV-9 lastText `<output>` aria-live, QV-96 LastTranscriptionPreview no aria-live (ancestor provides it), QV-16 MicToggleButton `aria-pressed`, QV-49 live MM:SS timer, dynamic status line variants, LAST_TEXT_AUTO_CLEAR_MS=30_000, no task-ID comments). `Home-render-loop-guard.test.tsx` (46 lines — same render-loop OOM guard).
- Audited Onboarding tests: `Onboarding.test.tsx` (1181 lines — F2 pre-select existing config, Permissions step at index 2, BG-11 progressbar aria-label, BG-12 localized step-name label, BG-14 DoneStep completeDescription interpolation, S5-CR-105 default-selection hints + Continue validation, Microphone no-mics block, Hotkey test-success). `Onboarding-render-loop-guard.test.tsx` (51 lines). `onboarding-fixes.test.tsx` (regression suite). `onboarding-model-step.test.tsx` (Model step local-vs-cloud + download). `ConsentStep.test.tsx` (6-consent toggles + Agree-to-All).
- Composed per-component decision matrix (KEEP / REPLACE / REFACTOR / REMOVE) with rationale per AGENTS.md W2 (real-constraint justification, not mechanical deferral).
- Wrote final report returned to orchestrator inline (chat).

Stage Summary:
- Audit partition complete; 0 files modified (investigation-only per scope).
- All findings cited with `file:line` references in the returned report.
- W0 satisfied: shadcn Chart API verified via web-search + page_reader against https://ui.shadcn.com/docs/components/chart. Confirmed Chart wraps **recharts** (not installed in Voice Typer — would be a NEW heavy dep ~95kb min+gzip for a single dashboard chart with zero interactive complexity).
- W2 satisfied: every KEEP-CUSTOM recommendation justified by a real Voice Typer constraint (no recharts dep, sandboxed bubble BrowserWindow with `focusable:false`, html-to-image capture target, custom trend indicator, zero-vs-missing chart distinction, bubble `no-drag` Electron requirement, custom a11y announcements via `<output aria-live>`, etc.).
- C-BRAND-1 noted: StatsShareImage already imports `APP_NAME` from `@/branding` (line 39 of StatsShareImage.tsx) + uses `t("stats.shareImage.exportedFrom", { appName: APP_NAME })`. No hardcoded "Voice Typer" in the partition.
- C-I18N-1/2 noted: every label/tooltip/aria-label resolves through `t()` / `tf()` / `tChoice()`. No raw English literals in production code (English fallbacks in `tf()` are documented as fallbacks for missing keys, not for actual user-facing strings).
- C-STYLE-1 noted: no task IDs / session prefixes in source code; only in this worklog entry.
- C-ARCH-1 noted: Dashboard.tsx (401), Home.tsx (864), Onboarding.tsx (584) — Home.tsx + Onboarding.tsx are over the ≤~300 line soft limit. Both already had one split pass (Home was 949, Onboarding was 884); a future split wave (extracting Home's recording timer + IPC subscriptions; extracting Onboarding's Done-step consent gate) is recommended but out of scope for THIS audit (which only flags).
- C-TEST-5 noted: every test lives in a separate `__tests__/` folder; no inline `#[cfg(test)]`-style blocks in production source.
- E12 noted: every recommendation preserves existing observable behavior (zero behavior-changing migrations proposed).
- E15 noted: no dead code observed in the partition — bubble's `Bubble-keyboard-move` test guards against the dead keydown handler being re-added.
- Test impact identified: a chart migration to shadcn Chart would force 7+ static-source assertions in `Dashboard.test.tsx` (BG-3 block, lines 106-160) to be rewritten (the `role="img"`/aria-label/no-button/per-bar-no-tabIndex/no-aria-label contract would change shape — recharts `<Bar>` is the bar element, not a `<div>`; `ChartTooltip` replaces `title=` per-bar tooltips). Skeleton primitive migration would touch DashboardSkeleton.tsx + every `animate-pulse` site (12+ files use the Tailwind utility). Progress primitive migration would touch Onboarding.tsx (line 309-324) + ModelStep.tsx (line 296-321). Checkbox primitive migration would touch Onboarding.tsx (line 443) + ModelStep.tsx (line 273) — 2 sites only.
- IPC contract: NO IPC impact from any recommendation in this audit. The bubble IPC subscriptions (`bubble:config` / `bubble:set-state` / `bubble:toggle-dictation` / `bubble:dismiss` / `bubble:move-by` / `bubble:hide-complete`) are renderer-only wiring decisions; the underlying `ALLOWED_COMMANDS` allowlist is untouched. The `_COMMAND_REGISTRY` Python server side is untouched. SEC-002 (`set_config` allowlist for `voice_biometric_consent` / `huggingface_consent` / cloud consents) untouched.
- Ready for handoff to orchestrator + implementation sub-agents.

Final Report (inline):

# Audit Report — Sub-Agent B (Dashboard + Chart + Bubble + Home + Onboarding)

## 1. Activity chart audit (`SevenDayActivityChart.tsx`)

- **Library used**: NONE. Fully custom div-based bar chart (`SevenDayActivityChart.tsx:39-177`). Pure HTML `<div>` + Tailwind classes + inline `style={{ height: pct% }}`. NOT SVG. NOT recharts. NOT d3. NOT visx.
- **Visual type**: bar chart, single-series. Y-axis with `[max, mid, 0]` tick labels (mid dropped when duplicating max — `SevenDayActivityChart.tsx:46-47`), gridlines as `border-t` divs (`:108`), bars with count label above (`:131-133`). Hourly (24 bars) for Today; daily (7/30/30 bars) for 7d/30d/all (`streaks.ts:466-497`). Zero-vs-missing visual distinction (`:140-148`): zero → `h-1 bg-border/50`; missing → `h-1 border-t border-dashed border-border/30`.
- **Interactions supported**: hover tooltip per bar via native `title=` attribute (`:135`, NOT a React tooltip). NO click, NO drilldown, NO legend toggle, NO zoom. Tooltip text is pluralized via `tChoice("analytics.dayCountTooltip", bar.count, { label })` (CLDR plural rules — `:122-124`). Missing-data tooltip uses `t("analytics.noDataBar", { label })` (`:121`).
- **Inputs / data shape**: props `{ range: RangeId; activity: ActivityChartData }` (`:25-28`). `ActivityChartData = { bars: ActivityBar[]; kind: "hourly"|"daily"; coveredFromKey: string|null; daySpan: number }` (`streaks.ts:451-458`). `ActivityBar = { key; label; count; isMissing }` (`streaks.ts:435-447`). Computed by `buildActivityBars(records, range, now)` in `streaks.ts:461-528`.
- **Responsive?**: PARTIAL. Width is responsive (`flex-1` bars fill the plot area). Height is FIXED at `h-36` (144px) for the plot area + `h-36` for the Y-axis (`:95`, `:111`). X-axis labels thinned via `tickEvery()` (every 3 hours for hourly, every 1/2/5 days for daily — `:31-37`).
- **Custom tooltip?**: NO. Uses native HTML `title=` attribute on each bar div (`:135`). shadcn `Tooltip` is on disk (`components/ui/tooltip.tsx`) but unused by the chart — by design (the bars are non-interactive `<div>` per BG-3 a11y contract — `Dashboard.test.tsx:129-152`).
- **Theme integration**: uses theme tokens exclusively. `bg-(--bg-subtle)` card (`:58`), `border-border/10` (`:58`), `text-(--text-muted)` axis (`:95`), `bg-accent/90 hover:bg-accent` bar fill (`:141`), `border-border/15` gridlines (`:108`), `text-(--text-primary)` title (`:76`), `text-(--text-muted)` subtitle (`:79`). NO hardcoded colors. Dark-theme inherits automatically via the theme tokens. `--chart-1/2/3` tokens are present in `index.css:125-127,174-176` AND every theme preset (`themes/*.ts`) — but the chart doesn't use them (single-series uses `--accent`); the `--chart-N` tokens exist for the future use case of a multi-series chart.
- **A11y**: container `<div role="img" aria-label={t("analytics.activityChartAria", { range, counts: ariaCounts })}>` (`:85-91`). `ariaCounts = bars.map(b => \`${b.label}: ${b.count}\`).join(", ")` (`:55`) — a single AT announcement. Bars carry NO `tabIndex`, NO `aria-label` (test asserts this — `Dashboard.test.tsx:139-152`). Gridline overlay is `aria-hidden="true"` (`:104`). The chart's accessible name is the descriptive range + per-bar counts.
- **Tests touching it**: `Dashboard.test.tsx:106-160` (BG-3 describe block — 5 assertions via `fs.readFileSync` on `SevenDayActivityChart.tsx` source): `role="img"` + aria-label, bars are non-`<button>` `<div>`, no per-bar tabIndex/aria-label, `bg-accent/90` (not /60). `Dashboard.test.tsx:439-483` (tChoice migration describe block — 4 assertions). `streaks.test.ts` (327 lines — `buildActivityBars` zero-vs-missing, hourly/daily, `coveredFromKey`). `Dashboard-render-loop-guard.test.tsx` (78 lines — page-level render-loop OOM guard via `renderLoopGuard` helper). NO behavioral render-and-click tests — the chart is non-interactive by design.
- **Other chart implementations**: searched the entire renderer source for `<svg`, `recharts`, `visx`, `d3`, `ChartContainer`, `ChartTooltip`, `ChartConfig`. NONE found. The only `<svg>` usages are: `HotkeyPicker.tsx` (custom hotkey picker), `StatsShareImage.tsx` (inline mic glyph for html-to-image capture), `Logo.tsx`, `TitleBar.tsx`, `InfoTooltip.tsx`, `number-input-stepper.tsx`, `ThemeSettingsSection.tsx`, plus the 3 bubble button glyphs (`BubbleMicButton`/`Stop`/`Dismiss`) + 4 model brand SVG assets (`qwen.svg`/`nvidia.svg`/`openai.svg`/`deepgram.svg`). NONE are data charts.
- **Recommendation**: **KEEP CUSTOM**. Rationale:
  1. **No charting library is installed today** (`package.json` confirms — recharts/d3/visx/@nivo/chart.js/tremor/victory/plotly all absent). Adopting shadcn Chart = adopting **recharts** as a NEW transitive dep (~95kb min+gzip + react/react-dom version-pinning constraints) for a single 177-line dashboard chart that has zero interactive complexity.
  2. The chart's bespoke **zero-vs-missing-data distinction** (future hours for "Today", sample-out-of-range days for "all") has no recharts equivalent — recharts would render both as zero-height bars; the product's intentional visual distinction (`dashed border-t bg-transparent` for missing) would be lost (E12 violation).
  3. The chart's a11y contract (single `role="img"` container with descriptive aria-label; bars are NON-interactive `<div>` with native `title=` tooltips) is documented + tested (`Dashboard.test.tsx:106-160`). recharts's default `<Bar>` is an interactive SVG rect with its own tab stop + chart-wide `accessibilityLayer` — a different AT model. Migration would force a rewrite of 9 test assertions + the AT announcement shape.
  4. The chart is theme-token-driven (`bg-accent/90`, `border-border/15`, `bg-(--bg-subtle)`) — it already inherits every theme preset correctly. shadcn Chart's `--chart-1..5` palette adds nothing for a single-series chart that uses `--accent`.
  5. The chart is **already factored cleanly** (177 LOC component + 528 LOC pure-helper `streaks.ts` + 72 LOC `format.ts` + 408 LOC data-hook `useDashboardData.ts` — the pre-split was a 732-LOC `Dashboard.tsx` monolith per the file's LOC history comment). Maintainability is already good.
  - **REFACTOR OPPORTUNITY** (not migration): if a second chart is ever needed (e.g. a models page accuracy chart, a microphone page SNR history chart), THEN revisit shadcn Chart — recharts's expressiveness would pay for itself across 2+ charts. For one chart, the custom impl is cheaper.

## 2. StatCards + DashboardStatCard + QuickInfoCard + ActivityList

### `StatCards.tsx` (Home page top-row, 99 LOC)
- **Current implementation**: 3-card horizontal flex strip (`StatCards.tsx:71-95`). Card = `<div className="rounded-lg bg-(--bg-subtle) px-4 py-3 flex-1 border border-border/10">` with `HugeiconsIcon` (h-4 w-4) + label + value. `React.memo` wrapped. Uses shared `formatCompactNumber` + `formatDuration` from `@/lib/format` (W2 satisfied — was a duplicated inline copy).
- **Candidate shadcn component**: shadcn `Card` / `CardHeader` / `CardContent`. **NOT on disk** (`components/ui/card.tsx` does not exist).
- **Why it doesn't fit**: shadcn Card's structure (`CardHeader` + `CardTitle` + `CardDescription` + `CardContent` + `CardFooter`) is overkill for a 1-line value card. The card deliberately has no header/title — the icon + label sit in a single inline row, the value directly below. Migrating would force wrapping the icon+label in `CardHeader` (adding a header element with extra padding) and the value in `CardContent` — adding DOM noise + breaking the existing `StatCards.stories.tsx` snapshot tests.
- **Behavior supported**: icon + label + formatted value; locale-aware compact number; i18n-driven label.
- **Behavior replacement supports**: same — but with more DOM nesting.
- **Compatibility gaps**: Storybook snapshot tests + the Home.test.tsx "renders StatCards when today's stats are provided via cache" test (line 84-99) would need snapshot updates.
- **A11y implications**: neutral — both use semantic `<div>` + text content; no heading role today.
- **Maintainability implications**: neutral-to-slightly-worse (extra abstraction for no behavioral gain).
- **Reusability implications**: StatCards is Home-specific; no other page uses the 3-card compact-strip layout (the Analytics page uses `DashboardStatCard`, which has a different shape — see below).
- **Regression risk**: medium (snapshot churn, label-vs-value DOM order could shift).
- **Recommendation**: **KEEP CUSTOM**. Adding shadcn Card would be a cosmetic refactor with zero behavior gain and ~6 test updates. The card layout is intentionally lightweight — the E3/E15-style "card primitive" abstraction would add a layer the codebase doesn't need.

### `DashboardStatCard.tsx` (Analytics top-row, 100 LOC)
- **Current implementation**: 4-card grid (`Dashboard.tsx:254-308`). Card = `<div className="flex min-h-24 flex-col gap-1.5 rounded-xl border border-border/10 bg-(--bg-subtle) p-3">` with icon + label row at top, value pushed down via `mt-auto`, optional `sublabel` + optional `TrendIndicator` (`:62-99`). Trend: ▲/▼/– glyph + pct + `role="img"` + localized aria-label; emerald (up) / destructive (down) / muted (flat).
- **Candidate shadcn component**: shadcn `Card` + `CardHeader` + `CardContent` + `CardFooter`. **NOT on disk**.
- **Why it doesn't fit**: the `mt-auto` value-push to the bottom of a `min-h-24` card is bespoke layout logic; shadcn Card's `CardHeader` (top-pinned) + `CardContent` (middle) + `CardFooter` (bottom-pinned) doesn't match — the value sits in `CardContent` per shadcn convention, but here the value is BOTTOM-pinned with the label TOP-pinned (the `mt-auto` creates a gap between them). The TrendIndicator's color-coded emerald/destructive/muted variants + the glyph (▲/▼/–) are bespoke — shadcn has no "trend indicator" primitive.
- **Behavior supported**: label + icon + value + optional sublabel + optional trend with localized aria-label.
- **Behavior replacement supports**: card structure only; trend indicator is custom either way.
- **Compatibility gaps**: `DashboardStatCard.test.tsx:81-92` asserts `flex min-h-24 flex-col` + `mt-auto` on the value — shadcn Card uses `grid` (not `flex flex-col`) and `CardContent` is a single block.
- **A11y implications**: TrendIndicator's `role="img"` + localized aria-label (`DashboardStatCard.tsx:36-46`) is preserved either way.
- **Maintainability implications**: migrating would require custom Card overrides for the `mt-auto` pattern — losing the maintainability win.
- **Reusability implications**: DashboardStatCard is Analytics-specific (the trend concept is unique to the period-vs-previous-period comparison).
- **Regression risk**: high (8+ test assertions on layout + 4 call sites in `Dashboard.tsx:268-305`).
- **Recommendation**: **KEEP CUSTOM**.

### `QuickInfoCard.tsx` (Analytics secondary row + Current Setup section, 66 LOC)
- **Current implementation**: `<div className="flex items-stretch gap-3 rounded-lg border border-border/10 bg-(--bg-subtle) p-3.5">` with `HugeiconsIcon` (h-5 w-5) + label + `mt-auto` value + optional sublabel + `muted` variant (`bg-(--bg-subtle)/50 p-3`). 6 call sites in `Dashboard.tsx:317-382` (3 secondary metrics + 3 Current Setup rows).
- **Candidate shadcn component**: shadcn `Card`. **NOT on disk**.
- **Why it doesn't fit**: same `mt-auto` value-push as `DashboardStatCard`. Plus the `muted` variant (smaller padding + 50% bg opacity) is bespoke — shadcn Card has no muted variant.
- **Recommendation**: **KEEP CUSTOM**.

### `ActivityList.tsx` (recent dictations list, 298 LOC)
- **Current implementation**: `<div className="rounded-lg border border-border/10 bg-(--bg-subtle) divide-y divide-border/10">` with `ActivityListRow` per item (`:269-282`). Row = `<div className="flex items-start gap-3 px-3.5 py-2.5">` with truncated text + timestamp + 3 action buttons (Copy / Star / Delete — all shadcn `Button variant="ghost" size="icon-xs"`). `React.memo` on the row + `useCallback` on parent handlers for shallow-equal re-render skipping. Empty-state inline muted message (`:225-249`). Shared between Home page + Dashboard page (Dashboard imports it but the current Dashboard doesn't render it — it lives in `components/dashboard/` for legacy reasons).
- **Candidate shadcn component**: shadcn `Card` for the row container, OR shadcn `List` (no such primitive — shadcn doesn't ship a List). The action buttons already use shadcn `Button`.
- **Why it fits/doesn't fit**: the row container is a flat list item with `divide-y` separators — shadcn Card's header/content/footer structure would force per-row DOM noise. The current pattern (outer `div` + `divide-y` + rows) is the idiomatic Tailwind list pattern.
- **Recommendation**: **KEEP CUSTOM**. Already uses shadcn `Button` for the interactive affordances. Consider **RELOCATION** (move from `components/dashboard/` to `components/feedback/` or `components/history/` since it's not dashboard-specific) — but that's an out-of-scope refactor.

## 3. ShareStatsDialog + StatsShareImage

### `ShareStatsDialog.tsx` (352 LOC)
- **Dialog primitive in use**: shadcn `Dialog` + `DialogContent` + `DialogHeader` + `DialogTitle` + `DialogDescription` + `DialogTrigger asChild` (`ShareStatsDialog.tsx:41-48`, `:225-350`). Correct usage with `open`/`onOpenChange` controlled state.
- **Share button**: shadcn `Button variant="outline" size="icon"` as `DialogTrigger asChild` (`:226-240`) — correct.
- **Action buttons**: 3 export actions (Download / Copy / Save As) + 4 social targets (WhatsApp / Telegram / X / Facebook) all use shadcn `Button variant="outline"` (`:292-345`). Correct.
- **Custom bits**:
  - `--preview-scale` CSS custom property written via callback ref + `ResizeObserver` (`:145-180`) — fits the off-screen 1200×630 export to the preview frame's width BEFORE first paint (no flash). This is product-specific; shadcn has no equivalent.
  - `SOCIAL_TARGETS` table (`:86-127`) — the 4 web-intent URL builders. Telegram's `t.me/share/url?url=<url>&text=<text>` REQUIRES the `url` param (without it Telegram redirects to telegram.org homepage — documented in `:102-111`).
  - `handleSocial` clipboard-fallback flow (`:207-222`) — desktop web intents are text/URL-only; the image is copied to the clipboard + the composer opens + the user is to toast'd to paste. Same fallback for all 4 platforms (no desktop-web path attaches the image directly).
  - `aspect-ratio: 1200/630` frame (`:267`) — the export's fixed social-share ratio.
- **Image rendering approach**: pure HTML/CSS rendered in a position:absolute off-screen container, captured by `html-to-image`'s `toPng()` (called from `useStatsShare.ts:1`). NOT canvas, NOT SVG, NOT server-side rendering.
- **Recommendation**: **KEEP CUSTOM**. Already uses shadcn Dialog + Button correctly. The custom preview-scale logic + social-target table + clipboard fallback are product-specific.

### `StatsShareImage.tsx` (338 LOC)
- **Implementation**: pure HTML/CSS 1200×630 div rendered off-screen (`StatsShareImage.tsx:68-82`). Inline `style={{...}}` props (NOT Tailwind classes — html-to-image needs computed styles from real CSS, but inline styles also work). Private `StatCard` sub-component (`:258-330`) — NOT shadcn Card. One inline `<svg>` mic glyph (`:95-116`) — bundled so html-to-image captures it cleanly (external image refs would fail CORS).
- **Theme integration**: ALL colors come from the `palette` prop (`StatsThemePalette` from `@/lib/theme-palette`). `legibleOn(palette.primary, palette.card, palette.foreground)` for accent legibility (WCAG 3:1 — `:57-63`). `mixHexColors(palette.card, palette.foreground, 0.06)` for card-surface lift (`:286-291`). `FALLBACK_THEME_PALETTE` constant for tests (`:51`). Zero hardcoded colors.
- **Branding**: `APP_NAME` from `@/branding` (`:39`, `:125`, `:249`). `t("stats.shareImage.exportedFrom", { appName: APP_NAME })` — i18n-driven with dynamic brand interpolation (C-BRAND-1 satisfied).
- **RTL**: `direction: isRtl ? "rtl" : "ltr"` (`:80`) — Arabic / Hebrew share cards flip layout.
- **Recommendation**: **KEEP CUSTOM**. html-to-image capture requires real DOM/CSS, not Radix Card shadow markup — adding shadcn Card would risk breaking the export. The `StatCard` private sub-component is intentionally lightweight (no header/footer structure).

## 4. TimeRangeSelector (37 LOC)

- **Implementation**: thin wrapper over shared `SegmentedControl variant="default" radius="pill"` (`TimeRangeSelector.tsx:23-35`). 4 ranges: today / 7d / 30d / all. Labels via `t(\`analytics.range.${r}\`)`. `ariaLabel={t("analytics.rangeAria")}`.
- **Recommendation**: **KEEP CUSTOM** (inherits Sub-Agent A's SegmentedControl decision — KEEP CUSTOM, used in 7 call sites). Nothing else worth flagging.

## 5. DashboardSkeleton (90 LOC)

- **Implementation**: pure Tailwind `animate-pulse` utility (`DashboardSkeleton.tsx:23,24,27,39,41,42,50,51,52,53,61,63,79,81,82`). Mirrors the loaded dashboard layout (heading + 4 stat cards + chart bars placeholder + quick-info row). `aria-label={t("analytics.loadingAria")}` + `aria-busy="true"` on the root (`:18-20`). Fixed bar heights via inline `style={{ height: \`${20 + ((i * 7) % 40)}px\` }}` (`:64`) so the skeleton chart looks varied.
- **Candidate shadcn component**: shadcn `Skeleton` (`<div className="animate-pulse rounded-md bg-muted" />`). **NOT on disk** (`components/ui/skeleton.tsx` does not exist).
- **Why it doesn't fit**: shadcn Skeleton is a 1-line primitive (`<div className="animate-pulse rounded-md bg-muted" />`). Voice Typer's DashboardSkeleton is a STRUCTURAL skeleton (mirrors the dashboard layout exactly) — the `animate-pulse` utility is already in use; the value of a `Skeleton` primitive would be ~0 (it'd save one className per skeleton block, at the cost of a new file + import).
- **Pulse vs shimmer vs spin**: PULSE (Tailwind `animate-pulse` — opacity fade 50% ↔ 100% over 2s). NO shimmer, NO spin. The MicToggleButton's `toggling` state uses SPIN (`MicToggleButton.tsx:85` `animate-spin` — the same spinner pattern shared via the `Spinner` component at `components/feedback/Spinner.tsx`).
- **Recommendation**: **KEEP CUSTOM**. If a future refactor wave adds a `skeleton.tsx` primitive for cross-component consistency (Settings + Microphone + Models + History pages also use `animate-pulse`), it'd be a 5-line file wrapping `<div className="animate-pulse rounded bg-(--bg-subtle)" />` — but that's a separate sweep across 12+ files using `animate-pulse` (per `rg animate-pulse`), out of scope for this audit.

## 6. Bubble audit

The bubble is a sandboxed Electron BrowserWindow with `focusable: false` (intentional — `main/windows/bubble-window.ts`, prevents stealing keyboard focus from the user's active text field). All bubble components are mouse-only in production; the `aria-label` + `title` props are populated so AT users navigating via screen-reader CURSOR (not keyboard) can still discover them.

### `BubbleMicButton.tsx` (84 LOC)
- **Current**: native `<button type="button">` with shared `BUBBLE_BUTTON_CLASS` (`:48-83`). Inline SVG mic (idle) / stop-square (recording) glyphs. `aria-label` + `title` populated. A11Y trade-off documented (`:11-31`).
- **Candidate shadcn component**: shadcn `Button`. **ON disk** (`components/ui/button.tsx`).
- **Why it doesn't fit**:
  1. `BUBBLE_BUTTON_CLASS` includes `no-drag` (constants.ts:86) — required so clicks bubble through the Electron `-webkit-app-region: drag` CSS region (the pill is a drag-region). shadcn Button doesn't know about `no-drag`.
  2. The 24px round button (`h-6 w-6`) with `ms-1` (margin-inline-start, RTL-safe) + `hover:bg-(--surface-hover)` is bespoke — shadcn Button's `size="icon"` defaults to `h-9 w-9` (36px) — wrong size for the bubble pill.
  3. The button is mouse-only (`focusable: false` window) — shadcn Button's focus-ring logic + keyboard activation is irrelevant.
  4. The SVG glyphs are bundled inline (NOT hugeicons) — bubble window's preload is minimal (SEC-026 sandbox); importing `@hugeicons/react` would pull a heavier bundle into a sandboxed context.
- **Recommendation**: **KEEP CUSTOM**. The bubble's product constraints (no-drag, sandboxed preload, mouse-only, 24px size, inline SVG) make shadcn Button a poor fit.

### `BubbleStopButton.tsx` (84 LOC)
- **Current**: same pattern as BubbleMicButton. Stop (filled square) + retry (circular arrow) SVGs. `tf()` translation-with-fallback.
- **Recommendation**: **KEEP CUSTOM** (same rationale as BubbleMicButton).

### `BubbleDismissButton.tsx` (52 LOC)
- **Current**: same pattern. '×' SVG. `t("bubble.dismissAria")`.
- **Recommendation**: **KEEP CUSTOM** (same rationale).

### `BubbleVisualizer.tsx` (81 LOC)
- **Current**: 7-bar spectrum visualizer (`BubbleVisualizer.tsx:42-80`). REC indicator (`bg-destructive animate-pulse`) + label (`tf("bubble.recordingLabel", "REC")`) + 7 `<span>` bars (`w-0.75 rounded-full bg-(--text-primary)`, `MIN_HEIGHT=5`). Bars are animated by `useAudioLevels`'s rAF loop via `dotRefs` (direct DOM height/opacity writes, no React re-render). `refSetters` memoised once per `dotRefs` instance (`:32-41`). `DOT_INDICES` hoisted module constant.
- **Candidate shadcn component**: NONE. shadcn has no audio-level visualizer primitive.
- **Recommendation**: **KEEP CUSTOM**. There is no shadcn equivalent. The 7-bar spectrum + rAF direct-DOM writes + `prefers-reduced-motion` fallback + dynamic `onLevel` IPC gating are deeply product-specific.

### `BubbleModeContent.tsx` (270 LOC)
- **Current**: 8-way mode switch (`:100-269`). transcribing (live `<output>` transcript preview + 3 staggered dots), fading (opacity 0 + translateY transition), idle (empty div + sr-only announcement), error (red dot + label + optional message), blocked (⊘), cancelling (⏇ pulse), permission_revoked, paste_failed, recording default (`<BubbleVisualizer>`).
- **Candidate shadcn component**: NONE (no shadcn mode-status primitive).
- **Recommendation**: **KEEP CUSTOM**.

### Bubble hooks (`useAudioLevels`, `useBubbleBridge`, `useBubbleLifecycle`, `useBubbleStateMachine`, `useThemeSync`)
- All 5 hooks are deeply product-specific (rAF loop with visibility + recording gates + reduced-motion fallback + dynamic IPC gating + `MutationObserver` for theme switches + centralised bridge for single-listener-per-event). NO shadcn equivalent exists.
- **Recommendation**: **KEEP CUSTOM** for all 5.

### `Bubble.tsx` (375 LOC)
- **Current**: `<output aria-live="polite" aria-atomic="true" aria-label={getBubbleAriaLabel(mode, errorMessage)}>` wrapper (`:322-372`). `<BubbleBridgeProvider>` wraps `<BubbleInner>`. `pillRef` + `useLayoutEffect` for BrowserWindow auto-resize. `draggable`/`micButton`/`dismissable`/`bubbleBehavior` state from `bubble:config` IPC. Error-mode auto-hide (7s) for `show_on_record`. Dead keyboard-move handler intentionally REMOVED (`:73-98` — documented product decision: mouse-drag-only).
- **Recommendation**: **KEEP CUSTOM**. The `<output aria-live>` wrapper + the state-aware aria-label + the BrowserWindow auto-resize are product-specific.

## 7. Home page audit

### `RecordingStatusPill.tsx` (50 LOC)
- **Current**: `<div className="flex items-center gap-2 animate-fade-in">` with colored dot (`h-2 w-2 rounded-full`, `backgroundColor={statusColor}` from `STATUS_COLORS`) + uppercase label. Custom — NOT a live region (intentional — see comment `:8-18`: ancestor `<output>` in Home.tsx + App-level sr-only region cover announcements; a live pill would triple-announce).
- **Candidate shadcn component**: NONE. shadcn has no "status pill" / "indicator" primitive.
- **Recommendation**: **KEEP CUSTOM**. `React.memo` wrapped.

### `MicToggleButton.tsx` (93 LOC)
- **Current**: native `<button type="button">` (NOT shadcn Button). 84px round (`h-21 w-21`), `bg-destructive animate-glow-pulse` idle, `bg-foreground/15` recording. Pulse-ring overlay. Spinner overlay (`border-2 border-white/80 border-t-transparent animate-spin`) while `toggling`. `aria-pressed={isRecording}`. `disabled` + `disabledReason` for aria-label substitution.
- **Candidate shadcn component**: shadcn `Button`. **ON disk**.
- **Why it doesn't fit**:
  1. 84px round button with `bg-destructive` red + `animate-glow-pulse` (custom keyframe in `index.css`) + `press-scale` (custom utility) + pulse-ring overlay — none of these match shadcn Button's variants (default/outline/ghost/destructive/secondary/link).
  2. Adding a custom `variant="mic"` to shadcn Button would still require overriding the size, the animations, and the spinner overlay — effectively a from-scratch button with shadcn Button as the base.
  3. The `disabledReason` aria-label substitution logic (`:45`) is bespoke.
- **Recommendation**: **KEEP CUSTOM**. `React.memo` wrapped. The visual treatment is unique to the Home page's hero CTA.

### `LastTranscriptionPreview.tsx` (73 LOC)
- **Current**: custom container (`w-130 max-w-full rounded-[10px] bg-(--bg-subtle) px-4 py-3`) with truncated text + Undo + Re-paste buttons. Buttons ARE shadcn `Button variant="ghost" size="sm"` (`:36, :52`). NO aria-live on the card (intentional — see comment `:27-30`: ancestor `<output aria-live="polite">` in Home.tsx is the single live region).
- **Recommendation**: **KEEP CUSTOM** (the container) + already uses shadcn Button (the actions). `React.memo` wrapped.

### `ActivityList.tsx`
- Already covered in §2 (shared between Home + Dashboard).

### Other Home primitives
- `Spinner` (`components/feedback/Spinner.tsx`, 100 LOC) — custom but already project-shared (replaces 9 duplicated spinners per the file's docstring). NOT shadcn (no shadcn spinner primitive).
- `EmptyState` (`components/feedback/EmptyState.tsx`, 117 LOC) — custom; uses shadcn `Button` for the action. NOT shadcn (shadcn has an EmptyState primitive only in newer versions, not in this project).
- `HotkeyChips` (`components/hotkey/HotkeyChips.tsx`) — custom hotkey display; uses shadcn `Kbd` primitive under the hood.
- `LastUpdatedIndicator` — custom (no shadcn equivalent).
- `KeyboardPermissionBanner` — custom (no shadcn equivalent; conditionally rendered, platform-aware).
- `OfflinePackPreparingBanner` — custom.
- **Recommendation for all**: **KEEP CUSTOM**. No shadcn equivalents exist; all use shadcn Button where applicable.

## 8. Onboarding audit

### Stepper implementation
- **Current**: NONE. There is NO dedicated Stepper component in the Onboarding wizard. The "stepper" is the inline progressbar at `Onboarding.tsx:309-324` (`<div role="progressbar" aria-valuenow={step.step + 1} aria-valuemin={1} aria-valuemax={step.total_steps} aria-label={t("onboarding.progressAria", {current, total})}>` with an inner `<div className="h-1.5 rounded-full bg-accent transition-all duration-300" style={{ width: \`${progress}%\` }} />`). The step list itself is the backend's `STEP_NAMES` array (`Onboarding.test.tsx:101-108`) — the renderer just renders whichever step the backend says is current (`Onboarding.tsx:353-424` switch).
- **shadcn Stepper**: **DOES NOT EXIST** as a dedicated primitive in shadcn/ui (confirmed via W0 web search — shadcn's docs only mention "Stepper" as an example use case composed from primitives, not as a shipped component). There are community steppers (e.g. `shadcn-ui-stepper` npm package) but none in the official registry.
- **Recommendation**: **KEEP CUSTOM** (no shadcn Stepper primitive to migrate to). The current inline progressbar + backend-driven step switching is correct and minimal.

### Step components

#### `WelcomeStep.tsx` (85 LOC)
- 6-item ordered list + language picker (shadcn `Select` + `SelectTrigger` + `SelectContent` + `SelectItem`). Uses `getLocaleLabel` + `SUPPORTED_LOCALES` from `@/i18n/i18n`.
- **Recommendation**: **KEEP CUSTOM** layout (the language picker already uses shadcn Select).

#### `ConsentStep.tsx` (126 LOC)
- 6 consent rows (`voice_biometric` / `huggingface` / `cloud_openai` / `cloud_groq` / `cloud_deepgram` / `llm_polish`). Each row uses shadcn `Switch` (`:115-119`). "Agree to All" button uses shadcn `Button variant="default" size="sm"` (`:92-100`). Reuses `settings.privacy.*` i18n keys (single source of truth — wizard + Settings Privacy can't drift).
- **Recommendation**: **KEEP CUSTOM** (already uses shadcn Switch + Button correctly).

#### `MicrophoneStep.tsx` (131 LOC)
- shadcn `Select` for mic picker (`:44-95`). Per-option badges: "Default" + "BT" (Bluetooth/HFP). No-mics branch: hint + Refresh button (shadcn `Button variant="outline"`).
- **Recommendation**: **KEEP CUSTOM**.

#### `ModelStep.tsx` (442 LOC)
- `role="radiogroup"` with two `<button role="radio">` (Local vs Cloud — `:161-191`). biome-ignore `lint/a11y/useSemanticElements` comment justifies the pattern: a native `<input type="radio">` cannot render the card layout; `role="radio"` + `aria-checked` in a `radiogroup` is the correct ARIA pattern.
- shadcn `Select` for model picker. shadcn `Input` for cloud API key. shadcn `Button` for download.
- **GAPS**:
  - HuggingFace consent checkbox at `:273-280` uses RAW `<input type="checkbox">` instead of shadcn `Checkbox` (which IS on disk at `components/ui/checkbox.tsx`).
  - Cloud consent checkbox (further down the file) uses the same raw pattern.
  - Download progress bar at `:296-321` uses RAW `<div role="progressbar">` instead of shadcn `Progress` (NOT on disk — would need to be added).
- **Recommendation**:
  - **REFACTOR**: HuggingFace + Cloud consent checkboxes → use shadcn `Checkbox` (already installed, gives consistent focus-ring + Radix's `indeterminate` support + a11y-tested checked state). 2 sites only. Low regression risk.
  - **KEEP CUSTOM** for the radio-group cards (shadcn RadioGroup is NOT on disk; the custom `<button role="radio">` is the correct ARIA pattern).
  - **KEEP CUSTOM** for the download progress bar (shadcn Progress NOT on disk; the inline `role="progressbar"` with proper aria is correct). IF a future sweep adds shadcn Progress to disk, this + the Onboarding.tsx progress bar (line 309-324) + the Settings PrewarmAndUpdates progress bar would all be REFACTOR candidates in one wave.

#### `PermissionsStep.tsx` (152 LOC)
- shadcn `Button` (Refresh + Test Hotkey). `<output role="alert">` for error state (`:65-72`). `<output>` for needed-state with platform-specific instructions (`:77-97`). `<pre>` for commands.
- **Recommendation**: **KEEP CUSTOM**.

#### `HotkeyStep.tsx` (103 LOC)
- shadcn `Select` for hotkey presets. Optional inline test-hotkey affordance (shadcn `Button variant="outline"`) mirroring PermissionsStep.
- **Recommendation**: **KEEP CUSTOM**.

#### `DoneStep.tsx` (76 LOC)
- Pure text summary (backend / hotkey / model / mic). No primitives.
- **Recommendation**: **KEEP CUSTOM**.

### Onboarding-level custom bits

- **Progress bar** (`Onboarding.tsx:309-324`): raw `<div role="progressbar">`. shadcn Progress NOT on disk. **KEEP CUSTOM** (same ModelStep recommendation).
- **Consent checkbox on Done step** (`Onboarding.tsx:443-451`): raw `<input type="checkbox">`. **REFACTOR** with shadcn `Checkbox` (same ModelStep recommendation).
- **Skip confirmation dialog** (`Onboarding.tsx:570-581`): uses `ConfirmDialog` from `@/components/common/ConfirmDialog.tsx` — already wraps shadcn `AlertDialog` + `AlertDialogAction` + `AlertDialogCancel` + `AlertDialogContent` + `AlertDialogDescription` + `AlertDialogFooter` + `AlertDialogHeader` + `AlertDialogTitle` (verified at `ConfirmDialog.tsx:1-12`). **KEEP CUSTOM** (correct abstraction).

## 9. Component decisions summary

| Component | File | Decision | Rationale (one line) |
|---|---|---|---|
| SevenDayActivityChart | `pages/dashboard/components/SevenDayActivityChart.tsx:39` | **KEEP CUSTOM** | recharts NOT installed; chart is 177 LOC, theme-token-driven, has bespoke zero-vs-missing distinction + non-interactive a11y contract tested in 9 assertions |
| StatCards | `components/dashboard/StatCards.tsx:69` | **KEEP CUSTOM** | shadcn Card NOT on disk; 3-card strip is intentionally lightweight (icon + label + value, no header/footer); migrating would add DOM noise + break Storybook snapshot |
| DashboardStatCard | `components/dashboard/DashboardStatCard.tsx:55` | **KEEP CUSTOM** | bespoke `min-h-24 mt-auto` value-push + TrendIndicator (▲▼/– with emerald/destructive/muted variants); shadcn Card structure doesn't match |
| QuickInfoCard | `components/dashboard/QuickInfoCard.tsx:24` | **KEEP CUSTOM** | same `mt-auto` pattern + bespoke `muted` variant; 6 call sites |
| ActivityList | `components/dashboard/ActivityList.tsx:179` | **KEEP CUSTOM** | list (not card grid); actions already use shadcn `Button variant="ghost" size="icon-xs"`; consider future RELOCATION out of `components/dashboard/` since it's shared with Home |
| ShareStatsDialog | `components/dashboard/ShareStatsDialog.tsx:129` | **KEEP CUSTOM** | already uses shadcn Dialog + Button correctly; custom `--preview-scale` + `SOCIAL_TARGETS` + clipboard fallback are product-specific |
| StatsShareImage | `components/dashboard/StatsShareImage.tsx:49` | **KEEP CUSTOM** | html-to-image capture requires real DOM/CSS; private `StatCard` is intentionally lightweight; palette-driven (zero hardcoded colors); C-BRAND-1 satisfied |
| TimeRangeSelector | `pages/dashboard/components/TimeRangeSelector.tsx:21` | **KEEP CUSTOM** | inherits Sub-Agent A's SegmentedControl decision |
| DashboardSkeleton | `pages/dashboard/components/DashboardSkeleton.tsx:14` | **KEEP CUSTOM** | shadcn Skeleton NOT on disk; `animate-pulse` is a Tailwind utility, not a primitive; layout mirrors the loaded dashboard exactly |
| Bubble | `Bubble.tsx:100` | **KEEP CUSTOM** | `<output aria-live>` wrapper + BrowserWindow auto-resize + state-aware aria-label; sandboxed Electron window |
| BubbleMicButton | `bubble/BubbleMicButton.tsx:36` | **KEEP CUSTOM** | `no-drag` Electron drag-region requirement + sandboxed preload (SEC-026) + mouse-only (`focusable:false`) + 24px round + inline SVG |
| BubbleStopButton | `bubble/BubbleStopButton.tsx:30` | **KEEP CUSTOM** | same rationale as BubbleMicButton |
| BubbleDismissButton | `bubble/BubbleDismissButton.tsx:24` | **KEEP CUSTOM** | same rationale as BubbleMicButton |
| BubbleVisualizer | `bubble/BubbleVisualizer.tsx:22` | **KEEP CUSTOM** | 7-bar audio spectrum + rAF direct-DOM writes + reduced-motion fallback; NO shadcn equivalent exists |
| BubbleModeContent | `bubble/BubbleModeContent.tsx:94` | **KEEP CUSTOM** | 8-way mode switch + `<output>` transcript preview; NO shadcn equivalent |
| Bubble hooks (5) | `bubble/use{AudioLevels,BubbleBridge,BubbleLifecycle,BubbleStateMachine,ThemeSync}.ts` | **KEEP CUSTOM** | deeply product-specific (rAF + visibility gates + dynamic IPC gating + centralised bridge) |
| RecordingStatusPill | `pages/home/components/RecordingStatusPill.tsx:25` | **KEEP CUSTOM** | shadcn has no status-pill primitive; explicitly NOT a live region (ancestor `<output>` covers announcements) |
| MicToggleButton | `pages/home/components/MicToggleButton.tsx:32` | **KEEP CUSTOM** | 84px round + `bg-destructive animate-glow-pulse` + pulse-ring + spinner overlay + `aria-pressed`; shadcn Button variants don't fit |
| LastTranscriptionPreview | `pages/home/components/LastTranscriptionPreview.tsx:21` | **KEEP CUSTOM** (container) | already uses shadcn `Button` for Undo/Re-paste actions |
| Onboarding Stepper | `pages/Onboarding.tsx:309` | **KEEP CUSTOM** | shadcn has NO Stepper primitive; inline `role="progressbar"` + backend-driven step switching is correct |
| Onboarding Progress bar | `pages/Onboarding.tsx:309-324` | **KEEP CUSTOM** (for now) | shadcn Progress NOT on disk; inline `role="progressbar"` with proper aria is correct. IF shadcn Progress is added later, REFACTOR in a sweep wave (3 sites: Onboarding + ModelStep + PrewarmAndUpdates) |
| Onboarding HF/Cloud consent checkbox | `pages/onboarding/components/ModelStep.tsx:273` + `pages/Onboarding.tsx:443` | **REFACTOR** | shadcn Checkbox IS on disk; 2 sites; low regression risk; gains focus-ring + Radix `indeterminate` support |
| Onboarding Model radio-group | `pages/onboarding/components/ModelStep.tsx:161` | **KEEP CUSTOM** | shadcn RadioGroup NOT on disk; custom `<button role="radio">` is the correct ARIA pattern (biome-ignore comment justifies) |
| WelcomeStep | `pages/onboarding/components/WelcomeStep.tsx:31` | **KEEP CUSTOM** | already uses shadcn Select for language picker |
| ConsentStep | `pages/onboarding/components/ConsentStep.tsx:69` | **KEEP CUSTOM** | already uses shadcn Switch + Button |
| MicrophoneStep | `pages/onboarding/components/MicrophoneStep.tsx:28` | **KEEP CUSTOM** | already uses shadcn Select + Button |
| PermissionsStep | `pages/onboarding/components/PermissionsStep.tsx:18` | **KEEP CUSTOM** | already uses shadcn Button; `<output role="alert">` for errors is correct |
| HotkeyStep | `pages/onboarding/components/HotkeyStep.tsx:37` | **KEEP CUSTOM** | already uses shadcn Select + Button |
| DoneStep | `pages/onboarding/components/DoneStep.tsx:25` | **KEEP CUSTOM** | pure text summary; no primitives |

**Summary**: 0 REPLACE, 1 REFACTOR (shadcn Checkbox for 2 onboarding consent sites), 0 REMOVE, 0 REFACTOR-CUSTOM-USING-EXISTING-PRIMITIVE beyond the checkbox, 30+ KEEP CUSTOM.

## 10. Risks & gotchas

### AGENTS.md constraints touching this scope
- **E12 (never downgrade behavior)**: ANY future chart migration to shadcn Chart would downgrade the chart's bespoke zero-vs-missing-data visual distinction (recharts has no equivalent) AND change the a11y model (single `role="img"` container + non-interactive bars → interactive SVG rects with `accessibilityLayer`). E12 explicitly forbids this.
- **E15 (technical debt)**: the partition has 0 dead code (bubble's dead keyboard-move handler was already REMOVED with a loud guard test `Bubble-keyboard-move.test.tsx` that fails if `focusable:false` is removed without re-adding the handler). No removals recommended.
- **E16 (big-task policy)**: Home.tsx (864 LOC) + Onboarding.tsx (584 LOC) are over the E3 ≤~300 soft limit. Both already had one split pass (Home was 949; Onboarding was 884). A future split wave is recommended (extracting Home's recording-timer + IPC subscriptions; extracting Onboarding's Done-step consent gate) but is OUT OF SCOPE for this audit (which flags, doesn't fix).
- **W0 (web-search first)**: satisfied — shadcn Chart API verified via z-ai page_reader against `https://ui.shadcn.com/docs/components/chart` BEFORE the KEEP-CUSTOM recommendation.
- **W2 (prefer existing libraries)**: the chart is the only candidate where W2 could argue for recharts. The justification for KEEP-CUSTOM is documented (no recharts dep today; bespoke zero-vs-missing distinction; non-interactive a11y contract tested in 9 assertions; single chart at 177 LOC doesn't justify a ~95kb min+gzip new dep).
- **C-BRAND-1**: StatsShareImage.tsx already imports `APP_NAME` from `@/branding` + uses `t("stats.shareImage.exportedFrom", { appName: APP_NAME })`. No hardcoded "Voice Typer" in the partition. Any future refactor MUST preserve this.
- **C-I18N-1/2**: every label/tooltip/aria-label resolves through `t()` / `tf()` / `tChoice()`. The `tf(key, fallback)` helper (bubble/helpers.ts:15-18) is a documented fallback for missing keys — the English literals in `tf()` calls are FALLBACK STRINGS, not user-facing literals. Any new user-facing text added by a refactor MUST be added to all 8 locale files (en/ar/de/es/fr/hi/ru/zh).
- **C-STYLE-1**: no task IDs / session prefixes in source code; only in this worklog entry.
- **C-TEST-5**: every test lives in a separate `__tests__/` folder; no inline `#[cfg(test)]`-style blocks in production source.

### Tests that will need updating if migrations proceed
- **REFACTOR wave 1 — Onboarding consent checkboxes → shadcn Checkbox** (2 sites):
  - `Onboarding.test.tsx` — the test currently mocks `@/components/ui/select` but NOT `@/components/ui/checkbox`. The Radix Checkbox requires `pointerDown` + `pointerUp` simulation in jsdom (similar to the Radix Select stub at `Onboarding.test.tsx:59-97`). The test will need a parallel `vi.mock("@/components/ui/checkbox", ...)`.
  - `onboarding-model-step.test.tsx` — same.
  - `onboarding-fixes.test.tsx` — if it asserts on the raw `<input type="checkbox">` selector.
  - Test risk: LOW (2 sites + 1 mock addition).
- **HYPOTHETICAL wave 2 — Dashboard chart → shadcn Chart** (NOT RECOMMENDED per this audit, but if forced):
  - `Dashboard.test.tsx:106-160` (BG-3 describe block — 5 static-source assertions on `role="img"`, aria-label, no `<button>`, no per-bar tabIndex/aria-label, `bg-accent/90` fill). ALL 5 would need rewriting — recharts `<Bar>` is an SVG rect, not a `<div>`; `ChartTooltip` replaces `title=` per-bar tooltips; the `role="img"` container would change to recharts's `accessibilityLayer` model.
  - `Dashboard.test.tsx:439-483` (tChoice migration block — 4 assertions). The `tChoice("analytics.dayCountTooltip", bar.count, {label})` call would need to be re-wired to `ChartTooltipContent`'s `labelKey`/`nameKey` props.
  - `streaks.test.ts` (327 lines — `buildActivityBars` zero-vs-missing distinction). The `isMissing` flag would need a recharts equivalent (likely a custom `<Bar>` with a dashed fill pattern — recharts has no first-class "missing data" concept for categorical bars).
  - `Dashboard-render-loop-guard.test.tsx` (78 lines — page-level render-loop OOM guard). recharts's React tree is heavier; the render-count threshold may need bumping.
  - Test risk: HIGH (9+ assertions rewritten + a11y model change + zero-vs-missing distinction lost). This is the primary reason the audit recommends KEEP CUSTOM.
- **HYPOTHETICAL wave 3 — Skeleton primitive** (NOT RECOMMENDED for this audit):
  - `DashboardSkeleton.tsx` + 12+ files using `animate-pulse` (per `rg animate-pulse`): `SettingsSkeleton.tsx`, `MicToggleButton.tsx`, `RecordingStatusPill.tsx`, `BubbleVisualizer.tsx`, `BubbleModeContent.tsx`, `App.tsx`, `ActiveMicrophoneCard.tsx`, `HotkeyPicker.tsx`, `ModelCardActions.tsx`, `CloudProvidersPanel.tsx`, `ModelStep.tsx`, `LiveQualityFeedback.tsx`.
  - Test risk: LOW (skeleton primitive would be a 5-line file wrapping `animate-pulse`).

### IPC contract
- NO IPC impact from any recommendation in this audit.
- The bubble IPC subscriptions (`bubble:config` / `bubble:set-state` / `bubble:toggle-dictation` / `bubble:dismiss` / `bubble:move-by` / `bubble:hide-complete`) are renderer-only wiring decisions.
- The underlying `ALLOWED_COMMANDS` allowlist (`src/main/allowed-commands.ts`) is untouched.
- The `_COMMAND_REGISTRY` Python server side (`voice_typer/server/ipc/registry.py`) is untouched.
- SEC-002 (`set_config` allowlist for `voice_biometric_consent` / `huggingface_consent` / cloud consents) untouched.
- SEC-026 (sandboxed bubble preload) untouched — bubble button KEEP-CUSTOM recommendation preserves the sandboxed-preload constraint (no hugeicons import into the bubble window).

### Bundle-size implication (informational)
- Adopting shadcn Chart (= recharts) for a single chart would add ~95kb min+gzip to the renderer bundle. The current div-based chart is ~3kb. The trade-off is unjustified for ONE chart with zero interactive complexity. If a second chart is ever needed (e.g. a Models page accuracy chart, a Microphone page SNR history chart), the trade-off changes — revisit then.

### Next actions for the orchestrator
1. **Accept** the KEEP-CUSTOM recommendations for the chart, StatCards, DashboardStatCard, QuickInfoCard, ActivityList, ShareStatsDialog, StatsShareImage, TimeRangeSelector, DashboardSkeleton, all bubble components, RecordingStatusPill, MicToggleButton, LastTranscriptionPreview, Onboarding Stepper + Progress bar + radio-group, and all step components.
2. **Approve** the single REFACTOR recommendation: replace the 2 raw `<input type="checkbox">` consent checkboxes (Onboarding.tsx:443 + ModelStep.tsx:273) with shadcn `Checkbox`. Low risk, gains focus-ring + Radix `indeterminate` support. Requires adding a `vi.mock("@/components/ui/checkbox", ...)` to `Onboarding.test.tsx` (parallel to the existing select mock at line 59).
3. **Note for future wave** (out of scope): if a cross-component `skeleton.tsx` primitive sweep is launched, it would touch 12+ files using `animate-pulse` — recommend deferring until a coordinated refactor wave.
4. **Note for future wave** (out of scope): Home.tsx (864 LOC) + Onboarding.tsx (584 LOC) exceed the E3 ≤~300 line soft limit. Both already had one split pass. A second split wave (extracting Home's recording-timer + IPC subscriptions into a `useHomeRecording` hook; extracting Onboarding's Done-step consent gate into a `DoneStepConsent` sub-component) is recommended but out of scope for THIS audit.
5. **Ready for handoff** to implementation sub-agents.

---
Task ID: 3-C
Agent: investigation-C (UI primitives + common + feedback + audio + hotkey + consent + models + microphone)
Task: Read-only audit of the UI-primitives + common-components + feedback + audio + hotkey + consent + help + KeyboardPermissionBanner + models + microphone partition per the orchestrator's file-disjoint split. Produce a structured Markdown report covering (1) on-disk UI primitives inventory, (2) Input audit + raw `<input>` sites, (3) Slider vs RangeSlider, (4) Kbd vs KeyboardPermissionBanner, (5) Modal vs Dialog, (6) ConfirmDialog vs AlertDialog, (7) Tooltip vs InfoTooltip vs HotkeyTooltip, (8) Spinner vs Skeleton landscape, (9) EmptyState, (10) AudioFilterChain + FilterRow, (11) HotkeyPicker + helpers, (12) Models components, (13) Microphone components, (14) component decisions summary, (15) risks + gotchas + file-disjoint implementation-wave grouping.

Work Log:
- Read full AGENTS.md (964 lines) — binding rules E1/E6/E10/E12/E14/E15/E16/E18/W0/W2/C-BRAND-1/C-I18N-1/2/C-STYLE-1/C-ARCH-1/C-TEST-1..5/C-LOG-1/2 acknowledged.
- Read worklog.md (451 lines) for orchestrator + Sub-Agent A + Sub-Agent B context: shadcn primitives inventory (15 on disk), components.json `radix-luma`/zinc/hugeicons config, vitest threads pool, locale files (en/ar/de/es/fr/hi/ru/zh), Sub-Agent A's KEEP-CUSTOM decision for Sidebar/SegmentedControl/SearchField, Sub-Agent B's KEEP-CUSTOM decisions for chart/StatCards/DashboardSkeleton/Bubble/Onboarding-stepper + single REFACTOR for Onboarding raw checkboxes (3 sites: Onboarding.tsx:443, ModelStep.tsx:273, ModelStep.tsx:409).
- W0 web-verified shadcn Input reference at https://ui.shadcn.com/docs/components/base/input — confirmed Voice Typer's input.tsx matches the standard shadcn radix-luma pattern (bare `<input>` + `data-slot="input"` + `cn()` merge + `aria-invalid:*` variants).
- Inventoried all 15 on-disk UI primitives: accordion, alert-dialog, button, checkbox, dialog, dropdown-menu, input, kbd, number-input-stepper, segmented-control, select, slider, sonner, switch, tooltip. Confirmed NONE of card/skeleton/progress/tabs/chart/radio-group/stepper are on disk (matches Sub-Agent B's finding).
- Audited each primitive for shadcn-standard vs project-specific extensions:
  - accordion.tsx (95 LOC): radix-luma + custom rounded-2xl border + ArrowDown01/ArrowUp01 chevron swap.
  - alert-dialog.tsx (207 LOC): radix-luma + AlertDialogAction/Cancel route through Button's className (tailwind-merge conflict resolution) + AlertDialogMedia slot + intentional onInteractOutside omission.
  - button.tsx (93 LOC): radix-luma + project-specific `warning` variant (amber-tinted, `--warning` token) + 9 sizes (default/xs/sm/lg/icon/icon-xs/icon-sm/icon-lg) + dev-mode a11y warn.
  - checkbox.tsx (58 LOC): radix-luma + Tick02Icon/LineIcon hugeicons glyphs + `data-[state=checked/indeterminate]` (NOT `data-checked:` which never matched Radix's data-state).
  - dialog.tsx (196 LOC): radix-luma + onOpenAutoFocus focuses DialogTitle (tabIndex={-1}) instead of first-focusable + visible X close button at top-end corner.
  - dropdown-menu.tsx (291 LOC): radix-luma + DropdownMenuTrigger forwards `disabled` to Radix primitive (not just child button) + DropdownMenuContent `loop={true}` default (WAI-ARIA cyclic ArrowDown wrap) + DropdownMenuItem `variant?: "default"|"destructive"`.
  - input.tsx (19 LOC): standard shadcn Input. W0-verified.
  - kbd.tsx (27 LOC): radix-luma + Kbd + KbdGroup + in-data-[slot=tooltip-content]/in-data-[slot=input-group] nesting variants.
  - number-input-stepper.tsx (316 LOC): CUSTOM — composes shadcn Input + custom SVG steppers + parse/range validation + aria-live region + ArrowUp/Down/Home/End keyboard. At-boundary uses aria-disabled + tabIndex=-1 (NOT native disabled).
  - segmented-control.tsx (~470 LOC): CUSTOM dual-mode (variant="tabs" → role=tablist+roving-tabindex+ArrowLeft/Right+RTL; variant="default" → role=radiogroup+sr-only radios+ArrowLeft/Right wrap). Sliding accent indicator via ResizeObserver with stable containerRef. Sub-Agent A: 7 production call sites, CANNOT be removed.
  - select.tsx (242 LOC): radix-luma + SelectTrigger `hideChevron` + `size="sm"|"default"` + dev-mode a11y warn + single ChevronDownIcon (replaces up/down double-arrows that read as "sort").
  - slider.tsx (108 LOC): radix-luma + project extensions (`trackClassName`/`rangeClassName`/`thumbClassName`/`thumbLabels?: string[]`/`getThumbAriaValueText?: (value: number) => string`) + dev-mode a11y warn + per-thumb aria-label fallback from root aria-label.
  - sonner.tsx (148 LOC): radix-luma + custom `useResolvedTheme()` hook (MutationObserver on documentElement's class attr — the app uses a custom useTheme that toggles `dark` class, NOT next-themes) + RTL position + HugeiconsIcon swaps for success/info/warning/error/loading.
  - switch.tsx (42 LOC): radix-luma + `size?: "sm"|"default"` + dev-mode a11y warn + `data-checked:border-primary/30` (subtle ring, was invisible at full opacity) + `rtl:data-checked:-translate-x-[calc(100%-8px)]` + `after:-inset-y-3` (sub-24px touch target → 44px total, WCAG 2.5.5).
  - tooltip.tsx (45 LOC): standard radix-luma Tooltip wrapper.
- Audited input.tsx vs shadcn reference (W0): Voice Typer matches standard shadcn radix-luma Input. Searched renderer for raw `<input>` usages — found 7 production sites:
  - `components/ui/input.tsx:7` — the shadcn Input primitive itself (KEEP).
  - `components/ui/segmented-control.tsx:436` — internal `<input type="radio">` for radiogroup variant (KEEP — intentional ARIA pattern, biome-ignore lint/a11y/useSemanticElements documented).
  - `pages/vocabulary/components/VocabToolbar.tsx:73` — `<input type="file">` sr-only for OS file picker (KEEP — file picker needs native input; shadcn Input is text-only with text-input styling irrelevant for sr-only picker).
  - `pages/templates/components/TemplateToolbar.tsx:44` — `<input type="file">` sr-only for OS file picker (KEEP — same as VocabToolbar).
  - `pages/onboarding/components/ModelStep.tsx:273` — raw `<input type="checkbox">` for HF consent (REFACTOR → shadcn Checkbox — already flagged by Sub-Agent B).
  - `pages/onboarding/components/ModelStep.tsx:409` — raw `<input type="checkbox">` for cloud consent (REFACTOR → shadcn Checkbox — already flagged by Sub-Agent B).
  - `pages/Onboarding.tsx:443` — raw `<input type="checkbox">` for Done-step consent (REFACTOR → shadcn Checkbox — already flagged by Sub-Agent B).
  - HotkeyPicker.tsx — DOES NOT use raw `<input>` (uses shadcn Button + DropdownMenu + Kbd + useHotkeyCapture hook with keydown listener on containerRef). KEEP.
  - HotkeyChips.tsx — DOES NOT use raw `<input>` (pure presentational using shadcn Kbd + KbdGroup). KEEP.
  - VocabInlineForm.tsx — already uses shadcn Input. KEEP.
  - VocabSearchFilterBar.tsx — already uses shadcn SearchField (which wraps shadcn Input). KEEP.
  - SearchField.tsx — already uses shadcn Input (confirmed by Sub-Agent A). KEEP.
- Audited Slider vs RangeSlider: RangeSlider is a HIGHER-LEVEL wrapper over the shadcn Slider (not a duplicate). Adds deferApply (pointer-up/blur commit), suffix unit display, aria-valuenow/min/max/text, larger thumb. KEEP CUSTOM both.
- Audited Kbd vs common/Kbd: ui/kbd.tsx is shadcn radix-luma muted chip for inline display (tooltips, buttons, chips). common/Kbd.tsx is CUSTOM polymorphic (kbd/code) bordered mono chip used by HelpOverlay + PunctuationCheatSheet where the chip is a primary visual element. Different visual languages; KEEP CUSTOM both. KeyboardPermissionBanner.tsx (216 LOC) is custom platform-aware banner (macOS Accessibility / Linux input / Windows no-op) — no shadcn equivalent; KEEP CUSTOM.
- Audited Modal vs Dialog: Modal.tsx (97 LOC) is a thin wrapper around shadcn Dialog (Dialog's `onOpenChange` → Modal's `onClose` simpler API). 3 production Modal sites (HelpOverlay, PunctuationCheatSheet, TemplateDialog). 1 direct Dialog site (ShareStatsDialog — uses DialogTrigger-as-child for open-on-click affordance Modal doesn't expose). KEEP CUSTOM both.
- Audited ConfirmDialog vs AlertDialog: ConfirmDialog.tsx (184 LOC) wraps AlertDialog with confirmedRef ref (discriminates Confirm vs Cancel close paths — Radix fires onOpenChange(false) once for both), variant mapping (destructive/warning/default → AlertDialogAction className via tailwind-merge), and dismissOnBackdrop opt-in (document-level pointerdown listener — Radix AlertDialogContent hard-replaces caller's onPointerDownOutside). 6 production ConfirmDialog sites (Settings, PrivacySettingsSection, Models, Onboarding, History, Vocabulary). 1 direct AlertDialog site (ConsentGateDialog — uses plain Button instead of AlertDialogAction because Allow must NOT auto-close on click — keep-open-on-persist-failure contract). KEEP CUSTOM both.
- Audited Tooltip vs InfoTooltip vs HotkeyTooltip: ui/tooltip.tsx is shadcn radix-luma Tooltip primitive. InfoTooltip.tsx (113 LOC) wraps Tooltip with info-circle icon button + plain text body + locale-aware aria-label (`t("a11y.moreInfoAbout", {label})`). HotkeyTooltip.tsx (55 LOC) wraps Tooltip with any-trigger-child + label + HotkeyChips body. 4 production Tooltip-direct sites (InfoTooltip internal, HotkeyTooltip internal, KeyringStatusBadge 2x, ThemeSettingsSection color swatches). 2 InfoTooltip sites (SettingRow, TemplateListRow). 4 HotkeyTooltip sites (Sidebar 1, TitleBar 3). Different trigger patterns + different body patterns; KEEP CUSTOM all three.
- Audited Spinner vs Skeleton: Spinner.tsx (101 LOC) is CUSTOM shared loading spinner (replaces 9 duplicated spinners per its docstring). Default root is `<span role="img">` (NOT `<output>` — implicit aria-live caused unwanted "Loading" announcements on every page). decorative prop for nested cases. shadcn Skeleton NOT on disk (confirmed by Sub-Agent B). 12+ files use `animate-pulse` Tailwind utility directly (DashboardSkeleton, SettingsSkeleton, ActiveMicrophoneCard, MicToggleButton, RecordingStatusPill, BubbleVisualizer, BubbleModeContent, HotkeyPicker, LiveQualityFeedback). KEEP CUSTOM. IF future wave adds `skeleton.tsx`, the 2 layout-mirroring skeletons (Dashboard/Settings) still need to stay custom (page-specific layouts).
- Audited EmptyState.tsx (118 LOC): CUSTOM. shadcn has no EmptyState primitive (W0 verified). `<h3>` title (NOT `<p>` — SR heading navigation). variant="info"|"error" with role="status"|"alert" + tinted ring on error. actionRef for programmatic focus. KEEP CUSTOM.
- Audited AudioFilterChain (173 LOC) + FilterRow (173 LOC) + audioFilterLabels (71 LOC) + audioFilterRowDescriptors (460 LOC): well-factored 4-file architecture (registry + builder + renderer + composition root). 24 distinct filter rows (16 sliders + 7 toggles + 1 select). Every RangeSlider uses `deferApply` to prevent IPC flooding. All controls use existing shadcn primitives (Switch, RangeSlider→Slider, Select) internally. KEEP CUSTOM.
- Audited HotkeyPicker (314 LOC) + HotkeyChips (89 LOC) + checkHotkeyConflict (59 LOC) + hotkey-utils + shortcuts (239 LOC) + useHotkeyCapture + hotkey-validation (440 LOC): well-factored 8-file architecture. HotkeyPicker uses shadcn Button + DropdownMenu + Kbd internally (NO raw input). hotkey-validation.ts loads `hotkey_reserved.json` (synced with server's `voice_typer/server/hotkey_reserved.json` via `test_hotkey_reserved_sync.py` cross-language contract). shortcuts.ts is the single source of truth for 12 in-app + dictation shortcut strings + the `IN_APP_BINDINGS` dispatch table for `useGlobalKeyboardShortcuts`. KEEP CUSTOM all 8 files.
- Audited Models components: FamilyLogo (69 LOC — pure img with dark:invert for black logos), LocalModelsPanel (composition root using shadcn Accordion + Button + hugeicons), CloudProvidersPanel (300 LOC — already uses shadcn Input + Button + Switch + KeyringStatusBadge throughout; no raw inputs), ModelCardActions (316 LOC — 4-state button row using shadcn Button + aria-busy + label swap), DownloadProgressBar (265 LOC — hand-rolled `<div role="progressbar">` with throttled aria-valuenow + 4-state fill + error/alert role switch + ETA/speed). shadcn Progress NOT on disk per Sub-Agent B. KEEP CUSTOM all 5 files.
- Audited Microphone components: TestReviewPanel (295 LOC — pure presentational using shadcn Button + `<output aria-live>` + detected-issue literal→i18n key map), MicrophoneListItem (62 LOC — pure presentational using shadcn Button + per-row aria-label), AudioPresetSelector (179 LOC — collapsible panel using shadcn Select + AudioFilterChain; custom expand/collapse `<button>` because shadcn Accordion's structure doesn't fit the nested Select+AudioFilterChain layout). KEEP CUSTOM all 3 files.
- Audited ConsentGateDialog (116 LOC) + HelpOverlay (158 LOC) + PunctuationCheatSheet (222 LOC) + KeyboardPermissionBanner (216 LOC): all use shadcn primitives correctly (AlertDialog/Button/Modal/SearchField/HugeiconsIcon). ConsentGateDialog uses plain Button (NOT AlertDialogAction) inside AlertDialogContent for the keep-open-on-persist-failure contract. HelpOverlay + PunctuationCheatSheet both wrap Modal. KeyboardPermissionBanner is platform-aware with macOS deep-link only. KEEP CUSTOM all 4 files.

Stage Summary:
- Audit partition complete; 0 files modified (investigation-only per scope).
- All findings cited with `file:line` references in the returned report.
- W0 satisfied: shadcn Input API verified via web-search against https://ui.shadcn.com/docs/components/base/input BEFORE the KEEP-CUSTOM recommendation. shadcn has NO EmptyState primitive, NO Skeleton on disk, NO Progress on disk, NO Stepper primitive (Sub-Agent B confirmed for Skeleton/Progress/Stepper; W0 verified for EmptyState).
- W2 satisfied: KEEP-CUSTOM recommendations are justified by real Voice Typer constraints — (a) cross-language contract for hotkey validation (synced `hotkey_reserved.json`), (b) `confirmedRef` discriminator + `variant` mapping + `dismissOnBackdrop` opt-in for ConfirmDialog, (c) `deferApply` IPC debouncing for RangeSlider, (d) MutationObserver theme-sync for Sonner (custom useTheme vs next-themes mismatch), (e) voice-inserted-character polymorphism (`as="code"`) for common/Kbd, (f) keep-open-on-persist-failure for ConsentGateDialog, (g) 4-state progressbar (accent/warning/destructive + error-alert role switch + ETA/speed) for DownloadProgressBar. NOT mechanical deferral.
- E15 (technical debt): the partition has 0 dead code. The 3 raw `<input type="checkbox">` sites in Onboarding/ModelStep are SUB-AGENT B's REFACTOR scope (not this partition). No removals recommended in this partition.
- E12 (never downgrade behavior): the bespoke Kbd/KbdGroup styling in common/Kbd.tsx is intentionally different from ui/kbd.tsx (bordered mono chip vs muted shadcn chip) — consolidating them would force one visual language to win, regressing the other. The bespoke `useResolvedTheme()` in sonner.tsx is required because the app uses a custom useTheme hook that toggles `dark` class (NOT next-themes — comment notes next-themes always returned `{theme: undefined}`). The bespoke AlertDialogAction→Button className routing is required for tailwind-merge conflict resolution (Radix Slot concatenates AFTER Button's already-merged classes, where plain stylesheet order — not tailwind-merge — would decide the winner).
- E14 (regression prevention): the audit IDENTIFIES the test files that would need updating if any REFACTOR proceeds:
  - Onboarding raw-checkbox → shadcn Checkbox REFACTOR (Sub-Agent B's scope): Onboarding.test.tsx needs `vi.mock("@/components/ui/checkbox", ...)` parallel to existing select mock at line 59.
  - HYPOTHETICAL Slider→shadcn Slider replacement (NOT RECOMMENDED — RangeSlider adds deferApply/suffix/aria-valuetext/larger thumb; shadcn Slider has none of these). Would touch RangeSlider.test.tsx + audio/__tests__/FilterRow.test.tsx + every RangeSlider call site in Settings.
  - HYPOTHETICAL Skeleton primitive addition (NOT RECOMMENDED for this audit): 12+ files using `animate-pulse` directly. Would touch DashboardSkeleton + SettingsSkeleton + 10 incidental sites. Defer until a coordinated sweep wave.
- C-BRAND-1 noted: no hardcoded "Voice Typer" anywhere in this partition. FamilyLogo.tsx uses `openai/qwen/nvidia/deepgram` SVG assets (brand identifiers, NOT the user-facing brand — those are model family names, not the app name). KeyboardPermissionBanner uses `t()` for all user-facing strings.
- C-I18N-1/2 noted: every user-facing string resolves through `t()` / `tf()` / `tChoice()`. The `tf(key, fallback)` helper exists in `bubble/helpers.ts:15-18` (Sub-Agent B's partition) — used by BubbleDismissButton. The hotkey-validation.ts `_formatForMessage` is intentionally NOT localized (it formats the rejected hotkey combo for inclusion in a localized error message via `t("hotkeyValidation.*", {label})` param). The DETECTED_ISSUE_LITERALS map in TestReviewPanel.tsx:42-53 maps backend English literal strings → i18n keys with raw-string fallback for unknown future backend additions (preserves information rather than dropping it silently).
- C-STYLE-1 noted: no task IDs / session prefixes in source code; only in this worklog entry.
- C-TEST-5 noted: every test lives in a separate `__tests__/` folder (15 test files in `ui/__tests__/`, 7 in `common/__tests__/`, 9 in `feedback/__tests__/`, 3 in `audio/__tests__/`, 3 in `hotkey/__tests__/`, 1 in `consent/__tests__/`, 1 in `help/__tests__/`, 1 in `__tests__/KeyboardPermissionBanner.test.tsx`, 5 in `models/__tests__/`). No inline test blocks in production source. `components/microphone/` has NO `__tests__/` subfolder — the 3 microphone components are tested indirectly via the Microphone page tests (file-disjoint with Sub-Agent B).
- IPC contract: NO IPC impact from any recommendation in this audit. The hotkey validation mirrors the backend `_validate_hotkey` in `voice_typer/server/config_validators.py` via `data/hotkey_reserved.json` (synced by `test_hotkey_reserved_sync.py` — Sub-Agent C does NOT modify either side). The `set_config` allowlist (SEC-002), `_COMMAND_REGISTRY`, `ALLOWED_COMMANDS`, `PythonRequest`/`PythonPushEvent` unions are untouched. The ConsentGateDialog uses `set_config` for the consent field (already in the SEC-002 allowlist) — no new IPC commands introduced.
- Ready for handoff to implementation sub-agents.


---
Task ID: 3-D
Agent: investigation-D (History + Vocabulary + Templates + Models + Microphone + About + Privacy + _tabBarStyles + ConnectionStatusScreen)
Task: Read-only audit of the file-disjoint partition NOT covered by A/B/C: pages/History.tsx + history/**, pages/Vocabulary.tsx + vocabulary/**, pages/Templates.tsx + templates/**, pages/Models.tsx, pages/Microphone.tsx + microphone/**, pages/About.tsx, pages/Privacy.tsx, pages/_tabBarStyles.ts, components/layout/ConnectionStatusScreen.tsx, plus all corresponding pages/__tests__/*.test.tsx files. Produce a structured Markdown report covering (1) per-page audit (file summary, custom components, reinvented primitives, hardcoded colors/text, i18n violations, duplicate code, KEEP/REPLACE/REFACTOR recommendation); (2) cross-page patterns (lists/tables, pagination, tooltips consistency, modal usage, skeletons, empty states); (3) per-component decisions matrix; (4) risks & gotchas (AGENTS.md constraints touching this scope, tests that will need updating, file-disjoint implementation-wave grouping).

Work Log:
- Read full AGENTS.md (964 lines) — binding rules E1/E6/E10/E12/E14/E15/E16/E18/W0/W2/C-BRAND-1/C-I18N-1/2/C-STYLE-1/C-ARCH-1/C-TEST-1..5/C-LOG-1/2 acknowledged.
- Read worklog.md (521 lines) for orchestrator + Sub-Agent A + B + C context: shadcn primitives inventory (15 on disk: accordion, alert-dialog, button, checkbox, dialog, dropdown-menu, input, kbd, number-input-stepper, segmented-control, select, slider, sonner, switch, tooltip; NO card/skeleton/progress/tabs/chart/radio-group/table/pagination/textarea/stepper). components.json `radix-luma`/zinc/hugeicons config. Sub-Agent A's KEEP-CUSTOM for Sidebar/SegmentedControl/SearchField + Settings-tab-relocation recommendation (move 4 Settings tabs into Sidebar submenu → if accepted, `_tabBarStyles.ts` becomes a Models-only consumer). Sub-Agent B's KEEP-CUSTOM for chart/StatCards/DashboardSkeleton/Bubble/Onboarding-stepper + single REFACTOR for 3 raw `<input type="checkbox">` sites in Onboarding.tsx:443 + ModelStep.tsx:273 + ModelStep.tsx:409 (Sub-Agent C's partition, NOT mine). Sub-Agent C's KEEP-CUSTOM for Modal/ConfirmDialog/InfoTooltip/HotkeyTooltip/Spinner/EmptyState/RangeSlider/AudioFilterChain/HotkeyPicker/Kbd/KeyboardPermissionBanner/ConsentGateDialog/Models components (FamilyLogo, LocalModelsPanel, CloudProvidersPanel, ModelCardActions, DownloadProgressBar)/Microphone components (TestReviewPanel, MicrophoneListItem, AudioPresetSelector).
- W0 web-verified shadcn Table + Pagination exist as official primitives via z-ai web_search against https://ui.shadcn.com/docs/components/base/table + https://ui.shadcn.com/docs/components/base/pagination. Confirmed Table composition = { Table, TableHeader, TableBody, TableCaption, TableCell, TableHead, TableRow } (styled native `<table>` wrapper, NOT a data grid — TanStack Table is the recommended data-table backend). Confirmed Pagination composition = { Pagination, PaginationContent, PaginationItem, PaginationPrevious, PaginationNext, PaginationEllipsis } (Link-based `<nav>` with `<a>` children, headless — does NOT auto-paginate). Both are NOT on disk (per Sub-Agent B's inventory).
- Audited History.tsx (532 LOC): thin composition root importing PageHeading + SearchField + ExportFormatMenu + LastUpdatedIndicator + ConfirmDialog + ActivityList (shared with Home/Dashboard per Sub-Agent B) + EmptyState + Spinner + shadcn Button + shadcn Select + history/hooks/{useHistoryCache, useHistoryExport} + history/utils/historySort. Visibility-gated debouncedRefreshFromEvent (mirrors Home's pattern). 200-item display cap (line 461 `slice(0, 50)` for visible list — comment notes 200 cap intended but renders 50). "Load More" button via shadcn `Button variant="outline"` with custom `border-dashed` styling (line 484-506). Custom inline `<output>` for `aria-live` on `<p className="showingCap">` (no — it's a plain `<p>`). NO reinvented primitives — uses shadcn Button + Select + ExportFormatMenu + ConfirmDialog + EmptyState + Spinner throughout. Custom: `--bg-subtle` + `border-border/10` + `text-(--text-muted)` token usage (consistent with project). Amber-tinted Favorites toggle (line 340-344) — matches Favorites palette used by VocabDuplicateBanner + TemplateListRow match-mode badge (cross-page consistency opportunity, NOT a violation).
- Audited history/hooks/useHistoryCache.ts (361 LOC): owns records/stats/loading/loadingMore/hasMore/loadError state + ref mirrors of `call` + `markUpdated` (for stable identity — same pattern as useVocabulary/useTemplates). Cursor (keyset) pagination via `before_timestamp` + `before_id` (line 56-65) with OFFSET fallback (line 223-226) — backend contract per `voice_typer/server/service/history.py`. `HISTORY_MAX_ROWS=5000` safety cap. `refreshFromEvent` preserves loaded depth via `refreshLimit = Math.max(HISTORY_PAGE_SIZE, offsetRef.current)` (line 314) — no shrinking on background refresh. NO reinvented primitives.
- Audited history/hooks/useHistoryExport.ts (169 LOC): filter-aware export paging loop — branches on searchQuery/favoritesOnly to call get_favorites/search_history/get_history (line 92-110). `EXPORT_MAX_ROWS=10000` cap. Sorts via shared `sortRecords` from `historySort.ts`. Uses `window.window_.exportHistory(rows, format)` IPC bridge — NOT a reinvented primitive (uses the existing IPC bridge per AGENTS.md IPC contract). Info toast when filter active (line 76). NO reinvented primitives.
- Audited history/utils/historySort.ts (68 LOC): pure `sortRecords` + `parseHistorySortOrder` using `Intl.Collator` with `sensitivity: "base"` + `numeric: true` (line 17-20). Locale-aware via `getLocale()` (used inside `Intl.Collator` constructor — but the collator default `undefined` locale means it follows the *runtime* locale, not the i18n locale. NOT a bug because the user's runtime locale matches the i18n locale by the `setLocale` wiring). NO reinvented primitives — pure helper.
- Audited Vocabulary.tsx (466 LOC): thin shell importing PageHeading + EmptyState + Spinner + ConfirmDialog + 7 vocabulary components (VocabToolbar, VocabSearchFilterBar, VocabInlineForm, VocabListHeader, VocabListRow, VocabBulkBar, VocabDuplicateBanner) + 5 vocabulary hooks (useVocabulary, useVocabularyEdit, useVocabularyImportExport, useVocabularyQuickAdd, useVocabularySelection) + vocabulary/lib/{transform, sort, categories, importExport, testServer}. DISPLAY_CAP=200 + "Show more" button (line 413-421) — raw `<button>` (NOT shadcn Button; bespoke rounded-full pill style). ConfirmDialog with `variant="destructive"` + `dismissOnBackdrop` for Clear All (line 453-463). NO reinvented primitives beyond the raw Show-more pill button (consistency candidate — Vocabulary reinvents a pill button that Templates doesn't have because Templates doesn't paginate; consistent with the inline-retry button in VocabListRow's error block).
- Audited vocabulary/components/VocabToolbar.tsx (156 LOC): hidden `<input type="file">` sr-only (line 73-84, aria-hidden + tabIndex=-1 — KEEP per Sub-Agent C: native file picker requires raw input, shadcn Input is text-only). Import/Export/Clear-All buttons cluster + Add Word (primary, ms-auto flush right) — uses shadcn Button throughout. ExportFormatMenu (shared with History/Templates). NO reinvented primitives.
- Audited vocabulary/components/VocabSearchFilterBar.tsx (103 LOC): SearchField + shadcn Select with `hideChevron` + sort glyph (line 64-78). NO reinvented primitives.
- Audited vocabulary/components/VocabInlineForm.tsx (122 LOC): shadcn Input × 2 (trigger + replacement) + shadcn Button (Save + Cancel). `role="alert"` for inline error (line 113). NO reinvented primitives.
- Audited vocabulary/components/VocabListHeader.tsx (73 LOC): sticky column-header row with select-all shadcn Checkbox (`checked="indeterminate"` for partial — line 58-62). Grid layout `grid-cols-[auto_minmax(0,1fr)_minmax(0,1fr)_6.25rem]` matches VocabListRow's grid exactly. NO reinvented primitives.
- Audited vocabulary/components/VocabListRow.tsx (305 LOC): memo'd row with shadcn Checkbox + 3 shadcn Buttons (Test + Delete + Edit) — Edit rightmost per app-wide convention (line 232-248). Spinner decorative size=12 inline (line 261) — uses existing shared Spinner. Retry button is raw `<button>` (line 289-298) with bespoke pill style — minor consistency opportunity (matches VocabToolbar's Show-more pill button). NO reinvented primitives beyond the bespoke pill button (consistent with the page's design language).
- Audited vocabulary/components/VocabBulkBar.tsx (125 LOC): sticky floating bulk-action bar with shadcn Button + shadcn DropdownMenu (Export Selected format menu). Raw `<button>` for Deselect-all (line 109-122) — bespoke icon button (matches VocabDuplicateBanner dismiss button). NO reinvented primitives.
- Audited vocabulary/components/VocabDuplicateBanner.tsx (68 LOC): amber-tinted banner with role="status" + shadcn Button (Remove Duplicates) + raw `<button>` (Dismiss × icon, line 52-65). NO reinvented primitives.
- Audited vocabulary/hooks/useVocabulary.ts (394 LOC): owns entries/loading/loadError/saving + ref mirrors of `call` + `showSnack` (stable identity). `instantDeleteEntry` with 6-second undoable toast via `showUndoableToast` + entriesRef ref mirror (D2-FIX pattern — same as Templates). `loadUsage` (line 153-183) fetches `get_correction_usage` snapshot for per-row "Used N×" indicator. `loadError` fallback uses `t("vocabulary.loadFailedDescription")` (line 222-225) — CORRECT i18n pattern (BG-62 fix). NO reinvented primitives.
- Audited vocabulary/hooks/useVocabularyQuickAdd.ts (161 LOC): inline quick-add row state. `isDuplicateEntryError` discriminator exported (line 22-28). Convenience pre-check via `findDuplicate` (line 111-115). NO reinvented primitives.
- Audited vocabulary/hooks/useVocabularyEdit.ts (141 LOC): inline edit row state — same VocabInlineForm treatment as Add. NO reinvented primitives.
- Audited vocabulary/hooks/useVocabularySelection.ts (147 LOC): bulk selection Set + `bulkDeleteSelected` with 6-second undoable toast (mirrors `instantDeleteEntry` pattern — captures `originalIndexes` Map for restore). NO reinvented primitives.
- Audited vocabulary/hooks/useVocabularyImportExport.ts (202 LOC): hidden `<input type="file">` ref + `doExport` (calls `window_.exportVocabulary({entries}, format)` IPC bridge) + `handleImportFile` (parses via `parseImportedVocabulary` — supports JSON-array + backend-shape VocabularyData + CSV). Pair-based dedupe. NO reinvented primitives.
- Audited vocabulary/lib/transform.ts (193 LOC): pure `flattenEntries` + `rebuildData` + `dedupeEntries` + `findDuplicateGroups` + `normalizeWrongPhrase` + `makeEntryId` + `withEntryIds`. Mirrors backend `_normalize_wrong_phrase` (server/service/vocabulary.py) per cross-language contract. NO reinvented primitives.
- Audited vocabulary/lib/sort.ts (52 LOC): pure `sortEntries` generic over T using `Intl.Collator` with `getLocale()`. NO reinvented primitives.
- Audited vocabulary/lib/categories.ts (141 LOC): CATEGORIES list + `findDuplicate` + `detectCategory` (auto-detect heuristics with CJK + Arabic script detection fallbacks). NO reinvented primitives.
- Audited vocabulary/lib/importExport.ts (177 LOC): `parseImportedVocabulary` accepting JSON-array + backend VocabularyData + CSV (RFC 4180 quoted). NO reinvented primitives.
- Audited Templates.tsx (206 LOC): thin shell importing PageHeading + EmptyState + Spinner + 4 templates components (TemplateToolbar, TemplateSearchSortBar, TemplateListRow, TemplateDialog) + 3 templates hooks (useTemplates, useTemplateDialog, useTemplateImportExport) + templates/lib/types. Stable-callback wrapper via `openEditDialogRef` for memo'd TemplateListRow (line 75-81, mirrors ActivityListRow pattern per Sub-Agent B). Empty states: 3 branches (load-error → variant="error", genuinely empty → "create first", search-no-matches → "noResults") (line 91-181). NO reinvented primitives.
- Audited templates/components/TemplateDialog.tsx (167 LOC): uses shared `Modal` + `ModalFooter` (wraps shadcn Dialog — per Sub-Agent C, KEEP CUSTOM). shadcn Input for trigger + shadcn Select for match-mode + RAW `<textarea>` for output (line 104-116, NOT shadcn Textarea — Textarea NOT on disk per Sub-Agent B inventory). ZU-30: Save disabled until both fields non-whitespace (line 54). Unknown-variables warning (line 124-133) via regex match of `{[^}]+}` tokens not in `VARIABLES` list. NO reinvented primitives beyond the raw `<textarea>` (candidate for migration IF shadcn Textarea is added in a future sweep).
- Audited templates/components/TemplateToolbar.tsx (82 LOC): hidden `<input type="file">` (line 44-55, KEEP per Sub-Agent C). Import + Export (ExportFormatMenu) + Add Template — all shadcn Button. NO reinvented primitives.
- Audited templates/components/TemplateListRow.tsx (124 LOC): memo'd row with InfoTooltip (existing shared per Sub-Agent C) for variable-list tooltip + shadcn Button × 2 (Delete + Edit, Edit rightmost per app-wide convention). Match-mode badge uses raw `<output>` (line 64-76) with conditional amber/accent classes — bespoke but consistent with the favorites-tint pattern. NO reinvented primitives.
- Audited templates/components/TemplateSearchSortBar.tsx (66 LOC): SearchField + shadcn Select — identical pattern to VocabSearchFilterBar (without `hideChevron` and entryCount placeholder). NO reinvented primitives.
- Audited templates/hooks/useTemplates.ts (344 LOC): owns templates/loading/loadError state + ref mirrors of `call` (stable identity). One-time localStorage→backend migration (line 137-167). `instantDeleteTemplate` with 6-second undoable toast + templatesRef ref mirror (D2-FIX pattern — same as Vocabulary). `loadError` fallback uses `t("templates.loadFailedDescription")` (line 179, 189-193) — CORRECT i18n pattern (BG-62 fix). NO reinvented primitives.
- Audited templates/hooks/useTemplateDialog.ts (173 LOC): add/edit dialog state — openAddDialog/openEditDialog/saveTemplate. Duplicate-trigger guard (line 114-121). NO reinvented primitives.
- Audited templates/hooks/useTemplateImportExport.ts (173 LOC): hidden `<input type="file">` ref + `doExport` (calls `window_.exportTemplates({templates}, format)` IPC bridge via cast — line 82-84) + `handleImportFile` (parses via `parseImportedTemplates` + de-dupes by trigger+output+match_mode). NO reinvented primitives.
- Audited templates/lib/types.ts (50 LOC): TYPES + VARIABLES list. NO reinvented primitives.
- Audited templates/lib/storage.ts (168 LOC): `loadTemplatesFromLocalStorage` + `loadTemplatesFromBackend` + `saveTemplates` (async, rethrows IPC errors) + `makeRowId` (crypto.randomUUID fallback). NO reinvented primitives.
- Audited templates/lib/transform.ts (124 LOC): `toRows` + `rowsToTemplates` + `sortTemplateRows` + `parseImportedTemplates` (accepts bare JSON array + `{templates: [...]}` shape). NO reinvented primitives.
- Audited Models.tsx (265 LOC): thin composition root importing ConfirmDialog + LastUpdatedIndicator + PageHeading + Spinner + CloudProvidersPanel + LocalModelsPanel + shadcn Button + shadcn SegmentedControl (`variant="tabs"`) + useModelLifecycle + `_tabBarStyles` shared constants (line 47-49). ConfirmDialog with `variant="destructive"` for model deletion (line 251-262) — hard delete (no undo, deliberate per docs/ux/model-delete-rationale.md — model delete holds 1.5-3 GB; soft-delete undo would defeat the user's intent). NO reinvented primitives. Confirmed ONLY one `SegmentedControl variant="tabs"` usage on this page (line 96-107) — Settings is the only OTHER `variant="tabs"` consumer per Sub-Agent A; if Settings's tab UI is relocated to Sidebar per A's recommendation, Models becomes the sole `_tabBarStyles.ts` consumer and the file collapses to a one-call-site helper.
- Audited Microphone.tsx (282 LOC): thin composition root importing LastUpdatedIndicator + PageHeading + EmptyState + OfflinePackPreparingBanner + Spinner + useOfflinePackDownload + 3 microphone components (MicrophonePermissionBanner, ActiveMicrophoneCard, AvailableMicrophonesList) + 3 microphone hooks (useMicrophoneData, useMicrophonePermission, useMicrophoneTest) + computeAudioKey. `meterRef` wrapper for rAF direct-DOM level-meter writes (mirrors bubble's useAudioLevels pattern per Sub-Agent B). `hasAttempted` state gates the "Preparing offline engine…" banner (line 75 + 220). NO reinvented primitives.
- Audited microphone/components/MicrophonePermissionBanner.tsx (88 LOC): platform-aware (macOS/Windows deep-link URL schemes — `x-apple.systempreferences:` + `ms-settings:privacy-microphone`; Linux has no equivalent) banner. Uses raw `<a href={deepLink}>` for the OS-settings link (line 73-85) — NOT a shadcn Button-as-link (would also work; current is acceptable). Custom destructive banner styling (`bg-destructive/10 border-destructive/30`). NO reinvented primitives — but no shadcn Button either (the deep-link uses raw `<a>` for URL handling).
- Audited microphone/components/AvailableMicrophonesList.tsx (121 LOC): renders "Use System Default" row + list of microphones via MicrophoneListItem (existing shared per Sub-Agent C). Native `<ul>/<li>` semantics (line 64-118). EmptyState when zero microphones (line 42-49). shadcn Button for "Use" per row (line 89-98). NO reinvented primitives.
- Audited microphone/components/ActiveMicrophoneCard.tsx (408 LOC): main interactive card. RangeSlider (shared per Sub-Agent C) with `deferApply` for test-duration slider (line 258-267). LevelBar + LiveQualityFeedback (shared per Sub-Agent B's partition). TestReviewPanel + AudioPresetSelector (shared per Sub-Agent C, both `React.memo`'d with custom comparators — line 388-407). Custom `border-accent bg-(--bg-subtle)` card surface (NOT shadcn Card — Card NOT on disk per Sub-Agent B). Custom amber-tinted "filters changed" notice (line 273-275). NO reinvented primitives — uses shadcn Button + RangeSlider + memoised children correctly.
- Audited microphone/hooks/useMicrophonePermission.ts (74 LOC): OS-level permission probe via `navigator.permissions.query({name: "microphone"})` + `addEventListener("change", handler)` cleanup (line 38-44 + 56-69 — fix for the onchange leak bug). NO reinvented primitives.
- Audited microphone/hooks/useMicrophoneTestSession.ts (preview only — 32 KB file): extracted from former useMicrophoneTest monolith per the docstring. Owns test-recording lifecycle state + countdown + elapsed timers + `microphone_test_complete` push-event subscription. `useCallback` deps align with sibling hooks per the 1-C Finding 8 fix. NO reinvented primitives (sub-agent did not deep-audit the test-session state machine — flagged for future wave if needed).
- **C-I18N-1 VIOLATION found**: `pages/microphone/hooks/useMicrophoneData.ts:161` has `setLoadError(err instanceof Error ? err.message : "Failed to load microphone data")` — hardcoded English fallback string. Sibling hooks `useVocabulary.ts:222` and `useTemplates.ts:189` were both fixed in session BG-62 to use `t("*.loadFailedDescription")`; `useHistoryCache.ts:251` falls back to `String(err)` (raw error). `useMicrophoneData.ts:161` was MISSED in the BG-62 sweep. The localized key `microphone.loadFailedDescription` does NOT exist in any locale file yet — needs to be added to all 8 locales (en/ar/de/es/fr/hi/ru/zh) per C-I18N-1.
- **C-BRAND-1 VIOLATION found (minor — comment only)**: `pages/Privacy.tsx:1` has `// Privacy page — how Voice Typer handles audio and data.` — the literal "Voice Typer" appears in a prose comment. C-BRAND-1 explicitly says "Prose comments describing the app must also avoid the literal brand." Sibling pages About.tsx:1-3 (comment) avoids the literal via "product identity" wording. Privacy.tsx:1 should be re-worded to "// Privacy page — how {APP_NAME} handles audio and data." (import APP_NAME at the top of the file is already present — Privacy.tsx doesn't currently import APP_NAME; About.tsx:20 does). Actually the existing import is missing — this is a real comment fix that doesn't require code change beyond the comment text.
- Audited About.tsx (118 LOC): static info page — uses PageHeading + ReadonlyRow (shared) + Logo + APP_NAME import (line 20 — C-BRAND-1 satisfied for the runtime-rendered name). APP_VERSION read from package.json at build time (line 25-30, VERSION-SOURCE-FIX). NO reinvented primitives — custom `rounded-xl border bg-(--bg-subtle)` card (NOT shadcn Card — Card NOT on disk per Sub-Agent B). C-I18N-1/2 satisfied — every label via `t()`. C-BRAND-1 satisfied (APP_NAME import + `t("about.versionValue", {version: APP_VERSION})` interpolation). NO reinvented primitives.
- Audited Privacy.tsx (129 LOC): static privacy disclosure — uses PageHeading + useT() for locale re-render + `get_status` IPC for config dir interpolation (line 73-91). PRIVACY_TOPICS array drives the 5 disclosure rows (line 35-57) — clean data-driven render. Custom divide-y list container (NOT shadcn Card — Card NOT on disk). C-I18N-1/2 satisfied — every label via `t()`. C-BRAND-1 satisfied for runtime strings (uses `t(topic.desc, {configDir})` — no `appName` interpolation needed because the disclosure copy was written to refer to "the app" indirectly). NO reinvented primitives. C-BRAND-1 violation flagged above (comment-only at line 1).
- Audited _tabBarStyles.ts (82 LOC): shared sticky-tab class names co-consumed by Settings.tsx (line 39-41, 436, 449) + Models.tsx (line 47-49, 94, 102). Two constants: `tabPageHeaderClassName` (sticky wrapper) + `tabPageIndicatorClassName` (SegmentedControl indicator override). Module-level comment explicitly notes the file lives under `pages/` (NOT `components/common/PageTabs.tsx`) to keep within this sub-agent's file scope (line 32-36). NO reinvented primitives — pure className constants.
- Audited components/layout/ConnectionStatusScreen.tsx (182 LOC): full-screen disconnect/reconnecting/restarting overlay. Uses EmptyState (variant="error") + Spinner wrapped in `<output aria-live="polite">` (line 124-130 — restores the implicit live-region announcement the Spinner default used to provide; Sub-Agent B noted Spinner's default root was changed from `<output>` to `<span role="img">` to avoid unwanted "Loading" announcements on every page; this is the ONE place where the live region is restored explicitly). Hand-rolled `<div role="progressbar">` for the connecting-progress bar (line 136-150 — shadcn Progress NOT on disk per Sub-Agent B; matches the Onboarding + ModelStep + PrewarmAndUpdates pattern). shadcn Button for "Force Retry" (line 161-176). `useEffect` auto-focuses the Retry button on `status === "disconnected"` transition (line 52-61 — WCAG 2.4.3 Focus Order Level A). NO reinvented primitives — uses EmptyState + Spinner + shadcn Button + hand-rolled progressbar (consistent with project pattern).
- Audited partition tests: `History.test.tsx` (520 lines — R7-F13 single-callback-identity contract + R7-F16 200-item cap + Favorites aria-pressed + filter-aware export paging + Clear-All confirmation messaging). `History-render-loop-guard.test.tsx` (41 lines — page-level render-loop OOM guard via `renderLoopGuard` helper, asserts `get_history` + `get_today_stats` fire EXACTLY once under fresh-`call`-per-render worst case). `Vocabulary.test.tsx` (312 lines — D2-FIX undo-restore-exactly-once). `Vocabulary-bg60-bg62.test.tsx` (93 lines — load-error variant="error" + localised `loadFailedDescription`). `Vocabulary-render-loop-guard.test.tsx` (asserts `get_vocabulary` + `get_correction_usage` fire EXACTLY once). `vocabulary/hooks/__tests__/useVocabularyImportExport.test.tsx`, `vocabulary/__tests__/importExport.test.ts`, `vocabulary/__tests__/categories-detect.test.ts`, `vocabulary/__tests__/Vocabulary-delete-persistence.test.tsx`, `vocabulary/__tests__/Vocabulary-page-features.test.tsx`, `vocabulary/__tests__/Vocabulary-page-improvements.test.tsx`. `Templates-bg60-bg63.test.tsx` (191 lines — load-error variant="error" + export format forwarding). `Templates-nh15-no-results.test.tsx`, `Templates-nh28-instant-delete.test.tsx`, `Templates-render-loop-guard.test.tsx`, `templates/components/__tests__/TemplateDialog-zu30.test.tsx`. `ModelsPage.test.tsx` (789 lines — Import Model flow + MDL-3/5/9/16). `ModelsPage-nh29-cancel-state-reset.test.tsx`, `Models-render-loop-guard.test.tsx`. `Microphone-render-loop-guard.test.tsx` (41 lines — asserts `get_microphones` + `get_config` fire EXACTLY once). `microphone/hooks/__tests__/useMicrophoneData.test.ts`, `useMicrophonePlayback.test.ts`, `useMicrophonePermission-cleanup.test.ts`, `useMicrophoneTest.test.ts`, `useMicrophoneTestSession.test.ts`, `useMicrophoneLevelMonitor-wake-on-event.test.tsx`. `About.test.tsx` (502 lines — product identity + Diagnostics model-truth parity with Analytics). `About-privacy-fix.test.tsx` (136 lines — BG-59 BG-60 BG-62 Privacy URL fix). `ConnectionStatusScreen.test.tsx` (157 lines — 4 status branches + retry button autofocus). `data-pages-live-region-guards.test.tsx` (339 lines — exactly-ZERO / exactly-ONE live-region count assertions for Models + History + Dashboard). `pages-improvements.test.tsx` (790 lines — R7-F8/F9/F10/F11/F12/F13/F15/F16/F18 + b-Microphone + b-Settings tabpanel contract). `client-pages-fixes.test.tsx`, `debug-test.test.tsx`.
- Composed per-component decision matrix (KEEP / REPLACE / REFACTOR / REMOVE) with rationale per AGENTS.md W2 (real-constraint justification, not mechanical deferral).

Stage Summary:
- Audit partition complete; 0 files modified (investigation-only per scope).
- All findings cited with `file:line` references in the returned report.
- W0 satisfied: shadcn Table + Pagination APIs verified via web-search against the official docs URLs BEFORE the KEEP-CUSTOM recommendation. Neither is on disk per Sub-Agent B's inventory; neither is a fit for any current page in this partition (no semantic `<table>` data displays; no multi-page pagination — Vocabulary uses a soft DISPLAY_CAP=200 + Show-more button; History uses an infinite "Load More" cursor).
- W2 satisfied: every KEEP-CUSTOM recommendation justified by a real Voice Typer constraint — (a) shadcn Table NOT on disk + no semantic tabular data in the partition (Vocabulary is a 2-column list, not a table; History is a vertical list; Templates is a vertical list; About/Privacy are static card layouts; Microphone is a vertical card stack; Models is tabbed accordions); (b) shadcn Pagination NOT on disk + no multi-page pagination pattern (Vocabulary's DISPLAY_CAP is a soft limit with Show-more, History's Load More is cursor-based infinite-scroll, all other pages render unbounded lists); (c) shadcn Textarea NOT on disk (the 1 raw `<textarea>` in TemplateDialog is the only candidate, and adding shadcn Textarea for ONE 5-line field is not justified); (d) shadcn Card NOT on disk + bespoke `bg-(--bg-subtle) border-border/10` containers are the project-wide list/card surface language; (e) shared Modal/ConfirmDialog/InfoTooltip/EmptyState/Spinner/SearchField/ExportFormatMenu/LastUpdatedIndicator/PageHeading/ReadonlyRow/RangeSlider/LevelBar/LiveQualityFeedback/MicrophoneListItem/AudioPresetSelector/TestReviewPanel/LocalModelsPanel/CloudProvidersPanel/ModelCardActions/DownloadProgressBar/FamilyLogo all reused correctly per Sub-Agent C's audit; (f) the hand-rolled `<div role="progressbar">` in ConnectionStatusScreen is consistent with the Onboarding + ModelStep + PrewarmAndUpdates pattern (shadcn Progress NOT on disk).
- E12 (never downgrade behavior): every recommendation preserves existing observable behavior (zero behavior-changing migrations proposed). The 2 REFACTOR findings (i18n fallback fix + comment brand fix) are bug fixes that bring the partition INTO compliance with AGENTS.md constraints — they don't change observable UX for end users (only the localised fallback string when a non-Error rejection is caught).
- E15 (technical debt): the partition has 0 dead code. The 3 raw `<input type="checkbox">` sites in Onboarding (Sub-Agent B's partition, NOT mine) are NOT in this audit's scope. The 2 raw `<input type="file">` sites in VocabToolbar + TemplateToolbar are intentional (native file picker requires raw input per Sub-Agent C). The 1 raw `<textarea>` in TemplateDialog is a candidate for migration IF shadcn Textarea is added to disk (NOT recommended for this audit — 1 site, low value). The 2 raw `<button>` instances in VocabListRow (Retry button) + VocabToolbar (Show-more pill) + VocabBulkBar/VocabDuplicateBanner (Dismiss × icon) are bespoke pill buttons consistent with the page's design language — NOT a duplication (each carries different content + handler).
- E14 (regression prevention): the audit IDENTIFIES the test files that would need updating if any REFACTOR proceeds:
  - **REFACTOR wave 1 — `useMicrophoneData.ts:161` i18n fallback fix** (1 site): add `microphone.loadFailedDescription` key to all 8 locale files (en/ar/de/es/fr/hi/ru/zh) per C-I18N-1. Test impact: `pages-improvements.test.tsx:580-582` already asserts no hardcoded `"Loading…"` string in the renderer source — extend that assertion to cover `"Failed to load microphone data"` OR add a parallel assertion that the source uses `t("microphone.loadFailedDescription")`. New test: assert the load-error EmptyState on the Microphone page surfaces the localised string when a non-Error rejection is thrown (mirror `Vocabulary-bg60-bg62.test.tsx`'s pattern). Test risk: LOW (1 hook + 8 locale files + 1 test extension).
  - **REFACTOR wave 2 — `Privacy.tsx:1` comment brand fix** (1 site): reword the comment to avoid the literal "Voice Typer" — no source code change, no test impact. Test risk: NONE (comment-only edit).
- C-BRAND-1 noted: 1 violation found in `pages/Privacy.tsx:1` (prose comment containing literal "Voice Typer"). About.tsx:20 correctly imports + uses `APP_NAME` from `@/branding`. All locale strings use `{appName}` placeholder correctly (verified for `app.startingBackend`, `app.restartingBackend`, `microphone.permissionDeniedMessage*`, `vocabulary.emptyDescription`, `about.productDesc`, `about.audioProcessingDesc`, `about.cloudAsrDesc`, `about.voiceBiometricsDesc`, `about.versionValue`, etc.).
- C-I18N-1/2 noted: 1 violation found in `pages/microphone/hooks/useMicrophoneData.ts:161` (hardcoded English fallback `"Failed to load microphone data"` when caught error is not an Error instance). Sibling hooks `useVocabulary.ts:222` + `useTemplates.ts:189` were both fixed in session BG-62; `useHistoryCache.ts:251` falls back to `String(err)` (raw error, no localized fallback but no hardcoded English either). `useMicrophoneData.ts:161` was MISSED in the BG-62 sweep. Every other user-facing string in this partition resolves through `t()` / `tf()` / `tChoice()`. The `tf(key, fallback)` helper (Sub-Agent B's bubble partition) is NOT used here — the partition uses bare `t()` calls (correct for keys that exist in all 8 locales).
- C-STYLE-1 noted: no task IDs / session prefixes in source code; only in this worklog entry. Comment tags like `R7-F8`, `D2-FIX`, `BG-62`, `BG-63`, `NH-1`, `NH-15`, `NH-28`, `NH-29`, `ZU-30`, `BG-59`, `F2`, `F4`, `F11-FIX`, `IPD-1`, etc. appear in source comments — these are pre-existing session-prefix anchors that pre-date C-STYLE-1's codification (they reference fix histories documented in `worklog.md`/`SUMMARY.md`). New code added by this audit would not introduce new prefixes; the existing ones are flagged as legacy and should be cleaned up in a separate sweep (out of scope).
- C-ARCH-1 noted: History.tsx (532 LOC), Vocabulary.tsx (466 LOC), Templates.tsx (206 LOC), Models.tsx (265 LOC), Microphone.tsx (282 LOC), About.tsx (118 LOC), Privacy.tsx (129 LOC), ConnectionStatusScreen.tsx (182 LOC) — all WITHIN the ≤~300 line soft limit OR modestly over it (History 532, Vocabulary 466). The over-limit pages (History + Vocabulary) are ALREADY thin composition roots that delegate to extracted `./history/**`, `./vocabulary/**`, `./templates/**`, `./microphone/**` packages — the LOC count is inflated by extensive inline documentation comments explaining the D2-FIX + R7-F13 + cursor-pagination contracts. A further split (extracting inline handlers into a `useHistoryActions` hook) is possible but offers no behavior gain — out of scope for THIS audit (which flags, doesn't fix).
- C-TEST-5 noted: every test lives in a separate `__tests__/` folder. Test files for this partition: 22 in `pages/__tests__/`, 6 in `pages/history/hooks/__tests__/` + `pages/history/utils/__tests__/`, 6 in `pages/vocabulary/__tests__/` + `pages/vocabulary/hooks/__tests__/`, 4 in `pages/templates/components/__tests__/`, 6 in `pages/microphone/hooks/__tests__/`. No inline test blocks in production source.
- IPC contract: NO IPC impact from any recommendation in this audit. The partition uses ONLY already-allowlisted IPC commands: `get_history`, `get_favorites`, `search_history`, `get_today_stats`, `delete_history`, `restore_history`, `toggle_favorite`, `clear_history`, `get_vocabulary`, `save_vocabulary`, `get_correction_usage`, `test_vocabulary_correction`, `get_templates`, `save_templates`, `get_microphones`, `get_config`, `set_config`, `microphone_test_start`, `microphone_test_stop`, `select_microphone`, `import_model`, `delete_model`, `get_model_status`, `get_model_catalog`, `get_status`. The `_COMMAND_REGISTRY` (server), `ALLOWED_COMMANDS` (main), `PythonRequest`/`PythonPushEvent` (renderer types) unions are UNTOUCHED. SEC-002 (`set_config` allowlist) is untouched — Vocabulary + Templates persist via `save_vocabulary` + `save_templates` (NOT `set_config`). SEC-026 (sandboxed bubble preload) untouched — none of these pages touch the bubble window. The `window.window_.exportHistory` / `exportVocabulary` / `exportTemplates` IPC bridges (used by the export hooks) are pre-existing main-process bridges, not server IPC commands — they are NOT in the `_COMMAND_REGISTRY` allowlist and don't need to be.
- File-disjoint implementation-wave grouping: this audit's partition is INDEPENDENT of A (Sidebar/Settings), B (Dashboard/Bubble/Home/Onboarding), and C (UI primitives + common + feedback + audio + hotkey + consent + help + KeyboardPermissionBanner + models components + microphone components). The only OVERLAP is `_tabBarStyles.ts` (co-owned by Settings per A + Models per D) — if A's Settings-tab-relocation recommendation proceeds, Models becomes the sole consumer and the file can be inlined into Models.tsx (or kept as-is — it's only 2 lines of constants). The other cross-cutting dependency is the REFACTOR wave 1 (useMicrophoneData i18n fix) which touches Microphone.tsx + 8 locale files — locale files are co-owned by all sub-agents (any new i18n key needs to be added to all 8), but the actual code change is localised to `useMicrophoneData.ts` (this partition's exclusive file).
- Ready for handoff to implementation sub-agents.

Final Report (inline):

# Audit Report — Sub-Agent D (History + Vocabulary + Templates + Models + Microphone + About + Privacy + ConnectionStatusScreen)

## 1. Per-page audit

### 1.1 History page (`pages/History.tsx` + `pages/history/**`)

**File summary**
- `pages/History.tsx` (532 LOC) — thin composition root.
- `pages/history/hooks/useHistoryCache.ts` (361 LOC) — records/stats/loading/loadError state + cursor (keyset) pagination via `before_timestamp` + `before_id` (line 56-65) with OFFSET fallback.
- `pages/history/hooks/useHistoryExport.ts` (169 LOC) — filter-aware export paging loop, calls `window.window_.exportHistory(rows, format)` IPC bridge.
- `pages/history/utils/historySort.ts` (68 LOC) — pure `sortRecords` using `Intl.Collator` with `sensitivity: "base"` + `numeric: true`.

**Page purpose**: view + search + sort + favorite + clear-all + export the dictation history log. Backend-owned 50-row pages with infinite "Load More" cursor pagination.

**Custom components used**
- Shared (not in this partition): `PageHeading`, `SearchField`, `ExportFormatMenu`, `LastUpdatedIndicator`, `ConfirmDialog`, `EmptyState`, `Spinner`, `ActivityList` (shared with Home/Dashboard per Sub-Agent B).
- shadcn primitives: `Button` (Favorites toggle, Clear All, Sort trigger via Select, Load More), `Select` (sort order).
- No partition-specific custom components — all rendering delegates to shared primitives + `ActivityList`.

**Reinvented primitives?**
- **NO.** Every interactive control uses existing shadcn primitives or shared common components. The "Load More" button (line 484-506) is a shadcn `Button variant="outline"` with custom `border-dashed` className — bespoke styling, not a reinvented primitive.

**Hardcoded colors / hardcoded text (C-BRAND-1)?**
- Amber-tinted Favorites toggle: `bg-amber-400/15 text-amber-700 border-amber-400/30 hover:bg-amber-400/20 dark:text-amber-400` (line 342). Same amber palette as `VocabDuplicateBanner`, `TemplateListRow` match-mode badge, `VocabListRow` test-no-change message — cross-page consistency opportunity (no violation; amber is the project's "soft warning / favorite" tone).
- No hardcoded "Voice Typer" / "VoiceTyper" anywhere in the partition (verified via ripgrep).

**i18n violations?**
- **NO.** Every label, aria-label, toast, and EmptyState string resolves through `t()`. The `loadError` fallback in `useHistoryCache.ts:251` uses `String(err)` (raw error) — not a localized fallback, but not a hardcoded English literal either. Sibling hooks `useVocabulary.ts:222` + `useTemplates.ts:189` use `t("*.loadFailedDescription")`; History is the OUTLIER (uses `String(err)` instead of a localized fallback). Inconsistent but not a C-I18N-1 violation.

**Duplicate code across pages?**
- The `setFilter(query, favoritesOnly)` ref-mirror pattern (line 87) mirrors the `filterRef` pattern in `useVocabulary` + `useTemplates` — DRY across the 3 list pages (History, Vocabulary, Templates all share the same `callRef` + `entriesRef`/`recordsRef`/`templatesRef` + `loadError` + `loadMore`/`loadVocabulary`/`loadRows` shape). Not a duplication — a shared architectural pattern that could be extracted into a generic `useBackendList<T>` hook in a future wave (out of scope for this audit).

**Recommendation: KEEP CUSTOM** — no reinvented primitives, all shared primitives used correctly, cursor pagination + filter-aware export are product-specific. No migration needed.

### 1.2 Vocabulary page (`pages/Vocabulary.tsx` + `pages/vocabulary/**`)

**File summary**
- `pages/Vocabulary.tsx` (466 LOC) — thin composition root.
- `pages/vocabulary/components/`: VocabToolbar (156 LOC), VocabSearchFilterBar (103 LOC), VocabInlineForm (122 LOC), VocabListHeader (73 LOC), VocabListRow (305 LOC), VocabBulkBar (125 LOC), VocabDuplicateBanner (68 LOC).
- `pages/vocabulary/hooks/`: useVocabulary (394 LOC), useVocabularyQuickAdd (161 LOC), useVocabularyEdit (141 LOC), useVocabularySelection (147 LOC), useVocabularyImportExport (202 LOC).
- `pages/vocabulary/lib/`: transform (193 LOC), sort (52 LOC), categories (141 LOC), importExport (177 LOC), testServer.

**Page purpose**: CRUD for the wrong→correct correction list. Flat two-column layout (categories hidden from UI, auto-detected on save). Inline add + edit (no modal — Add and Edit both render the same `VocabInlineForm` row treatment). Bulk selection + bulk delete with 6-second undoable toast. Live-engine test per row (TestTube icon).

**Custom components used**
- Shared: `PageHeading`, `EmptyState`, `Spinner`, `ConfirmDialog`.
- shadcn primitives: `Button`, `Input` (×2 in VocabInlineForm), `Checkbox` (select-all in VocabListHeader + per-row in VocabListRow), `Select` (sort in VocabSearchFilterBar), `DropdownMenu` (Export Selected in VocabBulkBar).
- Partition-specific: VocabToolbar, VocabSearchFilterBar, VocabInlineForm, VocabListHeader, VocabListRow, VocabBulkBar, VocabDuplicateBanner — all presentational, all memo'd where appropriate.

**Reinvented primitives?**
- **NO.** Every interactive control uses existing shadcn primitives. The "Show more" pill button (line 413-421) is a raw `<button>` with bespoke rounded-full styling — consistent with the Retry pill button inside VocabListRow's error block (line 289-298). NOT a reinvented primitive; it's a per-use bespoke pill button.
- `VocabListHeader` uses shadcn `Checkbox` with `checked="indeterminate"` for partial selection (line 58-62) — CORRECT use of Radix's indeterminate state.
- `VocabBulkBar` uses shadcn `DropdownMenu` for the "Export Selected" format menu (line 80-108) — matches the `ExportFormatMenu` shared component's pattern (History + Templates both use `ExportFormatMenu`; Vocabulary has a SEPARATE inline DropdownMenu here because the bulk-bar needs a different trigger button styling — minor consistency opportunity, could be unified by passing `triggerVariant="bulkBar"` to `ExportFormatMenu`).

**Hardcoded colors / hardcoded text (C-BRAND-1)?**
- Amber-tinted duplicate banner (line 33-48) + amber-tinted test-entry-no-change message (VocabListRow.tsx:280) + emerald-tinted test-corrected text (VocabListRow.tsx:275) — product-specific notification tones, consistent with project palette. NOT violations.
- No hardcoded "Voice Typer" anywhere (verified).

**i18n violations?**
- **NO.** `useVocabulary.ts:222` correctly uses `t("vocabulary.loadFailedDescription")` as the non-Error fallback (BG-62 fix). Every label, aria-label, toast, EmptyState string resolves through `t()`.

**Duplicate code across pages?**
- `VocabBulkBar`'s inline `DropdownMenu` for Export Selected (line 80-108) duplicates the structure of `ExportFormatMenu` (which has JSON + CSV items). Difference: the bulk bar's trigger button has bespoke pill styling + the "Export Selected" label, while `ExportFormatMenu`'s trigger is a standard outline button. Could be unified by extending `ExportFormatMenu` with an optional `trigger` slot or `triggerClassName` prop — minor refactor opportunity, NOT a violation.
- The `instantDeleteEntry` + 6-second undoable toast pattern (useVocabulary.ts:306-357) mirrors `instantDeleteTemplate` in `useTemplates.ts:225-312` AND `bulkDeleteSelected` in `useVocabularySelection.ts:86-136`. Three implementations of the same undoable-toast-delete pattern — could be extracted into a shared `useUndoableDelete<T>` hook in a future wave (out of scope).

**Recommendation: KEEP CUSTOM** — no reinvented primitives, all shared primitives used correctly, the 7 vocabulary components + 5 hooks are well-factored presentational + state slices. The single REFACTOR opportunity (unify VocabBulkBar's DropdownMenu with ExportFormatMenu) is cosmetic and out of scope.

### 1.3 Templates page (`pages/Templates.tsx` + `pages/templates/**`)

**File summary**
- `pages/Templates.tsx` (206 LOC) — thin composition root.
- `pages/templates/components/`: TemplateDialog (167 LOC), TemplateToolbar (82 LOC), TemplateListRow (124 LOC), TemplateSearchSortBar (66 LOC).
- `pages/templates/hooks/`: useTemplates (344 LOC), useTemplateDialog (173 LOC), useTemplateImportExport (173 LOC).
- `pages/templates/lib/`: types (50 LOC), storage (168 LOC), transform (124 LOC), sanitize.

**Page purpose**: CRUD for text-expansion templates (trigger → output with `{today}/{now}/{clipboard}/{username}` variable substitution + match-mode exact/contains).

**Custom components used**
- Shared: `PageHeading`, `EmptyState`, `Spinner`, `Modal`, `ModalFooter`, `InfoTooltip`.
- shadcn primitives: `Button`, `Input`, `Select`, raw `<textarea>`, raw `<input type="file">`.
- Partition-specific: TemplateDialog, TemplateToolbar, TemplateListRow, TemplateSearchSortBar — all presentational.

**Reinvented primitives?**
- **1 raw `<textarea>`** in `TemplateDialog.tsx:104-116` for the multi-line output field. shadcn `Textarea` is NOT on disk (per Sub-Agent B's inventory). The raw textarea is styled to match the form's `Input` styling (border-border/10, bg-transparent, focus:border-accent). NOT a reinvented primitive per se — it's the only multi-line text input in the partition. Candidate for migration IF shadcn Textarea is added to disk in a future sweep (low priority — 1 site).
- **2 raw `<input type="file">`** in `TemplateToolbar.tsx:46` (and the parallel `VocabToolbar.tsx:73`) — KEEP per Sub-Agent C (native file picker requires raw input; shadcn Input is text-only). NOT reinvented primitives.
- `TemplateListRow` uses `InfoTooltip` (existing shared per Sub-Agent C) for the variable-list tooltip (line 77-85) — CORRECT use of the shared primitive.

**Hardcoded colors / hardcoded text (C-BRAND-1)?**
- Amber-tinted match-mode "contains" badge (line 68: `bg-amber-400/15 text-amber-700 dark:text-amber-400`) + accent-tinted "exact" badge (line 69: `bg-accent/15 text-accent`). Same amber palette as History favorites + VocabDuplicateBanner + VocabListRow test-no-change. Cross-page consistency, NOT a violation.
- No hardcoded "Voice Typer" anywhere (verified).

**i18n violations?**
- **NO.** `useTemplates.ts:179` + `:189-193` correctly use `t("templates.loadFailedDescription")` (BG-62 fix). Every label resolves through `t()`.

**Duplicate code across pages?**
- `useTemplateImportExport.ts` mirrors `useVocabularyImportExport.ts` in structure (hidden file input ref + doExport + handleImportFile + handleImportClick). The two hooks are NOT duplicates — they handle different data shapes (Template vs VocabularyEntry) and different IPC bridges (`exportTemplates` vs `exportVocabulary`). Could be unified via a generic `useImportExport<T>` hook — out of scope.
- `TemplateToolbar` + `VocabToolbar` both render hidden `<input type="file">` + Import button + `ExportFormatMenu` + Add button — structural similarity but different button labels + different Add behavior (Vocab opens inline quick-add; Templates opens Modal). NOT a duplication.

**Recommendation: KEEP CUSTOM** — no reinvented primitives (the raw `<textarea>` is the only candidate, and shadcn Textarea is NOT on disk). All shared primitives used correctly.

### 1.4 Models page (`pages/Models.tsx`)

**File summary**
- `pages/Models.tsx` (265 LOC) — thin composition root.
- Imports from `components/models/` (Sub-Agent C's partition): `CloudProvidersPanel`, `LocalModelsPanel`, `ModelCardActions`, `DownloadProgressBar`, `FamilyLogo`.
- Imports `useModelLifecycle` from `hooks/` (shared).
- Imports `_tabBarStyles` shared constants.

**Page purpose**: ASR model management — Local models tab (family cards + accordion + download/select/delete + disk-space + consent gate) + Cloud providers tab (API key + test + consent per provider).

**Custom components used**
- Shared: `ConfirmDialog`, `LastUpdatedIndicator`, `PageHeading`, `Spinner`.
- shadcn primitives: `Button`, `SegmentedControl variant="tabs"` (line 96-107).
- From `components/models/` (Sub-Agent C): `CloudProvidersPanel`, `LocalModelsPanel`, `ModelCardActions`, `DownloadProgressBar`, `FamilyLogo`.

**Reinvented primitives?**
- **NO.** Confirms Sub-Agent C's finding: all models components already use shadcn primitives correctly (Button, Accordion, Input, Switch, KeyringStatusBadge). `DownloadProgressBar` is a hand-rolled `<div role="progressbar">` (shadcn Progress NOT on disk — KEEP CUSTOM per Sub-Agent C).
- Confirms Sub-Agent A's finding: `SegmentedControl variant="tabs"` is used here AND in Settings.tsx (2 sites total). Sub-Agent A recommended moving Settings tabs into the Sidebar; if that proceeds, Models becomes the SOLE `variant="tabs"` consumer.

**Hardcoded colors / hardcoded text (C-BRAND-1)?**
- `border-accent bg-(--bg-subtle)` for the LocalModelsPanel no-model banner (line 168). Uses `--bg-subtle` token — NOT a violation.
- No hardcoded "Voice Typer" anywhere (verified — the only "Voice Typer" hit is in `Models.tsx:241` inside a comment block referencing `VoiceTyperService.delete_model`, which is the Python backend class name — C-BRAND-1 explicitly EXEMPTS "internal identifiers (types like `VoiceTyperConfig`, mutex/binary names like `VoiceTyperSingleInstance` / `VoiceTyper.exe`)" — `VoiceTyperService` is the same category).

**i18n violations?**
- **NO.** Every label resolves through `t()`.

**Duplicate code across pages?**
- The `_tabBarStyles.ts` constants (co-consumed by Settings + Models) — if Settings's tab UI is relocated per Sub-Agent A, the file becomes a one-consumer helper. NOT a duplication today.

**Recommendation: KEEP CUSTOM** — no reinvented primitives, all shared primitives used correctly, the 2-tab SegmentedControl + ConfirmDialog-for-delete pattern is correct. Coordinate with Sub-Agent A on the `_tabBarStyles.ts` future if Settings-tab-relocation proceeds.

### 1.5 Microphone page (`pages/Microphone.tsx` + `pages/microphone/**`)

**File summary**
- `pages/Microphone.tsx` (282 LOC) — thin composition root.
- `pages/microphone/components/`: MicrophonePermissionBanner (88 LOC), AvailableMicrophonesList (121 LOC), ActiveMicrophoneCard (408 LOC).
- `pages/microphone/hooks/`: useMicrophoneData, useMicrophonePermission (74 LOC), useMicrophoneTest, useMicrophoneLevelMonitor, useMicrophonePlayback, useMicrophoneTestSession.
- `pages/microphone/lib/`: buildTestFilters, computeAudioKey, types.

**Page purpose**: microphone selection + 3-30s test recording + post-test review (quality metrics + playback enhanced/original) + audio-preset selector (full AudioFilterChain).

**Custom components used**
- Shared: `PageHeading`, `EmptyState`, `OfflinePackPreparingBanner`, `LastUpdatedIndicator`, `Spinner`, `MicrophoneListItem`, `AudioPresetSelector`, `TestReviewPanel` (all from Sub-Agent C's partition), `RangeSlider`, `LevelBar`, `LiveQualityFeedback`.
- shadcn primitives: `Button`.
- Partition-specific: MicrophonePermissionBanner, AvailableMicrophonesList, ActiveMicrophoneCard.

**Reinvented primitives?**
- **NO.** `ActiveMicrophoneCard` uses `RangeSlider` (with `deferApply`), `LevelBar`, `LiveQualityFeedback`, `AudioPresetSelector`, `TestReviewPanel` — all existing shared per Sub-Agent B + C. The card itself is a bespoke `border-accent bg-(--bg-subtle)` container (shadcn Card NOT on disk — consistent with the project's list/card surface language).
- `MicrophonePermissionBanner` uses raw `<a href={deepLink}>` for the OS-settings link (line 73-85) — NOT a shadcn Button-as-link. Acceptable: the deep-link URL scheme (`x-apple.systempreferences:` + `ms-settings:privacy-microphone`) needs a real `<a>` for the OS handler; shadcn Button `asChild` would also work but adds nothing.
- `AvailableMicrophonesList` uses native `<ul>/<li>` semantics (line 64-118) — correct, no role attributes needed (biome's `noRedundantRoles` rule).

**Hardcoded colors / hardcoded text (C-BRAND-1)?**
- Amber-tinted "filters changed" notice (line 273: `bg-amber-500/10 border-amber-500/20 text-amber-700 dark:text-amber-500`) — NOTE: uses `amber-500` while History/Vocabulary use `amber-400`. Cross-page consistency opportunity (amber 400 vs 500 mix — minor polish, not a violation).
- `text-amber-700 dark:text-amber-500` pattern repeats across History/Vocabulary/Microphone — could be a shared `amber-warning` Tailwind utility in a future wave (out of scope).

**i18n violations?**
- **YES — 1 violation.** `pages/microphone/hooks/useMicrophoneData.ts:161` has `setLoadError(err instanceof Error ? err.message : "Failed to load microphone data")` — hardcoded English fallback string. Sibling hooks `useVocabulary.ts:222` + `useTemplates.ts:189` were both fixed in session BG-62 to use `t("*.loadFailedDescription")`. `useHistoryCache.ts:251` falls back to `String(err)` (raw error — not localized, not hardcoded English). `useMicrophoneData.ts:161` was MISSED in the BG-62 sweep. The localized key `microphone.loadFailedDescription` does NOT exist in any locale file yet.

**Duplicate code across pages?**
- The `callRef` + `markUpdatedRef` ref-mirror pattern (for stable callback identity under test mocks that return fresh `call` per render) is shared across `useHistoryCache.ts:110-117` + `useVocabulary.ts:144-147` + `useTemplates.ts:66-69` + `useMicrophoneData.ts` — DRY across all 4 list pages. NOT a duplication; the pattern is documented in each hook's docstring.

**Recommendation: KEEP CUSTOM + 1 REFACTOR** — no reinvented primitives. REFACTOR: fix the C-I18N-1 violation in `useMicrophoneData.ts:161` by adding `microphone.loadFailedDescription` to all 8 locale files + replacing the hardcoded string with `t("microphone.loadFailedDescription")`.

### 1.6 About page (`pages/About.tsx`)

**File summary**
- `pages/About.tsx` (118 LOC) — static info page.
- Imports `APP_NAME` from `@/branding` (line 20), `pkg.version` from `package.json` (line 25).

**Page purpose**: product identity card (logo + name + tagline + Local-vs-Cloud capability split + Version + Platforms).

**Custom components used**
- Shared: `PageHeading`, `ReadonlyRow`, `Logo`.
- shadcn primitives: NONE (pure static content).
- hugeicons: `CloudIcon`, `Mic02Icon`.

**Reinvented primitives?**
- **NO.** Custom `rounded-xl border border-border/10 bg-(--bg-subtle)` card container (line 46) — shadcn Card NOT on disk per Sub-Agent B. Consistent with the project's surface language.

**Hardcoded colors / hardcoded text (C-BRAND-1)?**
- None. `APP_NAME` imported + used at line 52 + 107 (via `t("about.versionValue", {version: APP_VERSION})`). `t("about.productTagline")` for the tagline. NO literal "Voice Typer" anywhere.

**i18n violations?**
- **NO.** Every label via `t()`. `useT()` re-render on locale switch (line 34).

**Duplicate code across pages?**
- None.

**Recommendation: KEEP CUSTOM** — static info page, no reinvented primitives.

### 1.7 Privacy page (`pages/Privacy.tsx`)

**File summary**
- `pages/Privacy.tsx` (129 LOC) — static privacy disclosure.
- `PRIVACY_TOPICS` array (line 35-57) drives the 5 disclosure rows.

**Page purpose**: 5-row privacy disclosure (Audio Processing, Model Weights, Cloud ASR, Voice Biometrics, Local Data) with config-dir interpolation from `get_status`.

**Custom components used**
- Shared: `PageHeading`.
- shadcn primitives: NONE.
- hugeicons: `Mic02Icon`, `Layers01Icon`, `CloudIcon`, `Shield01Icon`, `DatabaseIcon`.

**Reinvented primitives?**
- **NO.** Custom `divide-y divide-border/10 rounded-xl border border-border/10 bg-(--bg-subtle)` list container (line 103) — shadcn Card NOT on disk. Consistent with the project's list surface language.

**Hardcoded colors / hardcoded text (C-BRAND-1)?**
- **YES — 1 minor violation (comment only).** Line 1: `// Privacy page — how Voice Typer handles audio and data.` — the literal "Voice Typer" appears in a prose comment. C-BRAND-1 explicitly says "Prose comments describing the app must also avoid the literal brand." About.tsx:1-3 avoids this by referring to "product identity" — Privacy.tsx:1 should follow the same pattern. The file does NOT currently import `APP_NAME`; the comment fix is a 1-line edit (no code change required for the comment — the file doesn't need `APP_NAME` import because no runtime-rendered string contains the brand).

**i18n violations?**
- **NO.** Every label via `t()`. `useT()` re-render on locale switch (line 69). Config-dir interpolation via `t(topic.desc, {configDir})` (line 119-122).

**Duplicate code across pages?**
- None.

**Recommendation: KEEP CUSTOM + 1 REFACTOR** — REFACTOR: reword the line-1 comment to avoid the literal "Voice Typer" (e.g., "// Privacy page — how the app handles audio and data." or "// Privacy page — data-handling disclosure."). No source code change, no test impact.

### 1.8 ConnectionStatusScreen (`components/layout/ConnectionStatusScreen.tsx`)

**File summary**
- `components/layout/ConnectionStatusScreen.tsx` (182 LOC) — full-screen disconnect/reconnecting/restarting overlay.

**Page purpose**: shown when the Python backend disconnects — 3 status branches (connecting / disconnected / restarting) with state-aware title + description + retry affordance.

**Custom components used**
- Shared: `EmptyState` (variant="error"), `Spinner`.
- shadcn primitives: `Button`.
- hugeicons: `AlertCircleIcon`, `RefreshIcon`.

**Reinvented primitives?**
- **1 hand-rolled `<div role="progressbar">`** for the connecting-progress bar (line 136-150). shadcn Progress NOT on disk per Sub-Agent B. Consistent with the Onboarding (line 309-324) + ModelStep + PrewarmAndUpdates pattern. KEEP CUSTOM — adding shadcn Progress for ONE site is not justified; if a future sweep adds shadcn Progress to disk, all 4 sites (Onboarding + ModelStep + PrewarmAndUpdates + ConnectionStatusScreen) should be migrated in one wave (out of scope for this audit).
- `<output aria-live="polite">` wrapper around `<Spinner />` (line 124-130) — restores the implicit live-region announcement the Spinner default used to provide. Sub-Agent B noted Spinner's default root was changed from `<output>` to `<span role="img">` to avoid unwanted "Loading" announcements on every page; this is the ONE place where the live region is restored explicitly. NOT a reinvented primitive — it's a wrapper element.

**Hardcoded colors / hardcoded text (C-BRAND-1)?**
- None. `--bg-subtle` + `--fg-subtle` + `bg-primary` token usage. No literal "Voice Typer".

**i18n violations?**
- **NO.** Every label via `t()` (mocked in test as `(key) => key`). `useT()` hook used (line 45).

**Duplicate code across pages?**
- The hand-rolled `<div role="progressbar">` pattern matches Onboarding + ModelStep + PrewarmAndUpdates — 4 sites total in the renderer. Sub-Agent B already flagged this as a future-wave consolidation opportunity (out of scope).

**Recommendation: KEEP CUSTOM** — the hand-rolled progressbar is consistent with the project pattern. The EmptyState + Spinner + Button reuse is correct. The auto-focus-on-disconnect effect (line 52-61) is a WCAG 2.4.3 Focus Order Level A compliance fix.

### 1.9 `_tabBarStyles.ts` (`pages/_tabBarStyles.ts`)

**File summary**
- 82 LOC. Two exported constants: `tabPageHeaderClassName` (sticky wrapper) + `tabPageIndicatorClassName` (SegmentedControl indicator override).

**Page purpose**: single source of truth for the sticky-tab-bar visual treatment shared between Settings + Models (the two pages that use `SegmentedControl variant="tabs"`).

**Custom components used**
- None — pure className constants.

**Reinvented primitives?**
- **NO.** Not a primitive; a shared-style module.

**Hardcoded colors / hardcoded text (C-BRAND-1)?**
- None. `bg-(--bg-subtle)` + `border-border/10` + `bg-(--bg)` + `border-border/75` token usage.

**i18n violations?**
- N/A (no user-facing text).

**Duplicate code across pages?**
- Co-consumed by `pages/Settings.tsx` (Sub-Agent A's partition — line 39-41, 436, 449) AND `pages/Models.tsx` (this partition — line 47-49, 94, 102). If Sub-Agent A's Settings-tab-relocation recommendation proceeds, Models becomes the sole consumer → the file collapses to a one-call-site helper → can be inlined into Models.tsx OR kept as-is (2-line constant file, no harm either way). Flagged for Sub-Agent A's implementation wave.

**Recommendation: KEEP CUSTOM** — the file is correct as-is. Coordinate with Sub-Agent A on the future.

## 2. Cross-page patterns

### 2.1 Lists/tables
- **NO semantic `<table>` data displays in this partition.** Verified via ripgrep (`<table` → 0 matches in `pages/`).
- Vocabulary: 2-column flat list (`VocabListHeader` + `VocabListRow` × N) using CSS Grid `grid-cols-[auto_minmax(0,1fr)_minmax(0,1fr)_6.25rem]` for sm+ alignment. The fixed `6.25rem` actions column (line 14-21 of `VocabListHeader.tsx`) ensures the header's short "Actions" label aligns with the rows' icon buttons — a bespoke layout invariant, NOT a use case for shadcn Table.
- History: vertical `ActivityList` (shared per Sub-Agent B).
- Templates: vertical `<ul>` of `TemplateListRow`.
- Microphone: vertical `<ul>` of `MicrophoneListItem` (shared per Sub-Agent C) + bespoke "Use System Default" row.
- About: static capability cards (Local + Cloud) + meta rows via `ReadonlyRow`.
- Privacy: 5-row divide-y list.
- Models: tabbed (SegmentedControl) → LocalModelsPanel uses shadcn Accordion for family cards (per Sub-Agent C) + CloudProvidersPanel uses vertical card stack.
- **shadcn Table fit assessment**: shadcn Table is a styled native `<table>` wrapper for tabular data (header row + data rows + columns). NONE of the partition's lists are tabular — Vocabulary is the closest (2 labeled columns) but it's a list-with-row-actions, not a data table. The `6.25rem` fixed-actions-column alignment invariant would be lost with shadcn Table's default `<th>`/`<td>` layout (and the row's checkbox + 2 text columns + 3 action buttons don't fit the table model cleanly). **Recommendation: KEEP CUSTOM** for all list patterns.

### 2.2 Pagination
- **NO multi-page pagination in this partition.** Verified via ripgrep (`Pagination|currentPage|pageCount` → 0 matches in `pages/`).
- History: cursor-based (keyset) infinite-scroll "Load More" button (`useHistoryCache.ts:259-295`) — NOT page-based pagination.
- Vocabulary: soft DISPLAY_CAP=200 + "Show more" button (`Vocabulary.tsx:413-421`) — increments displayCount by 200, NOT page-based.
- Templates + About + Privacy + Models + Microphone + ConnectionStatusScreen: render unbounded lists (no pagination).
- **shadcn Pagination fit assessment**: shadcn Pagination is a `<nav>`-based Link-component for navigating between numbered pages. NONE of the partition's lists use page-based pagination — Vocabulary's "Show more" is incremental, History's "Load More" is cursor-based. **Recommendation: KEEP CUSTOM** for all pagination patterns (i.e., no migration needed).

### 2.3 Tooltips consistency
- **InfoTooltip** (existing shared per Sub-Agent C): used by `TemplateListRow.tsx:77-85` for the variable-list tooltip. 1 site in this partition.
- **HotkeyTooltip** (existing shared per Sub-Agent C): NOT used by any page in this partition (used only by Sidebar + TitleBar per Sub-Agent A).
- **shadcn Tooltip** (direct): NOT used by any page in this partition (used internally by InfoTooltip + HotkeyTooltip + KeyringStatusBadge + ThemeSettingsSection per Sub-Agent C).
- VocabListRow's Test + Delete + Edit icon buttons use NO tooltips (line 184-248 — by design: "hover tooltips rendered over the adjacent icons while moving the cursor between them, and the shapes + aria-labels carry the meaning"). The `aria-label` is the accessible name; the visible icon is the visual cue. CORRECT ARIA pattern — no tooltip needed.
- TemplateListRow's Delete + Edit icon buttons use `title=` attribute (line 98, 112) — native HTML tooltip, NOT shadcn Tooltip. Inconsistent with VocabListRow (which uses no tooltips at all). Minor consistency opportunity: either add native `title=` to VocabListRow's icon buttons for parity, OR remove `title=` from TemplateListRow's icon buttons (the `aria-label` is sufficient). NOT a violation — both patterns are accessible (aria-label is the canonical accessible name; `title=` is a fallback native tooltip).

### 2.4 Modal usage
- **shadcn Dialog** (existing shared per Sub-Agent C): used by `ShareStatsDialog` (Sub-Agent B's partition) — 1 direct Dialog site.
- **Modal wrapper** (existing shared per Sub-Agent C, wraps Dialog): used by `TemplateDialog.tsx:68-165` — 1 site in this partition.
- **ConfirmDialog wrapper** (existing shared per Sub-Agent C, wraps AlertDialog): used by `History.tsx:513-528` (Clear All confirm) + `Vocabulary.tsx:453-463` (Clear All confirm with `dismissOnBackdrop`) + `Models.tsx:251-262` (delete-model confirm with `variant="destructive"`). 3 sites in this partition. Total across the renderer per Sub-Agent C: 6 sites (Settings + PrivacySettingsSection + Models + Onboarding + History + Vocabulary).
- The 3 ConfirmDialog sites in this partition all use the wrapper correctly: `variant="destructive"` for destructive actions (History Clear All, Vocabulary Clear All, Models delete-model), `dismissOnBackdrop` opt-in for Vocabulary (the least-destructive of the three — Clear All is recoverable via Undo toast, so backdrop-dismiss is acceptable). History Clear All does NOT use `dismissOnBackdrop` (also recoverable via Undo, but the action is more destructive — wiping ALL history). Models delete-model does NOT use `dismissOnBackdrop` (hard `shutil.rmtree`, no Undo per `docs/ux/model-delete-rationale.md`).
- **Recommendation: KEEP CUSTOM** — the Modal + ConfirmDialog abstractions are correct (Sub-Agent C). No direct-Dialog sites in this partition.

### 2.5 Skeletons
- **NO page-level Skeleton in this partition.** All loading states use `Spinner` (existing shared per Sub-Agent C): `History.tsx:411` + `History.tsx:493` (inline in Load More button) + `Vocabulary.tsx:252` + `Templates.tsx:86` + `Models.tsx:81` + `Microphone.tsx:161` + `ConnectionStatusScreen.tsx:129`.
- shadcn Skeleton NOT on disk per Sub-Agent B. The project-wide `animate-pulse` Tailwind utility is used in `DashboardSkeleton` (Sub-Agent B's partition) + `SettingsSkeleton` + `MicToggleButton` + `RecordingStatusPill` + `BubbleVisualizer` + `BubbleModeContent` + `App.tsx` + `ActiveMicrophoneCard.tsx:185` (Stop button pulse). The ActiveMicrophoneCard Stop-button `animate-pulse` (line 185) is the only `animate-pulse` site in this partition — NOT a skeleton, it's a "recording in progress" pulse on the Stop button. Correct usage.
- **Recommendation: KEEP CUSTOM** — no skeleton primitives needed in this partition. If a future wave adds `skeleton.tsx` to disk (per Sub-Agent B's flagged future-wave opportunity), this partition has 0 sites to migrate.

### 2.6 Empty states
- **EmptyState** (existing shared per Sub-Agent C, with `variant="info"|"error"` + `role="status"|"alert"` + tinted ring on error + `actionRef` for programmatic focus): used CONSISTENTLY across all 4 list pages in this partition:
  - `History.tsx:420-427` (load-error, `variant="error"`) + `:429-456` (genuine empty / search-no-results / favorites-no-results, `variant="info"` default).
  - `Vocabulary.tsx:264-273` (load-error, `variant="error"`) + `:350-358` (genuine empty, `variant="info"`) + `:358-366` (search-no-results, `variant="info"`).
  - `Templates.tsx:105-114` (load-error, `variant="error"`) + `:150-158` (genuine empty, `variant="info"`) + `:163-169` (search-no-results, `variant="info"`).
  - `Microphone.tsx:180-188` (load-error, `variant="error"`) — no genuine-empty state (the page always renders the ActiveMicrophoneCard; an empty-microphones list delegates to `AvailableMicrophonesList`'s internal EmptyState at `:42-49`).
  - `ConnectionStatusScreen.tsx:99-111` (always `variant="error"` — the disconnect IS the error state).
  - `About.tsx` + `Privacy.tsx` + `Models.tsx`: NO EmptyState (always render content).
- **Recommendation: KEEP CUSTOM** — EmptyState usage is consistent across all 4 list pages (load-error → `variant="error"`; genuine empty / search-no-results → `variant="info"`). No per-page reinvention.

## 3. Component decisions summary

| Component | File | Decision | Rationale (one line) |
|---|---|---|---|
| History page | `pages/History.tsx:48` | **KEEP CUSTOM** | thin composition root; delegates to shared primitives + ActivityList + useHistoryCache/Export; cursor pagination is product-specific |
| useHistoryCache | `pages/history/hooks/useHistoryCache.ts:88` | **KEEP CUSTOM** | cursor (keyset) pagination via before_timestamp/before_id + OFFSET fallback; ref mirrors for stable callback identity |
| useHistoryExport | `pages/history/hooks/useHistoryExport.ts:57` | **KEEP CUSTOM** | filter-aware paging loop + window_.exportHistory IPC bridge; EXPORT_MAX_ROWS=10000 cap |
| historySort | `pages/history/utils/historySort.ts:13` | **KEEP CUSTOM** | pure Intl.Collator helper; locale-aware via getLocale() |
| Vocabulary page | `pages/Vocabulary.tsx:57` | **KEEP CUSTOM** | thin composition root; delegates to 7 vocabulary components + 5 hooks; inline add/edit (no modal) |
| VocabToolbar | `pages/vocabulary/components/VocabToolbar.tsx:53` | **KEEP CUSTOM** | hidden `<input type="file">` (KEEP per Sub-Agent C) + shadcn Button cluster + ExportFormatMenu |
| VocabSearchFilterBar | `pages/vocabulary/components/VocabSearchFilterBar.tsx:38` | **KEEP CUSTOM** | SearchField + shadcn Select with hideChevron + sort glyph |
| VocabInlineForm | `pages/vocabulary/components/VocabInlineForm.tsx:45` | **KEEP CUSTOM** | shadcn Input × 2 + shadcn Button (Save/Cancel) + role="alert" for inline error |
| VocabListHeader | `pages/vocabulary/components/VocabListHeader.tsx:41` | **KEEP CUSTOM** | shadcn Checkbox with indeterminate + grid layout matching VocabListRow |
| VocabListRow | `pages/vocabulary/components/VocabListRow.tsx:76` | **KEEP CUSTOM** | memo'd; shadcn Checkbox + 3 shadcn Buttons (Test/Delete/Edit, Edit rightmost); inline test result with role="status" |
| VocabBulkBar | `pages/vocabulary/components/VocabBulkBar.tsx:41` | **KEEP CUSTOM** | sticky floating bar; shadcn Button + shadcn DropdownMenu + raw `<button>` for Deselect (bespoke icon button) |
| VocabDuplicateBanner | `pages/vocabulary/components/VocabDuplicateBanner.tsx:24` | **KEEP CUSTOM** | amber-tinted banner with role="status"; shadcn Button + raw `<button>` for Dismiss |
| useVocabulary | `pages/vocabulary/hooks/useVocabulary.ts:82` | **KEEP CUSTOM** | entries/loading/loadError state + ref mirrors; instantDeleteEntry with 6s undoable toast (D2-FIX); loadError fallback uses t() correctly (BG-62) |
| useVocabularyQuickAdd | `pages/vocabulary/hooks/useVocabularyQuickAdd.ts:56` | **KEEP CUSTOM** | inline quick-add state + isDuplicateEntryError discriminator |
| useVocabularyEdit | `pages/vocabulary/hooks/useVocabularyEdit.ts:53` | **KEEP CUSTOM** | inline edit state; splices in place preserving _id |
| useVocabularySelection | `pages/vocabulary/hooks/useVocabularySelection.ts:39` | **KEEP CUSTOM** | bulk selection Set + bulkDeleteSelected with 6s undoable toast (mirrors instantDeleteEntry) |
| useVocabularyImportExport | `pages/vocabulary/hooks/useVocabularyImportExport.ts:51` | **KEEP CUSTOM** | hidden `<input type="file">` + window_.exportVocabulary IPC bridge + parseImportedVocabulary (JSON/CSV) |
| vocabulary/lib/{transform,sort,categories,importExport} | `pages/vocabulary/lib/*.ts` | **KEEP CUSTOM** | pure helpers; mirror backend _normalize_wrong_phrase per cross-language contract |
| Templates page | `pages/Templates.tsx:32` | **KEEP CUSTOM** | thin composition root; delegates to 4 templates components + 3 hooks |
| TemplateDialog | `pages/templates/components/TemplateDialog.tsx:38` | **KEEP CUSTOM** | uses shared Modal + ModalFooter (wraps Dialog); shadcn Input + shadcn Select + raw `<textarea>` (shadcn Textarea NOT on disk — 1 site, low priority for migration) |
| TemplateToolbar | `pages/templates/components/TemplateToolbar.tsx:34` | **KEEP CUSTOM** | hidden `<input type="file">` (KEEP per Sub-Agent C) + shadcn Button + ExportFormatMenu |
| TemplateListRow | `pages/templates/components/TemplateListRow.tsx:38` | **KEEP CUSTOM** | memo'd; shadcn Button × 2 (Delete/Edit) + InfoTooltip for variable list; match-mode badge |
| TemplateSearchSortBar | `pages/templates/components/TemplateSearchSortBar.tsx:29` | **KEEP CUSTOM** | SearchField + shadcn Select (identical pattern to VocabSearchFilterBar) |
| useTemplates | `pages/templates/hooks/useTemplates.ts:57` | **KEEP CUSTOM** | templates/loading/loadError state + ref mirrors; one-time localStorage→backend migration; instantDeleteTemplate with 6s undoable toast (D2-FIX) |
| useTemplateDialog | `pages/templates/hooks/useTemplateDialog.ts:50` | **KEEP CUSTOM** | add/edit dialog state; duplicate-trigger guard |
| useTemplateImportExport | `pages/templates/hooks/useTemplateImportExport.ts:50` | **KEEP CUSTOM** | hidden `<input type="file">` + window_.exportTemplates IPC bridge + parseImportedTemplates |
| templates/lib/{types,storage,transform,sanitize} | `pages/templates/lib/*.ts` | **KEEP CUSTOM** | pure helpers; sanitize is NUL-only (preserves user data) |
| Models page | `pages/Models.tsx:51` | **KEEP CUSTOM** | thin composition root; SegmentedControl variant="tabs" (2nd of 2 sites — Settings is the other); ConfirmDialog for delete-model (hard delete, no Undo per docs/ux/model-delete-rationale.md) |
| Microphone page | `pages/Microphone.tsx:31` | **KEEP CUSTOM** | thin composition root; meterRef for rAF direct-DOM level writes; hasAttempted gates the OfflinePackPreparingBanner |
| MicrophonePermissionBanner | `pages/microphone/components/MicrophonePermissionBanner.tsx:25` | **KEEP CUSTOM** | platform-aware (macOS/Windows deep-link URL schemes; Linux has none); raw `<a href={deepLink}>` for OS-settings link |
| AvailableMicrophonesList | `pages/microphone/components/AvailableMicrophonesList.tsx:35` | **KEEP CUSTOM** | native `<ul>/<li>` semantics; EmptyState when zero microphones; shadcn Button per row |
| ActiveMicrophoneCard | `pages/microphone/components/ActiveMicrophoneCard.tsx:81` | **KEEP CUSTOM** | RangeSlider with deferApply + LevelBar + LiveQualityFeedback + memoised TestReviewPanel + memoised AudioPresetSelector (custom comparators) |
| useMicrophoneData | `pages/microphone/hooks/useMicrophoneData.ts` | **REFACTOR** | C-I18N-1 violation at line 161 — hardcoded English fallback `"Failed to load microphone data"`; sibling hooks useVocabulary/useTemplates were fixed in BG-62; add `microphone.loadFailedDescription` to all 8 locale files + replace the hardcoded string with `t("microphone.loadFailedDescription")` |
| useMicrophonePermission | `pages/microphone/hooks/useMicrophonePermission.ts:21` | **KEEP CUSTOM** | navigator.permissions.query + addEventListener cleanup (fix for onchange leak) |
| useMicrophoneTest/LevelMonitor/Playback/TestSession | `pages/microphone/hooks/*.ts` | **KEEP CUSTOM** | deeply product-specific (rAF level loop + visibility gates + microphone_test_complete push-event subscription) |
| microphone/lib/{buildTestFilters,computeAudioKey,types} | `pages/microphone/lib/*.ts` | **KEEP CUSTOM** | pure helpers |
| About page | `pages/About.tsx:32` | **KEEP CUSTOM** | static info; APP_NAME import (C-BRAND-1 satisfied); APP_VERSION from package.json; ReadonlyRow + Logo shared |
| Privacy page | `pages/Privacy.tsx:59` | **KEEP CUSTOM + 1 REFACTOR** | REFACTOR: reword line-1 comment to avoid literal "Voice Typer" (C-BRAND-1 minor violation in prose comment); no source code change, no test impact |
| _tabBarStyles.ts | `pages/_tabBarStyles.ts:58,82` | **KEEP CUSTOM** | 2 constant exports co-consumed by Settings + Models; if Sub-Agent A's Settings-tab-relocation proceeds, Models becomes sole consumer (coordinate) |
| ConnectionStatusScreen | `components/layout/ConnectionStatusScreen.tsx:39` | **KEEP CUSTOM** | EmptyState variant="error" + Spinner wrapped in `<output aria-live="polite">` (restores implicit live region); hand-rolled `<div role="progressbar">` (shadcn Progress NOT on disk — consistent with Onboarding+ModelStep+PrewarmAndUpdates); auto-focus Retry on disconnect (WCAG 2.4.3) |

**Summary**: 0 REPLACE, 2 REFACTOR (useMicrophoneData i18n fix + Privacy.tsx:1 comment fix), 0 REMOVE, 0 REFACTOR-CUSTOM-USING-EXISTING-PRIMITIVE beyond the 2 fixes, 40+ KEEP CUSTOM.

## 4. Risks & gotchas

### AGENTS.md constraints touching this scope
- **C-I18N-1 (one violation found)**: `pages/microphone/hooks/useMicrophoneData.ts:161` has hardcoded English `"Failed to load microphone data"` fallback. Sibling hooks `useVocabulary.ts:222` + `useTemplates.ts:189` were fixed in BG-62; this site was missed. REFACTOR requires adding `microphone.loadFailedDescription` to all 8 locale files (en/ar/de/es/fr/hi/ru/zh) per C-I18N-1 + C-I18N-2 (genuine translation, not English pasted under a non-English key).
- **C-BRAND-1 (one minor violation found)**: `pages/Privacy.tsx:1` has literal "Voice Typer" in a prose comment. The rule explicitly covers prose comments. About.tsx:1-3 avoids the literal via "product identity" wording. REFACTOR is a 1-line comment edit (no code change).
- **E12 (never downgrade behavior)**: every recommendation preserves existing observable behavior. The 2 REFACTOR findings are bug fixes that bring the partition INTO compliance with AGENTS.md constraints — they don't change observable UX for end users (only the localised fallback string when a non-Error rejection is caught).
- **E15 (technical debt)**: 0 dead code in this partition. The 3 raw `<input type="checkbox">` sites in Onboarding (Sub-Agent B's partition) are NOT in this scope. The 2 raw `<input type="file">` sites (VocabToolbar + TemplateToolbar) are intentional (native file picker). The 1 raw `<textarea>` (TemplateDialog) is a candidate for migration IF shadcn Textarea is added to disk (NOT recommended for this audit — 1 site, low value). The 3 raw `<button>` instances (VocabListRow Retry + VocabToolbar Show-more + VocabBulkBar/VocabDuplicateBanner Dismiss ×) are bespoke pill buttons consistent with the page's design language — NOT duplicates.
- **W0 (web-search first)**: satisfied — shadcn Table + Pagination APIs verified via z-ai web_search against the official docs URLs BEFORE the KEEP-CUSTOM recommendations. Neither is on disk per Sub-Agent B's inventory; neither is a fit for any current page in this partition.
- **W2 (prefer existing libraries)**: every KEEP-CUSTOM recommendation justified by a real Voice Typer constraint — shadcn Table NOT on disk + no semantic tabular data; shadcn Pagination NOT on disk + no multi-page pagination; shadcn Textarea NOT on disk + 1 site; shadcn Card NOT on disk + bespoke `bg-(--bg-subtle)` is the project's surface language; shadcn Progress NOT on disk + hand-rolled progressbar consistent across 4 sites; shadcn Skeleton NOT on disk + 0 page-level skeletons in this partition. NOT mechanical deferral.
- **C-STYLE-1 noted**: pre-existing session-prefix anchors in source comments (R7-F8, D2-FIX, BG-62, BG-63, NH-1, NH-15, NH-28, NH-29, ZU-30, BG-59, F2, F4, F11-FIX, IPD-1, etc.) are LEGACY from sessions that pre-date C-STYLE-1's codification. They reference fix histories documented in worklog.md/SUMMARY.md. New code added by this audit would not introduce new prefixes; the existing ones are flagged as legacy and should be cleaned up in a separate sweep (out of scope).
- **C-ARCH-1 noted**: History.tsx (532 LOC) + Vocabulary.tsx (466 LOC) are over the ≤~300 line soft limit. Both are already thin composition roots that delegate to extracted `./history/**` + `./vocabulary/**` packages — the LOC count is inflated by extensive inline documentation comments explaining the D2-FIX + R7-F13 + cursor-pagination contracts. A further split is possible but offers no behavior gain — out of scope.
- **C-TEST-5 noted**: every test lives in a separate `__tests__/` folder (22 in `pages/__tests__/` for this partition + 6 in `pages/history/hooks/__tests__/` + 6 in `pages/vocabulary/__tests__/` + 4 in `pages/templates/components/__tests__/` + 6 in `pages/microphone/hooks/__tests__/`). No inline test blocks in production source.

### Tests that will need updating if migrations proceed
- **REFACTOR wave 1 — `useMicrophoneData.ts:161` i18n fallback fix** (1 site):
  - Add `microphone.loadFailedDescription` key to all 8 locale files (en/ar/de/es/fr/hi/ru/zh).
  - Replace `"Failed to load microphone data"` with `t("microphone.loadFailedDescription")` at line 161.
  - Extend `pages-improvements.test.tsx:580-582` (the existing "no hardcoded `Loading…`" source-scan assertion) to also assert no hardcoded `"Failed to load microphone data"` in the renderer source — OR add a parallel assertion that the source uses `t("microphone.loadFailedDescription")`.
  - Add a new test (mirror `Vocabulary-bg60-bg62.test.tsx`'s pattern) that mounts the Microphone page with `mockCall.mockRejectedValue("backend exploded")` (a non-Error rejection) and asserts the load-error EmptyState surfaces the localised `microphone.loadFailedDescription` string.
  - Test risk: LOW (1 hook + 8 locale files + 1 test extension + 1 new test).
- **REFACTOR wave 2 — `Privacy.tsx:1` comment brand fix** (1 site):
  - Reword the line-1 comment to avoid the literal "Voice Typer" (e.g., "// Privacy page — how the app handles audio and data.").
  - No source code change, no test impact. Test risk: NONE.

### IPC contract
- NO IPC impact from any recommendation in this audit.
- The partition uses ONLY already-allowlisted IPC commands: `get_history`, `get_favorites`, `search_history`, `get_today_stats`, `delete_history`, `restore_history`, `toggle_favorite`, `clear_history`, `get_vocabulary`, `save_vocabulary`, `get_correction_usage`, `test_vocabulary_correction`, `get_templates`, `save_templates`, `get_microphones`, `get_config`, `set_config`, `microphone_test_start`, `microphone_test_stop`, `select_microphone`, `import_model`, `delete_model`, `get_model_status`, `get_model_catalog`, `get_status`.
- The `_COMMAND_REGISTRY` (server), `ALLOWED_COMMANDS` (main), `PythonRequest`/`PythonPushEvent` (renderer types) unions are UNTOUCHED.
- SEC-002 (`set_config` allowlist) untouched — Vocabulary + Templates persist via `save_vocabulary` + `save_templates` (NOT `set_config`).
- SEC-026 (sandboxed bubble preload) untouched — none of these pages touch the bubble window.
- The `window.window_.exportHistory` / `exportVocabulary` / `exportTemplates` IPC bridges (used by the export hooks) are pre-existing main-process bridges, not server IPC commands — they are NOT in the `_COMMAND_REGISTRY` allowlist and don't need to be.

### File-disjoint implementation-wave grouping
- This audit's partition is INDEPENDENT of A (Sidebar/Settings), B (Dashboard/Bubble/Home/Onboarding), and C (UI primitives + common + feedback + audio + hotkey + consent + help + KeyboardPermissionBanner + models components + microphone components).
- The only OVERLAP is `_tabBarStyles.ts` (co-owned by Settings per A + Models per D) — if A's Settings-tab-relocation recommendation proceeds, Models becomes the sole consumer and the file can be inlined into Models.tsx (or kept as-is — it's only 2 lines of constants). Coordinate with Sub-Agent A's implementation wave.
- The other cross-cutting dependency is the REFACTOR wave 1 (useMicrophoneData i18n fix) which touches Microphone.tsx + 8 locale files — locale files are co-owned by all sub-agents (any new i18n key needs to be added to all 8), but the actual code change is localised to `useMicrophoneData.ts` (this partition's exclusive file).
- The 4-site hand-rolled `<div role="progressbar">` pattern (ConnectionStatusScreen + Onboarding + ModelStep + PrewarmAndUpdates) spans Sub-Agent B's partition (Onboarding + ModelStep) + Sub-Agent C's partition (PrewarmAndUpdates) + this partition (ConnectionStatusScreen). IF a future wave adds shadcn Progress to disk, all 4 sites should be migrated in one coordinated sweep — out of scope for THIS audit.
- The 3-site raw `<input type="checkbox">` pattern (Onboarding.tsx:443 + ModelStep.tsx:273 + ModelStep.tsx:409) is entirely in Sub-Agent B's partition — Sub-Agent B + C already flagged it as REFACTOR. NOT in this audit's scope.

### Next actions for the orchestrator
1. **Accept** the KEEP-CUSTOM recommendations for all 40+ components in this partition (History, Vocabulary, Templates, Models, Microphone, About, Privacy, ConnectionStatusScreen + their hooks/libs/components).
2. **Approve** the 2 REFACTOR recommendations:
   - Fix C-I18N-1 violation in `useMicrophoneData.ts:161` (add `microphone.loadFailedDescription` to all 8 locale files + replace hardcoded English fallback).
   - Fix C-BRAND-1 minor violation in `Privacy.tsx:1` (reword comment to avoid literal "Voice Typer").
3. **Note for future wave** (out of scope): if a coordinated `skeleton.tsx` / `progress.tsx` / `textarea.tsx` primitive sweep is launched, the partition has 0 skeleton sites, 1 progressbar site (ConnectionStatusScreen — coordinate with B's 3 sites), 1 textarea site (TemplateDialog). Low ROI for the partition; defer until coordinated cross-partition wave.
4. **Note for Sub-Agent A's implementation wave**: if Settings-tab-relocation proceeds, `_tabBarStyles.ts` becomes a Models-only consumer — coordinate the inline-vs-keep decision.
5. **Ready for handoff** to implementation sub-agents.

---
Task ID: 6-a
Agent: impl-onboarding-checkbox
Task: Replace 3 raw `<input type="checkbox">` sites in the Onboarding flow with the existing shadcn Checkbox primitive (already on disk at `voice_typer/client/src/renderer/src/components/ui/checkbox.tsx`).

Files changed (5):
- `voice_typer/client/src/renderer/src/pages/Onboarding.tsx` (partition) — Done-step consent: raw `<input type="checkbox">` at line ~443 → `<Checkbox checked={consentAccepted} onCheckedChange={(v) => handleConsentToggle(v === true)} disabled={consentPersisting} aria-label={...} data-testid="onboarding-consent-checkbox" />`. Added `htmlFor="onboarding-consent-checkbox"` to the wrapping `<label>` + `id="onboarding-consent-checkbox"` to the Checkbox to satisfy Biome's `lint/a11y/noLabelWithoutControl` (it can't statically recognize a custom-component-wrapped Radix `<button role="checkbox">` as a labelable control).
- `voice_typer/client/src/renderer/src/pages/onboarding/components/ModelStep.tsx` (partition) — Same pattern at the two consent sites:
  - HF consent (line ~273): `<Checkbox checked={hfConsent} onCheckedChange={(v) => setHfConsent(v === true)} aria-label={t("onboarding.consentHuggingFace")} data-testid="onboarding-hf-consent" />` inside a `<label htmlFor="onboarding-hf-consent">` wrapper.
  - Cloud consent (line ~409): `<Checkbox checked={cloudConsent} onCheckedChange={(v) => setCloudConsent(v === true)} aria-label={t("models.cloud.consentAria", { provider: providerLabel(cloudProvider) })} data-testid="onboarding-cloud-consent" />` inside a `<label htmlFor="onboarding-cloud-consent">` wrapper.
- `voice_typer/client/src/renderer/src/pages/__tests__/Onboarding.test.tsx` (partition) — Added `vi.mock("@/components/ui/checkbox", ...)` parallel to the existing Select mock at line 59. The mock renders a real `<input type="checkbox">` so the existing `fireEvent.click` + `getByTestId(...) as HTMLInputElement` assertions keep working.
- `voice_typer/client/src/renderer/src/pages/onboarding/__tests__/onboarding-model-step.test.tsx` (partition) — Same `vi.mock("@/components/ui/checkbox", ...)` parallel to its inline Select mock.

PARTITION EXTENSION (E14 regression-prevention override):
- `voice_typer/client/src/renderer/src/pages/onboarding/__tests__/onboarding-fixes.test.tsx` (OUTSIDE the original 4-file partition) — Added the same `vi.mock("@/components/ui/checkbox", ...)`. WHY: this file has 2 tests that assert `screen.getByTestId("onboarding-consent-checkbox") as HTMLInputElement; expect(checkbox.checked).toBe(true)` (lines 543-548 + 754-759). After replacing the raw `<input>` with a Radix `<button role="checkbox">`, `.checked` becomes `undefined` and both tests break. Per AGENTS.md E14 (regression prevention) + E12 (don't downgrade the project), the mock had to be added here too. This is a 5th-file extension of my partition — flagged here for orchestrator visibility. The 5th file is mechanical (same `vi.mock` pattern as the 4 partition files), no production code touched, no other agent's work overwritten. If another in-flight agent's partition later claims this file, the mock addition is forward-compatible (a no-op if their work doesn't touch Checkbox).

Why shadcn Checkbox is a better fit per site (W2):
1. Onboarding.tsx:443 (Done-step voice-biometric consent) — Radix Checkbox provides a tested `aria-checked` state + visible focus-ring + keyboard activation (Space/Enter) out of the box; the raw input relied on browser default styling only.
2. ModelStep.tsx:273 (HuggingFace download consent) — Same focus-ring + a11y-tested checked state; the consent gates an explicit `download_model` IPC call so accessible state announcement matters for screen-reader users.
3. ModelStep.tsx:409 (cloud provider consent) — Same; consent gates the `cloud_*_consent` set_config persistence. Radix Checkbox's `data-[state=checked]:bg-accent` matches the project's `--accent` design token (was previously faked via `accent-accent` Tailwind utility on the raw input, which doesn't apply to all platforms).

Verification:
- Typecheck: `npm run typecheck:ci` (i.e. `tsc -b --force`) → exit 0 for my partition. The single error in the run is in `src/renderer/src/components/layout/__tests__/Sidebar.nav-submenu.test.tsx` (TS6196 'VoiceTyperConfig' is declared but never used) — that's another agent's in-flight UNTRACKED file (git status shows `??`), NOT my partition.
- Tests: ran the 6 onboarding-related test files (Onboarding.test.tsx, Onboarding-render-loop-guard.test.tsx, pages-improvements.test.tsx, client-pages-fixes.test.tsx, onboarding-fixes.test.tsx, onboarding-model-step.test.tsx) → 82 passed, 1 skipped (pre-existing), 0 failed.
- Lint: `npx biome check --write` on the 5 changed files → clean (0 errors after adding `htmlFor`+`id` pairs to satisfy `lint/a11y/noLabelWithoutControl`).

Pre-existing unrelated failures noted (NOT caused by my changes):
- 6 failures in App-level + Settings/useNavigation tests (App-ux-fixes.test.tsx BG-25 document.title, feature-hardening-behavior.test.tsx Settings shared-hook, ux-components-behavior.test.tsx Settings auto-save + Settings onNavigate + useNavigation localStorage). All are from the in-flight Sidebar/Settings/useNavigation refactor (git diff shows `useNavigation.ts`, `Sidebar.tsx`, `routes.ts` modified with `settingsGeneral` route split — another agent's partition, not mine). None of these test files import OnboardingPage or reference the consent checkbox test IDs (verified via ripgrep).

Risks/gotchas:
- Biome's `lint/a11y/noLabelWithoutControl` rule does NOT statically recognize a custom React component (e.g. `<Checkbox>`) as a labelable form control even though its underlying rendered DOM (`<button role="checkbox">`) IS labelable. Workaround: add `htmlFor` on the `<label>` and matching `id` on the wrapped Checkbox. The `id` reuses the same string as `data-testid` for a single source of truth (both attributes can coexist with the same value — they serve different consumers: testing vs a11y/labeling).
- The 3 `id` values (`onboarding-consent-checkbox`, `onboarding-hf-consent`, `onboarding-cloud-consent`) match the existing `data-testid` values — single source of truth. If a future test mocks `getElementById` instead of `getByTestId`, both attributes return the same element.
- Radix Checkbox's `onCheckedChange` returns `boolean | "indeterminate"`. All 3 call sites narrow to `boolean` via `v === true` (the consent states are simple booleans, not tristate). If a future requirement adds an indeterminate "partially consented" state, the narrowing would need to be removed.
- The shadcn Checkbox primitive ships with `disabled:cursor-not-allowed disabled:opacity-50` — passing `disabled={consentPersisting}` on the Done-step consent produces the disabled visual state automatically. The raw input didn't have this.
- Removed `accent-accent` Tailwind utility (the shadcn primitive applies `--accent` via `data-[state=checked]:bg-accent`); removed `size-4` (the primitive already ships `size-4 shrink-0`); kept `mt-0.5` for layout offset parity + `cursor-pointer` for UX (the primitive's default cursor on a `<button>` is `default`).

---
Task ID: 5+6+7
Agent: orchestrator (main) — final implementation + regression

Work Log:
- Phase 6.1: Extended `Page` union with 4 new literals (settingsGeneral, settingsAiAudio, settingsAppearance, settingsPrivacy); kept `settings` as a redirect target. Extended `ROUTES` (compiler-enforced via `Record<Page, RouteDef>`). Extended `useNavigation` with a `pendingSettingsScrollTarget` transient field (mirrors the proven `pendingConsentField` pattern) + made `navigate("settings")` redirect to `settingsGeneral` via `replace` (mirrors the onboarding-completed guard at App.tsx:131-140). Added `nav.settingsGeneral|AiAudio|Appearance|Privacy` keys to all 8 locale files via `/home/z/my-project/scripts/add_nav_settings_keys.py` (reuses existing `settings.tabs.*` labels — no new translations needed).
- Phase 6.2: Refactored `Sidebar.tsx` with a new `NavSubmenu` component (radix Collapsible + Popover, both already in node_modules) for the Settings parent. The parent shows icon + label + chevron; clicking the parent either navigates to the default child OR toggles expanded (if a child is already active). The chevron has its own click handler that ONLY toggles expanded (does not navigate). Active child carries `aria-current="page"`; parent carries `aria-expanded="true"` when expanded + `aria-current="page"` only when the parent literal itself is active (test fallback). Collapsed sidebar uses a Popover flyout for the 4 children. Manual expand/collapse preference persisted to `localStorage` key `vt_settings_submenu_expanded`. Added `a11y.collapseSubmenu` + `a11y.expandSubmenu` keys to all 8 locales via `/home/z/my-project/scripts/add_submenu_a11y_keys.py`.
- Phase 6.3: Refactored `Settings.tsx` to remove the top `<SegmentedControl variant="tabs">`. Settings page now accepts `page?: Page` prop (default `settingsGeneral`) and derives `activeTab` from it via `pageToTab()` (no more local tab state + localStorage persistence). `handleSearchChange` now calls `navigate(tabToPage(bestTab), { settingsScrollTarget: { rowHint: bestLabel } })` for cross-page matches (no more `setActiveTab(bestTab)`). The page consumes `pendingSettingsScrollTarget` (mirrors consent-deep-link consumption) to scroll to + briefly highlight the matched row. Removed `_tabBarStyles.ts` import (Settings no longer uses `tabPageHeaderClassName` / `tabPageIndicatorClassName`).
- Phase 6.4: Wired `App.tsx` `renderPage()` switch with 4 new cases for the Settings sub-pages + a defensive fallback case for `"settings"` (renders General sub-page if a stale persisted nav state somehow reaches render before the nav store redirect fires). Updated the `navigate` IPC event handler so a `consent_field` paired with the legacy `"settings"` path now sends `settingsPrivacy` (the Privacy sub-page where consent toggles live) instead of `settingsGeneral`.
- Phase 6.5: Updated Sidebar + Settings tests for the new architecture:
  - Added `PaintBoardIcon` + `SlidersHorizontalIcon` to the canonical hugeicons mock (`__tests__/helpers/hugeicons-mock.ts`).
  - Updated Settings tests to mount `<SettingsPage page="settingsGeneral|settingsAppearance|settingsPrivacy" />` directly (the SegmentedControl tab click is gone).
  - Updated `pages-improvements.test.tsx` to mount on the specific sub-page instead of clicking the (removed) tab.
  - Updated `useNavigation.test.tsx` for the `navigate("settings")` → `settingsGeneral` redirect (asserts `currentPage === "settingsGeneral"`).
  - Added new test file `Sidebar.nav-submenu.test.tsx` (7 tests) covering: 4 children render when expanded, active child has `aria-current="page"`, parent has `aria-expanded="true"`, children collapse when leaving Settings, clicking a child calls `onNavigate`, clicking the parent calls `onNavigate("settings")`, collapsed sidebar Popover flyout shows the 4 children.
  - Updated `stableMocks.tsx` to add `mockPendingSettingsScrollTarget` + `mockConsumeSettingsScrollTarget` for the new transient field.
- Bonus REFACTORs (Sub-Agent 6-a + orchestrator):
  - Replaced 3 raw `<input type="checkbox">` sites in Onboarding + ModelStep with the existing shadcn `Checkbox` primitive (already on disk). Added `vi.mock("@/components/ui/checkbox", ...)` to Onboarding tests.
  - Fixed C-I18N-1 violation at `useMicrophoneData.ts:161` — replaced hardcoded English "Failed to load microphone data" with `t("microphone.loadFailedDescription")` (key already exists in all 8 locales).
  - Fixed C-BRAND-1 minor violation at `Privacy.tsx:1` — reworded the prose comment to avoid the literal "Voice Typer" (now reads "how the app handles audio and data").

Stage Summary:
- 0 mechanical migrations — every KEEP-CUSTOM recommendation justified by a real Voice Typer constraint (Electron desktop app, no URL router, custom theme tokens, Kbd-chip tooltips, cross-language hotkey contract, etc.).
- 3 REFACTOR sites (Onboarding raw checkbox → shadcn Checkbox) — only justified migrations implemented.
- Settings sidebar redesign complete: nested submenu in Sidebar, 4 sub-pages in Page union, cross-page search deep-link via generalized transient field.
- All existing tests preserved: 1,771 renderer tests passing across 197 files (0 failures, 9 skipped). 7 new NavSubmenu tests added.
- `npm run typecheck` clean (web + node configs).
- `npm run lint` clean (after `npm run format` auto-fixed 3 files for formatter-only issues).
- `npm run build:renderer` succeeds — `out/renderer/` has `index.html`, `bubble.html`, and `assets/` (~2.5MB total, includes the Settings + Sidebar chunks).
- AGENTS.md constraints honored: E1, E6, E10, E12, E14, E15, E16, E18, W0, W2, C-BRAND-1, C-I18N-1/2, C-STYLE-1, C-ARCH-1. Override B for §0 logged at session start.
- IPC contract untouched — no new IPC commands; existing `navigate` push event + `consent_required` push event continue to work unchanged.
- Manual browser verification NOT run — Voice Typer is an Electron desktop app that requires a display + Python backend + sidecar binaries. The sandbox has no display; the test suite (1,771 tests, 0 failures) is the regression evidence per AGENTS.md validation pipeline. A real Electron launch would require `xvfb-run -a npm run dev` AND a running Python backend + sidecar binary — out of scope for this session per the worklog Known Limitations convention.
