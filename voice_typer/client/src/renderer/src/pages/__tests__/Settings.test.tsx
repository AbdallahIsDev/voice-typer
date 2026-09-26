/**
 * Tests for the Settings page, batched config writes (PERF-002).
 *
 */
import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
	navigationMock,
	nextThemesMock,
	pythonMock,
	resetStableMocks,
	sonnerMock,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";

const {
	mockCall,
	mockNavigate,
	mockPendingConsentField,
	mockConsumeConsentField,
} = stableMocks;

vi.mock("@/hooks/usePython", () => pythonMock());
vi.mock("@/hooks/useNavigation", () => navigationMock());
vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());
vi.mock("sonner", () => sonnerMock());
vi.mock("next-themes", () => nextThemesMock());

import { makeConfig } from "@/__tests__/helpers/fixtures";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { LausuConfig } from "@/types/config";

/**
 * Settings-page render helper. The page's render graph uses Radix
 * `Tooltip` (via SettingRow and other ui primitives); the real App shell
 */
const renderWithProviders = (ui: React.ReactElement) =>
	render(<TooltipProvider delayDuration={200}>{ui}</TooltipProvider>);

/**
 * A complete, valid LausuConfig with `theme_preset: "custom"` so the
 * color picker renders on first paint.  Only the theme-related fields are
 */
const baseConfig: LausuConfig = makeConfig({
	schema_version: 1,
	fast_startup: true,
	llm_preset: "default",
	theme_preset: "custom",
	custom_theme: {
		light: {
			"--background": "#ffffff",
			"--surface-subtle": "#f5f5f5",
			"--text": "#000000",
			"--muted-foreground": "#666666",
			"--accent": "#3b82f6",
			"--border": "#e5e7eb",
		},
		dark: {
			"--background": "#000000",
			"--surface-subtle": "#111111",
			"--text": "#ffffff",
			"--muted-foreground": "#999999",
			"--accent": "#60a5fa",
			"--border": "#222222",
		},
	},
});

/**
 * Count `set_config` IPC calls captured by mockCall.
 */
function setConfigCallCount(): number {
	return mockCall.mock.calls.filter(
		(args: unknown[]) => args[0] === "set_config",
	).length;
}

/**
 * Return the payload of the most recent `set_config` call (or null).
 */
function lastSetConfigPayload(): Record<string, unknown> | null {
	const setConfigCalls = mockCall.mock.calls.filter(
		(args: unknown[]) => args[0] === "set_config",
	) as Array<[string, Record<string, unknown>?]>;
	if (setConfigCalls.length === 0) return null;
	return setConfigCalls[setConfigCalls.length - 1]?.[1] ?? null;
}

describe("Settings page, PERF-002 batched config writes", () => {
	beforeEach(() => {
		resetStableMocks();
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("mounts and loads config via get_config without firing set_config", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: SettingsPage } = await import("@/pages/Settings");
		renderWithProviders(<SettingsPage page="settingsGeneral" />);

		await waitFor(() => {
			expect(screen.getByText("Settings")).toBeTruthy();
		});

		expect(setConfigCallCount()).toBe(0);
	});

	it("batches 3 rapid color-picker changes into a single set_config call", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: SettingsPage } = await import("@/pages/Settings");
		renderWithProviders(<SettingsPage page="settingsAppearance" />);

		await waitFor(() => {
			expect(screen.getByText("Settings")).toBeTruthy();
		});

		await waitFor(() => {
			expect(
				document.querySelectorAll('input[type="color"]').length,
			).toBeGreaterThanOrEqual(3);
		});
		const colorInputs = document.querySelectorAll('input[type="color"]');
		const [input0, input1, input2] = colorInputs;
		if (!input0 || !input1 || !input2) {
			throw new Error("expected at least 3 color inputs");
		}

		fireEvent.input(input0, { target: { value: "#ff0000" } });
		fireEvent.input(input1, { target: { value: "#00ff00" } });
		fireEvent.input(input2, { target: { value: "#0000ff" } });

		await waitFor(() => {
			expect(setConfigCallCount()).toBe(1);
		});

		// The single set_config payload must carry the custom_theme
		const payload = lastSetConfigPayload();
		expect(payload).not.toBeNull();
		expect(payload).toHaveProperty("custom_theme");
	});

	it("re-saves when a setting is reverted (diff is against the last saved value, not the original load)", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: SettingsPage } = await import("@/pages/Settings");
		renderWithProviders(<SettingsPage page="settingsAppearance" />);

		await waitFor(() => {
			expect(screen.getByText("Settings")).toBeTruthy();
		});

		await waitFor(() => {
			expect(
				document.querySelectorAll('input[type="color"]').length,
			).toBeGreaterThanOrEqual(1);
		});

		const colorInputs = document.querySelectorAll('input[type="color"]');
		const firstColorInput = colorInputs[0] as HTMLInputElement;
		expect(firstColorInput).toBeDefined();

		const originalValue = firstColorInput.value;

		fireEvent.input(firstColorInput, { target: { value: "#abcdef" } });
		await waitFor(() => {
			expect(setConfigCallCount()).toBe(1);
		});

		fireEvent.input(firstColorInput, { target: { value: originalValue } });
		await waitFor(() => {
			expect(setConfigCallCount()).toBe(2);
		});

		const payload = lastSetConfigPayload();
		expect(payload).not.toBeNull();
		expect(payload).toHaveProperty("custom_theme");
	});

	it("Re-run setup wizard synchronously mirrors onboarding_completed=false into the appStore", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { useAppStore } = await import("@/stores/appStore");
		useAppStore.getState().setConfig(baseConfig);
		expect(useAppStore.getState().config?.onboarding_completed).toBe(true);

		const { default: SettingsPage } = await import("@/pages/Settings");
		renderWithProviders(<SettingsPage page="settingsAdvanced" />);

		await waitFor(() => {
			expect(
				screen.getByRole("heading", { name: "Troubleshooting" }),
			).toBeTruthy();
		});

		const wizardButton = await waitFor(() =>
			screen.getByRole("button", { name: "Re-run setup wizard" }),
		);

		mockCall.mockClear();
		fireEvent.click(wizardButton);

		await waitFor(() => {
			expect(mockNavigate).toHaveBeenCalledWith("onboarding");
		});

		// D1-FIX assertion: the appStore snapshot must reflect
		expect(useAppStore.getState().config?.onboarding_completed).toBe(false);

		await waitFor(() => {
			expect(setConfigCallCount()).toBe(1);
		});
		const payload = lastSetConfigPayload();
		expect(payload).not.toBeNull();
		expect(payload).toHaveProperty("onboarding_completed", false);
	});

	it("consumes a consent deep-link: jumps to the Privacy tab and highlights the exact toggle row", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});
		// event handler). The test must mount the page with
		mockPendingConsentField.mockReturnValue("voice_biometric_consent");
		mockConsumeConsentField.mockReturnValue("voice_biometric_consent");

		const { default: SettingsPage } = await import("@/pages/Settings");
		renderWithProviders(<SettingsPage page="settingsPrivacy" />);

		await waitFor(() => {
			const row = document.querySelector(
				'[data-consent-field="voice_biometric_consent"]',
			);
			expect(row).toBeTruthy();
			expect(row?.className).toContain("ring-");
		});

		// The pending target was consumed exactly once (one-shot).
		expect(mockConsumeConsentField).toHaveBeenCalledTimes(1);
	});

	it("S5-CR-103: Reset to Defaults uses Delete02Icon, distinct from Re-run Wizard's ArrowTurnBackwardIcon", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: SettingsPage } = await import("@/pages/Settings");
		renderWithProviders(<SettingsPage page="settingsAdvanced" />);

		await waitFor(() => {
			expect(
				screen.getByRole("heading", { name: "Troubleshooting" }),
			).toBeTruthy();
		});

		const resetButton = await waitFor(() =>
			screen.getByRole("button", { name: "Reset to Defaults" }),
		);
		const wizardButton = await waitFor(() =>
			screen.getByRole("button", { name: "Re-run setup wizard" }),
		);

		const resetIcon = resetButton.querySelector(
			'[data-testid="hugeicon"]',
		) as HTMLElement | null;
		const wizardIcon = wizardButton.querySelector(
			'[data-testid="hugeicon"]',
		) as HTMLElement | null;

		expect(wizardIcon).toBeTruthy();

		//Reset to Defaults MUST use the trash glyph.
		expect(resetIcon?.getAttribute("data-name")).toBe("Delete02Icon");
		// Re-run Wizard MUST use the back-arrow glyph (NOT Delete02Icon).
		expect(wizardIcon?.getAttribute("data-name")).toBe("ArrowTurnBackwardIcon");
		// Belt-and-braces: the two icons must not be the same glyph.
		expect(resetIcon?.getAttribute("data-name")).not.toBe(
			wizardIcon?.getAttribute("data-name"),
		);
	});

	const macUA =
		"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) " +
		"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36";
	const linuxUA =
		"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 " +
		"(KHTML, like Gecko) Chrome/126.0 Safari/537.36";
	const windowsUA =
		"Mozilla/5.0 (Windows NT 10.0; Win64; x64) " +
		"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36";

	async function renderSettingsOnAdvancedForResetTests() {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: SettingsPage } = await import("@/pages/Settings");
		renderWithProviders(<SettingsPage page="settingsAdvanced" />);

		await waitFor(() => {
			expect(
				screen.getByRole("heading", { name: "Troubleshooting" }),
			).toBeTruthy();
		});
	}

	it("renders the Reset Accessibility Permission button on macOS and calls reset_macos_accessibility", async () => {
		Object.defineProperty(window.navigator, "userAgent", {
			value: macUA,
			configurable: true,
		});
		try {
			await renderSettingsOnAdvancedForResetTests();

			const button = await waitFor(() =>
				screen.getByRole("button", {
					name: "Reset macOS accessibility permission",
				}),
			);

			const icon = button.querySelector(
				'[data-testid="hugeicon"]',
			) as HTMLElement | null;
			expect(icon?.getAttribute("data-name")).toBe("ShieldBanIcon");

			fireEvent.click(button);
			expect(mockCall).toHaveBeenCalledWith("reset_macos_accessibility");
		} finally {
			Object.defineProperty(window.navigator, "userAgent", {
				value: linuxUA,
				configurable: true,
			});
		}
	});

	// button. A missing / falsy suggestion must render nothing extra.
	it("surfaces the runtime tccutil reset command when the grant looks stale", async () => {
		Object.defineProperty(window.navigator, "userAgent", {
			value: macUA,
			configurable: true,
		});
		try {
			mockCall.mockImplementation((type: string) => {
				if (type === "get_config") return Promise.resolve(baseConfig);
				if (type === "set_config") return Promise.resolve({ success: true });
				if (type === "check_accessibility")
					return Promise.resolve({
						granted: false,
						platform: "macos",
						suggest_reset: true,
						reset_command: "tccutil reset Accessibility com.Lausu.desktop",
					});
				return Promise.resolve({});
			});

			const { default: SettingsPage } = await import("@/pages/Settings");
			renderWithProviders(<SettingsPage page="settingsAdvanced" />);

			await waitFor(() => {
				expect(
					screen.getByRole("heading", { name: "Troubleshooting" }),
				).toBeTruthy();
			});

			// The suggested command must be rendered as code text.
			await waitFor(() => {
				expect(
					screen.getByText(
						"tccutil reset Accessibility com.Lausu.desktop",
					),
				).toBeTruthy();
			});
			expect(mockCall).toHaveBeenCalledWith("check_accessibility");
		} finally {
			mockCall.mockImplementation((type: string) => {
				if (type === "get_config") return Promise.resolve(baseConfig);
				if (type === "set_config") return Promise.resolve({ success: true });
				return Promise.resolve({});
			});
			Object.defineProperty(window.navigator, "userAgent", {
				value: linuxUA,
				configurable: true,
			});
		}
	});

	it("renders the Reset Linux Permission button on Linux and calls reset_linux_permissions", async () => {
		Object.defineProperty(window.navigator, "userAgent", {
			value: linuxUA,
			configurable: true,
		});
		try {
			await renderSettingsOnAdvancedForResetTests();

			const button = await waitFor(() =>
				screen.getByRole("button", {
					name: "Reset Linux keyboard permission",
				}),
			);

			const icon = button.querySelector(
				'[data-testid="hugeicon"]',
			) as HTMLElement | null;
			expect(icon?.getAttribute("data-name")).toBe("ShieldBanIcon");

			fireEvent.click(button);
			expect(mockCall).toHaveBeenCalledWith("reset_linux_permissions");
		} finally {
			Object.defineProperty(window.navigator, "userAgent", {
				value: macUA,
				configurable: true,
			});
		}
	});

	it("does NOT render either platform reset button on non-macOS/non-Linux", async () => {
		Object.defineProperty(window.navigator, "userAgent", {
			value: windowsUA,
			configurable: true,
		});
		await renderSettingsOnAdvancedForResetTests();

		await waitFor(() =>
			expect(
				screen.getByRole("button", { name: "Re-run setup wizard" }),
			).toBeTruthy(),
		);

		expect(
			screen.queryByRole("button", {
				name: "Reset macOS accessibility permission",
			}),
		).toBeNull();
		expect(
			screen.queryByRole("button", {
				name: "Reset Linux keyboard permission",
			}),
		).toBeNull();
	});
});

describe("Settings search auto-switch navigation", () => {
	beforeEach(() => {
		resetStableMocks();
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("navigates to the Appearance sub-page when the query matches the Appearance tab label", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: SettingsPage } = await import("@/pages/Settings");
		renderWithProviders(<SettingsPage page="settingsGeneral" />);

		await waitFor(() => {
			expect(screen.getByText("Settings")).toBeTruthy();
		});

		const { useGlobalSearch } = await import("@/stores/useGlobalSearch");
		useGlobalSearch.getState().setQuery("appearance");

		await waitFor(() => {
			expect(mockNavigate).toHaveBeenCalledWith(
				"settingsAppearance",
				expect.objectContaining({
					settingsScrollTarget: expect.objectContaining({
						rowHint: expect.any(String),
					}),
				}),
			);
		});
	});

	it("navigates to the Advanced section page via the cross-section results when the query matches a PrewarmAndUpdates label", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: SettingsPage } = await import("@/pages/Settings");
		renderWithProviders(<SettingsPage page="settingsGeneral" />);

		await waitFor(() => {
			expect(screen.getByText("Settings")).toBeTruthy();
		});

		const { useGlobalSearch } = await import("@/stores/useGlobalSearch");
		useGlobalSearch.getState().setQuery("Prewarm Status");

		const section = await waitFor(() =>
			screen.getByTestId("settings-other-tabs-results"),
		);
		expect(section.textContent).toContain("Advanced");

		fireEvent.click(
			screen.getAllByRole("button", {
				name: "Prewarm Status",
			})[0] as HTMLElement,
		);
		await waitFor(() => {
			expect(mockNavigate).toHaveBeenCalledWith(
				"settingsAdvanced",
				expect.objectContaining({
					settingsScrollTarget: expect.objectContaining({
						rowHint: "Prewarm Status",
					}),
				}),
			);
		});
	});

	it("does NOT navigate when the query is shorter than 2 characters", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: SettingsPage } = await import("@/pages/Settings");
		renderWithProviders(<SettingsPage page="settingsGeneral" />);

		await waitFor(() => {
			expect(screen.getByText("Settings")).toBeTruthy();
		});

		const { useGlobalSearch } = await import("@/stores/useGlobalSearch");
		useGlobalSearch.getState().setQuery("a");

		await new Promise((resolve) => setTimeout(resolve, 100));
		expect(mockNavigate).not.toHaveBeenCalled();
	});

	// The empty-banner sentinel and the auto-switch MUST share one match
	it("auto-switch and empty banner AGREE on the same query (one match semantic)", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: SettingsPage } = await import("@/pages/Settings");
		renderWithProviders(<SettingsPage page="settingsGeneral" />);

		await waitFor(() => {
			expect(screen.getByText("Settings")).toBeTruthy();
		});

		const { useGlobalSearch } = await import("@/stores/useGlobalSearch");

		// strict semantic must NOT navigate and MUST show the banner.
		useGlobalSearch.getState().setQuery("the llm polishing rows");
		await waitFor(() => {
			expect(
				screen.getByText('No settings match "the llm polishing rows"'),
			).toBeTruthy();
		});
		expect(mockNavigate).not.toHaveBeenCalled();

		useGlobalSearch.getState().setQuery("llm polishing");
		await waitFor(() => {
			expect(mockNavigate).toHaveBeenCalledWith(
				"settingsAI",
				expect.objectContaining({
					settingsScrollTarget: expect.objectContaining({
						rowHint: expect.any(String),
					}),
				}),
			);
		});
		await waitFor(() => {
			expect(screen.queryByText(/No settings match/)).toBeNull();
		});
	});
});
