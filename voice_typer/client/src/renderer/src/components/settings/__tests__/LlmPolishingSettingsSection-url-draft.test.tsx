/**
 * LlmPolishingSettingsSection — the LLM API URL draft resets when the
 * committed `llm_api_url` changes EXTERNALLY (reset-to-defaults,
 * config_changed push), while typed-while-focused edits are protected
 * from our own debounced save echo.
 *
 * Pinned contract:
 *  - Unfocused + committed value change → draft dropped, input shows
 *    the new committed value.
 *  - Focused (user typing) → draft survives, even when our own echo
 *    lands mid-typing.
 */
import {
	act,
	cleanup,
	fireEvent,
	render,
	screen,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: () => <span data-testid="hugeicon" />,
}));

vi.mock("@hugeicons/core-free-icons", async () => {
	const { createHugeiconsMock } = await import(
		"@/__tests__/helpers/hugeicons-mock"
	);
	return createHugeiconsMock();
});

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: vi.fn() }),
	usePythonEvent: () => {},
}));

vi.mock("@/hooks/useSnackbar", () => ({
	useSnackbar: () => ({ showSnack: vi.fn() }),
}));

vi.mock("sonner", () => ({
	toast: {
		success: vi.fn(),
		error: vi.fn(),
		warning: vi.fn(),
		info: vi.fn(),
		dismiss: vi.fn(),
	},
	Toaster: () => null,
}));

vi.mock("next-themes", () => ({ useTheme: () => ({ theme: "light" }) }));

import { LlmPolishingSettingsSection } from "@/components/settings/LlmPolishingSettingsSection";
import type { SettingsSectionSharedProps } from "@/components/settings/types";
import type { VoiceTyperConfig } from "@/types/config";

function makeConfig(
	overrides: Partial<VoiceTyperConfig> = {},
): VoiceTyperConfig {
	return {
		llm_polish: true,
		llm_polish_consent: true,
		llm_api_key: "",
		llm_api_url: "",
		llm_model: "",
		llm_preset: "professional",
		...overrides,
	} as VoiceTyperConfig;
}

const alwaysVisible: SettingsSectionSharedProps["isVisible"] = () => true;

function renderSection(config: VoiceTyperConfig) {
	return render(
		<TooltipProvider delayDuration={200}>
			<LlmPolishingSettingsSection
				config={config}
				updateConfig={() => {}}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>
		</TooltipProvider>,
	);
}

function urlInput(): HTMLInputElement {
	// The URL input carries aria-label = the translated "API URL" label
	// (en.json) — located via that accessible name.
	const input = screen
		.getAllByRole("textbox")
		.find((el) => el.getAttribute("aria-label") === "API URL");
	if (!input) throw new Error("API URL input not found");
	return input as HTMLInputElement;
}

afterEach(() => {
	cleanup();
});

describe("LlmPolishingSettingsSection — urlDraft resets on external config change", () => {
	it("unfocused: an external llm_api_url change drops the stale draft", () => {
		const { rerender } = renderSection(
			makeConfig({ llm_api_url: "https://old" }),
		);
		const input = urlInput();
		expect(input.value).toBe("https://old");

		// Simulate the committed value changing externally (reset-to-defaults).
		act(() => {
			rerender(
				<TooltipProvider delayDuration={200}>
					<LlmPolishingSettingsSection
						config={makeConfig({ llm_api_url: "https://reset" })}
						updateConfig={() => {}}
						updateConfigDebounced={() => {}}
						isVisible={alwaysVisible}
					/>
				</TooltipProvider>,
			);
		});
		expect(input.value).toBe("https://reset");
	});

	it("unfocused: rerenders with the SAME committed value keep the draft untouched", () => {
		const props = {
			updateConfig: () => {},
			updateConfigDebounced: () => {},
			isVisible: alwaysVisible,
		};
		const { rerender } = renderSection(
			makeConfig({ llm_api_url: "https://a" }),
		);
		const input = urlInput();
		fireEvent.change(input, { target: { value: "https://draft" } });
		expect(input.value).toBe("https://draft");

		act(() => {
			rerender(
				<TooltipProvider delayDuration={200}>
					<LlmPolishingSettingsSection
						config={makeConfig({ llm_api_url: "https://a" })}
						{...props}
					/>
				</TooltipProvider>,
			);
		});
		expect(input.value).toBe("https://draft");
	});

	it("focused: our own debounced echo landing mid-typing does NOT clobber the draft", () => {
		const props = {
			updateConfig: () => {},
			updateConfigDebounced: () => {},
			isVisible: alwaysVisible,
		};
		const { rerender } = renderSection(makeConfig({ llm_api_url: "" }));
		const input = urlInput();

		fireEvent.focus(input);
		fireEvent.change(input, { target: { value: "https://typ" } });
		// The debounced save lands while the user keeps typing.
		act(() => {
			rerender(
				<TooltipProvider delayDuration={200}>
					<LlmPolishingSettingsSection
						config={makeConfig({ llm_api_url: "https://typ" })}
						{...props}
					/>
				</TooltipProvider>,
			);
		});
		fireEvent.change(input, { target: { value: "https://typing-more" } });
		expect(input.value).toBe("https://typing-more");

		fireEvent.blur(input);
	});
});
