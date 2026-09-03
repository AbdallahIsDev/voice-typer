/**
 * LlmPolishingSettingsSection — LLM-polish enablement consent gate.
 *
 * Turning the "LLM Polishing" master toggle ON sends transcribed text
 * to the configured LLM provider, which requires `llm_polish_consent`.
 * Contract (shared point-of-use consent gate):
 *
 *   • consent missing + toggling ON → does NOT persist llm_polish;
 *     opens the shared dialog request (llm_polish_consent field +
 *     its consentDialog body key);
 *   • Allow → (dialog persisted the consent) → retry enables
 *     llm_polish;
 *   • Cancel → nothing enabled;
 *   • consent already granted, or toggling OFF → behaves as before
 *     (immediate persist, no dialog).
 */
import {
	act,
	cleanup,
	fireEvent,
	render,
	screen,
} from "@testing-library/react";
import { TooltipProvider } from "@/components/ui/tooltip";

const renderWithProviders = (ui: React.ReactElement) =>
	render(<TooltipProvider delayDuration={200}>{ui}</TooltipProvider>);

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
	nextThemesMock,
	pythonMock,
	snackbarMock,
	sonnerMock,
} from "@/__tests__/helpers/stableMocks";

vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());
vi.mock("@/hooks/usePython", () => pythonMock());
vi.mock("@/hooks/useSnackbar", () => snackbarMock());
vi.mock("sonner", () => sonnerMock());
vi.mock("next-themes", () => nextThemesMock());

import { LlmPolishingSettingsSection } from "@/components/settings/LlmPolishingSettingsSection";
import type { SettingsSectionSharedProps } from "@/components/settings/types";
import { useConsentGateStore } from "@/lib/consentGate";
import type { VoiceTyperConfig } from "@/types/config";

/** Minimal valid config (same shape as the Settings test suite's). */
function makeConfig(
	overrides: Partial<VoiceTyperConfig> = {},
): VoiceTyperConfig {
	return {
		llm_polish: false,
		llm_polish_consent: false,
		llm_api_key: "",
		llm_api_url: "",
		llm_model: "",
		llm_preset: "professional",
		...overrides,
	} as VoiceTyperConfig;
}

const alwaysVisible: SettingsSectionSharedProps["isVisible"] = () => true;

describe("LlmPolishingSettingsSection — enabling LLM polish asks for llm_polish_consent first", () => {
	let updateConfig: SettingsSectionSharedProps["updateConfig"];

	const renderSection = (config: VoiceTyperConfig) =>
		renderWithProviders(
			<LlmPolishingSettingsSection
				config={config}
				updateConfig={updateConfig}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);

	beforeEach(() => {
		updateConfig = vi.fn<(updates: Partial<VoiceTyperConfig>) => void>();
		useConsentGateStore.setState({ request: null });
	});

	afterEach(() => {
		cleanup();
		useConsentGateStore.setState({ request: null });
	});

	it("opens the shared consent gate and does NOT enable polish when consent is missing", () => {
		renderSection(makeConfig());

		fireEvent.click(screen.getByRole("switch", { name: "LLM Polishing" }));

		const req = useConsentGateStore.getState().request;
		expect(req).not.toBeNull();
		expect(req?.consentField).toBe("llm_polish_consent");
		expect(req?.bodyKey).toBe("consentDialog.field.llm_polish_consent");
		expect(updateConfig).not.toHaveBeenCalled();
	});

	it("enables polish after Allow (the retry runs once consent is granted)", async () => {
		renderSection(makeConfig());
		fireEvent.click(screen.getByRole("switch", { name: "LLM Polishing" }));

		const onAllow = useConsentGateStore.getState().request?.onAllow;
		expect(onAllow).toBeDefined();
		await act(async () => {
			await onAllow?.();
		});

		expect(updateConfig).toHaveBeenCalledWith({ llm_polish: true });
	});

	it("does not enable polish when the dialog is cancelled", () => {
		renderSection(makeConfig());
		fireEvent.click(screen.getByRole("switch", { name: "LLM Polishing" }));

		act(() => {
			useConsentGateStore.getState().close();
		});

		expect(useConsentGateStore.getState().request).toBeNull();
		expect(updateConfig).not.toHaveBeenCalled();
	});

	it("persists immediately when consent is already granted (no nag)", () => {
		renderSection(makeConfig({ llm_polish_consent: true }));

		fireEvent.click(screen.getByRole("switch", { name: "LLM Polishing" }));

		expect(useConsentGateStore.getState().request).toBeNull();
		expect(updateConfig).toHaveBeenCalledWith({ llm_polish: true });
	});

	it("toggling OFF never opens the gate", () => {
		renderSection(makeConfig({ llm_polish: true, llm_polish_consent: false }));

		fireEvent.click(screen.getByRole("switch", { name: "LLM Polishing" }));

		expect(useConsentGateStore.getState().request).toBeNull();
		expect(updateConfig).toHaveBeenCalledWith({ llm_polish: false });
	});
});
