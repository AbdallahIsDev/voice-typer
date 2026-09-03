/**
 * PageSwitch — the route→component mapping extracted from App.tsx.
 *
 * Verifies, per route literal:
 *   - the mapped page component mounts (lazy chunks resolve through
 *     the internal Suspense boundary),
 *   - the onboarding wizard receives its completion callback,
 *   - an unknown page value falls back to the i18n page-not-found UI
 *     with a working "go home" action.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/pages/Home", () => ({
	default: () => <div data-testid="page-home">Home page</div>,
}));
vi.mock("@/pages/History", () => ({
	default: () => <div data-testid="page-history">History page</div>,
}));
vi.mock("@/pages/Templates", () => ({
	default: () => <div data-testid="page-templates">Templates page</div>,
}));
vi.mock("@/pages/Vocabulary", () => ({
	default: () => <div data-testid="page-vocabulary">Vocabulary page</div>,
}));
vi.mock("@/pages/Models", () => ({
	default: () => <div data-testid="page-models">Models page</div>,
}));
vi.mock("@/pages/Microphone", () => ({
	default: () => <div data-testid="page-microphone">Microphone page</div>,
}));
vi.mock("@/pages/Dashboard", () => ({
	default: () => <div data-testid="page-analytics">Analytics page</div>,
}));
vi.mock("@/pages/AboutAndPrivacy", () => ({
	default: () => (
		<div data-testid="page-aboutAndPrivacy">About & Privacy page</div>
	),
}));
vi.mock("@/pages/Onboarding", () => ({
	default: ({ onComplete }: { onComplete?: () => void }) => (
		<div data-testid="page-onboarding">
			Onboarding page
			<button type="button" onClick={onComplete}>
				Finish
			</button>
		</div>
	),
}));
vi.mock("@/pages/Settings", () => ({
	default: ({ page }: { page: string }) =>
		// The real SettingsPage renders SettingsHub (whose rows carry
		// `settings-hub-row-<section>` testids) when page === "settings",
		// and the section UI otherwise. Mirror that contract so the
		// PageSwitch mapping test can assert the hub path.
		page === "settings" ? (
			<section aria-label="Settings">
				<button type="button" data-testid="settings-hub-row-settingsGeneral">
					General
				</button>
				<button type="button" data-testid="settings-hub-row-settingsPrivacy">
					Privacy
				</button>
			</section>
		) : (
			<div data-testid={`settings-${page}`}>Settings sub-page {page}</div>
		),
}));

import { PageSwitch } from "@/router/PageSwitch";
import type { Page } from "@/types/ipc";

const navigate = vi.fn();

afterEach(() => {
	cleanup();
	navigate.mockClear();
});

async function renderFor(page: Page) {
	render(
		<PageSwitch
			page={page}
			navigate={navigate}
			onOnboardingComplete={() => {}}
		/>,
	);
}

describe("PageSwitch — route table mapping", () => {
	it.each([
		["home", "page-home"],
		["history", "page-history"],
		["templates", "page-templates"],
		["vocabulary", "page-vocabulary"],
		["models", "page-models"],
		["microphone", "page-microphone"],
		["analytics", "page-analytics"],
		["aboutAndPrivacy", "page-aboutAndPrivacy"],
	] as const)(
		"renders %s as its mapped page component",
		async (page, testid) => {
			renderFor(page);
			expect(await screen.findByTestId(testid)).toBeTruthy();
		},
	);

	it.each([
		"settingsGeneral",
		"settingsAudio",
		"settingsAppearance",
		"settingsPrivacy",
	] as const)("renders %s with its sub-page prop", async (sub) => {
		renderFor(sub);
		expect(await screen.findByTestId(`settings-${sub}`)).toBeTruthy();
	});

	it("renders 'settings' as the Settings HUB with its section rows", async () => {
		renderFor("settings");
		expect(
			await screen.findByTestId("settings-hub-row-settingsGeneral"),
		).toBeTruthy();
		expect(
			await screen.findByTestId("settings-hub-row-settingsPrivacy"),
		).toBeTruthy();
	});

	it("passes the completion callback through to the onboarding wizard", async () => {
		const onComplete = vi.fn();
		render(
			<PageSwitch
				page="onboarding"
				navigate={navigate}
				onOnboardingComplete={onComplete}
			/>,
		);
		fireEvent.click(await screen.findByText("Finish"));
		expect(onComplete).toHaveBeenCalledTimes(1);
	});

	it("unknown page values fall back to the page-not-found screen with a working go-home action", async () => {
		render(
			<PageSwitch
				page={"does-not-exist" as unknown as Page}
				navigate={navigate}
				onOnboardingComplete={() => {}}
			/>,
		);
		expect(await screen.findByText("Page not found")).toBeTruthy();
		fireEvent.click(screen.getByText("Go to Home"));
		expect(navigate).toHaveBeenCalledWith("home");
	});
});
