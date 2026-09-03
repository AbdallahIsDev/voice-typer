import { lazy, Suspense } from "react";
import { RouteSkeleton } from "@/components/feedback/skeletons";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n/i18n";
// Route-level code splitting. Home is the default landing page
// and stays eagerly imported so first paint is fast. The other 8 pages
// (History, Templates, Vocabulary, Models, Microphone, Analytics,
// Settings, AboutAndPrivacy, Onboarding) are loaded on demand via
// React.lazy so Vite emits per-route chunks and the initial JS payload
// only carries the Home page's transitive deps. Each lazy import
// resolves to the page module's default export.
// Chunks are ALSO prefetched at idle + sidebar hover (router/prefetch.ts),
// so the Suspense fallback below is a one-frame affordance, not a wait.
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
 * A page-shaped Skeleton (the app's single loading primitive) instead
 * of the former centered spinner: it matches the target page's layout
 * so the chunk-load → content transition doesn't jump. In practice it's
 * a one-frame flash — all route chunks are prefetched at idle
 * (router/prefetch.ts), on sidebar hover via `prefetchPage`, and
 * React.lazy caches resolved modules so revisits render synchronously.
 */
function RouteSuspenseFallback() {
	return <RouteSkeleton />;
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
			// The Settings surface is HUB + nested section pages. "settings"
			// renders the hub (one card whose rows open the section pages —
			// see SettingsHub + settingsSections.ts); each section literal
			// renders `<SettingsPage page={...} />` with exactly that
			// domain's cards. All literals resolve to the same lazy chunk.
			case "settings":
				return <SettingsPage page="settings" />;
			case "settingsGeneral":
				return <SettingsPage page="settingsGeneral" />;
			case "settingsOverlay":
				return <SettingsPage page="settingsOverlay" />;
			case "settingsHotkeys":
				return <SettingsPage page="settingsHotkeys" />;
			case "settingsTranscription":
				return <SettingsPage page="settingsTranscription" />;
			case "settingsAI":
				return <SettingsPage page="settingsAI" />;
			case "settingsAudio":
				return <SettingsPage page="settingsAudio" />;
			case "settingsAppearance":
				return <SettingsPage page="settingsAppearance" />;
			case "settingsPrivacy":
				return <SettingsPage page="settingsPrivacy" />;
			case "settingsAdvanced":
				return <SettingsPage page="settingsAdvanced" />;
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
