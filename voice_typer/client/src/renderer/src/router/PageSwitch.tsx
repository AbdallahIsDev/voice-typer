import { lazy, Suspense } from "react";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n/i18n";
// Route-level code splitting. Home is the default landing page
// and stays eagerly imported so first paint is fast. The other 8 pages
// (History, Templates, Vocabulary, Models, Microphone, Analytics,
// Settings, AboutAndPrivacy, Onboarding) are loaded on demand via
// React.lazy so Vite emits per-route chunks and the initial JS payload
// only carries the Home page's transitive deps. Each lazy import
// resolves to the page module's default export.
import Home from "@/pages/Home";
import type { Page } from "@/types/ipc";

const AboutAndPrivacyPage = lazy(() => import("@/pages/AboutAndPrivacy"));
const DashboardPage = lazy(() => import("@/pages/Dashboard"));
const HistoryPage = lazy(() => import("@/pages/History"));
const MicrophonePage = lazy(() => import("@/pages/Microphone"));
const ModelsPage = lazy(() => import("@/pages/Models"));
const OnboardingPage = lazy(() => import("@/pages/Onboarding"));
const SettingsPage = lazy(() => import("@/pages/Settings"));
const TemplatesPage = lazy(() => import("@/pages/Templates"));
const VocabularyPage = lazy(() => import("@/pages/Vocabulary"));

/**
 * Suspense fallback for the lazy-loaded secondary routes.
 *
 * Inline (not a separate component file) so we don't introduce a new    * module outside the refactor scope. The spinner matches the visual
 * style already used by ``DoneStep.tsx`` and ``MicToggleButton.tsx``
 * (``animate-spin rounded-full border-2 border-current
 * border-t-transparent``) so the user sees a consistent loading
 * indicator across the app.
 *
 * The fallback is intentionally minimal — a route chunk typically
 * loads in <100ms on a local dev server and <300ms from a packaged
 * build, so a full-screen skeleton would flash too briefly to register.
 */
function RouteSuspenseFallback() {
	const t = useT();
	return (
		<output
			aria-live="polite"
			aria-label={t("a11y.loading")}
			className="flex h-full w-full items-center justify-center p-8"
		>
			<span className="inline-block h-6 w-6 animate-spin rounded-full border-2 border-(--text-muted) border-t-transparent" />
		</output>
	);
}

interface PageSwitchProps {
	/** The active route (from useNavigation). */
	page: Page;
	/** Navigate action — used only by the page-not-found fallback. */
	navigate: (page: Page) => void;
	/** Onboarding-completion callback passed through to the wizard. */
	onOnboardingComplete: () => void;
}

/**
 * Route table: see router/routes.ts for the single source of page names.
 * This component maps each `Page` literal to its view — legitimate
 * routing logic (which component renders for which page), not a
 * duplicate of the page registry. The set of valid page names lives
 * in `ROUTES` (router/routes.ts); this switch only chooses the view.
 *
 * Extracted from App.tsx so the app shell stays pure wiring (hooks,
 * overlays, layout) while the route→component mapping lives beside the
 * route table it mirrors. Rendered output is byte-identical to the
 * previous inline `renderPage()` + `<Suspense>` wrapper in App.tsx.
 */
export function PageSwitch({
	page,
	navigate,
	onOnboardingComplete,
}: PageSwitchProps) {
	const t = useT();

	const renderPage = () => {
		switch (page) {
			case "home":
				return <Home />;
			case "history":
				return <HistoryPage />;
			case "templates":
				return <TemplatesPage />;
			case "vocabulary":
				return <VocabularyPage />;
			case "models":
				return <ModelsPage />;
			case "microphone":
				return <MicrophonePage />;
			case "analytics":
				return <DashboardPage />;
			// The Settings page is now a nested-sidebar navigation
			// target (ADR-0021). The 4 sub-page literals
			// (settingsGeneral / settingsAiAudio / settingsAppearance /
			// settingsPrivacy) each render `<SettingsPage page={...} />`
			// — the page derives the active tab from the prop instead
			// of owning tab state locally. The legacy "settings"
			// parent literal is redirected to "settingsGeneral" inside
			// useNavigation.navigate BEFORE this switch is reached, so
			// the `case "settings"` below is a defensive fallback
			// (e.g. for a stale persisted `vt_nav_state` from an older
			// build that resolves before the redirect fires) — it
			// renders the General sub-page rather than an empty
			// parent.
			case "settings":
			case "settingsGeneral":
				return <SettingsPage page="settingsGeneral" />;
			case "settingsAiAudio":
				return <SettingsPage page="settingsAiAudio" />;
			case "settingsAppearance":
				return <SettingsPage page="settingsAppearance" />;
			case "settingsPrivacy":
				return <SettingsPage page="settingsPrivacy" />;
			case "aboutAndPrivacy":
				return <AboutAndPrivacyPage />;
			case "onboarding":
				return <OnboardingPage onComplete={onOnboardingComplete} />;
			default:
				// Page-not-found fallback now resolves via i18n
				// (`app.pageNotFoundTitle` / `app.pageNotFoundDescription`)
				// so non-English users see the fallback in their locale.
				// Both keys ship translated across all 8 locales.
				return (
					<div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
						<p className="text-sm font-medium text-(--text-primary)">
							{t("app.pageNotFoundTitle")}
						</p>
						<p className="text-xs text-(--text-muted)">
							{t("app.pageNotFoundDescription", {
								page: String(page),
							})}
						</p>
						<Button variant="default" onClick={() => navigate("home")}>
							{t("app.goHome")}
						</Button>
					</div>
				);
		}
	};

	return (
		<Suspense fallback={<RouteSuspenseFallback />}>{renderPage()}</Suspense>
	);
}
