/**
 * tests: AiEnhancementSettingsSection cross-slider clamping and
 * LlmPolishingSettingsSection LLM API URL validation.
 *
 * Cross-slider contract: the two confidence sliders (0..1) can touch
 * but never cross — dragging the suggest threshold ABOVE the auto-apply
 * threshold clamps it to the auto-apply value, and dragging auto-apply
 * BELOW the suggest threshold clamps it up to the suggest value.
 *
 * URL contract: typing is never blocked; the inline error appears on
 * blur while the value is not an absolute http(s) URL (empty is valid —
 * the input falls back to the default endpoint).
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: () => <span data-testid="hugeicon" />,
}));

vi.mock("@hugeicons/core-free-icons", async () => {
	const { createHugeiconsMock } = await import(
		"@/__tests__/helpers/hugeicons-mock"
	);
	return createHugeiconsMock();
});

vi.mock("@/components/feedback/InfoTooltip", () => ({
	InfoTooltip: ({ text }: { text: string }) => (
		<span data-testid="info-tooltip" data-text={text} />
	),
}));

vi.mock("@/components/common/KeyringStatusBadge", () => ({
	KeyringStatusBadge: () => <span data-testid="keyring-badge" />,
}));

vi.mock("@/lib/consentGate", () => ({
	openConsentGate: vi.fn(),
	consentBodyKey: (field: string) => `consent.${field}`,
}));

const sliderInstances: Array<{
	ariaLabel: string;
	value: number;
	onChange: (v: number) => void;
	disabled: boolean;
}> = [];

vi.mock("@/components/common/RangeSlider", () => ({
	RangeSlider: (props: {
		ariaLabel?: string;
		value: number;
		onChange: (v: number) => void;
		disabled?: boolean;
	}) => {
		sliderInstances.push({
			ariaLabel: props.ariaLabel ?? "",
			value: props.value,
			onChange: props.onChange,
			disabled: props.disabled ?? false,
		});
		return <div data-testid="range-slider" data-value={props.value} />;
	},
}));

import { makeConfig } from "@/__tests__/helpers/fixtures";
import { AiEnhancementSettingsSection } from "@/components/settings/AiEnhancementSettingsSection";
import { LlmPolishingSettingsSection } from "@/components/settings/LlmPolishingSettingsSection";

const alwaysVisible = () => true;
const noopUpdate = () => {};

describe("AiEnhancementSettingsSection — cross-slider clamping", () => {
	beforeEach(() => {
		sliderInstances.length = 0;
	});

	afterEach(() => {
		cleanup();
	});

	it("dragging suggest ABOVE auto-apply clamps suggest to the auto-apply value", () => {
		const updateConfigDebounced = vi.fn();
		render(
			<AiEnhancementSettingsSection
				config={makeConfig({
					vocabulary_automation_enabled: true,
					vocabulary_auto_confidence_threshold: 0.7,
					vocabulary_auto_apply_threshold: 0.9,
				})}
				updateConfig={noopUpdate}
				updateConfigDebounced={updateConfigDebounced}
				isVisible={alwaysVisible}
			/>,
		);
		const suggest = sliderInstances.find((s) => s.value === 0.7);
		expect(suggest).toBeTruthy();
		suggest?.onChange(0.95);
		expect(updateConfigDebounced).toHaveBeenCalledWith(
			"vocabulary_auto_confidence_threshold",
			0.9,
		);
	});

	it("dragging suggest BELOW auto-apply passes the value through unclamped", () => {
		const updateConfigDebounced = vi.fn();
		render(
			<AiEnhancementSettingsSection
				config={makeConfig({
					vocabulary_automation_enabled: true,
					vocabulary_auto_confidence_threshold: 0.7,
					vocabulary_auto_apply_threshold: 0.9,
				})}
				updateConfig={noopUpdate}
				updateConfigDebounced={updateConfigDebounced}
				isVisible={alwaysVisible}
			/>,
		);
		const suggest = sliderInstances.find((s) => s.value === 0.7);
		suggest?.onChange(0.5);
		expect(updateConfigDebounced).toHaveBeenCalledWith(
			"vocabulary_auto_confidence_threshold",
			0.5,
		);
	});

	it("dragging auto-apply BELOW suggest clamps it up to the suggest value", () => {
		const updateConfigDebounced = vi.fn();
		render(
			<AiEnhancementSettingsSection
				config={makeConfig({
					vocabulary_automation_enabled: true,
					vocabulary_auto_confidence_threshold: 0.7,
					vocabulary_auto_apply_threshold: 0.9,
				})}
				updateConfig={noopUpdate}
				updateConfigDebounced={updateConfigDebounced}
				isVisible={alwaysVisible}
			/>,
		);
		const apply = sliderInstances.find((s) => s.value === 0.9);
		expect(apply).toBeTruthy();
		apply?.onChange(0.4);
		expect(updateConfigDebounced).toHaveBeenCalledWith(
			"vocabulary_auto_apply_threshold",
			0.7,
		);
	});

	it("sliders can touch (suggest == apply) without clamping", () => {
		const updateConfigDebounced = vi.fn();
		render(
			<AiEnhancementSettingsSection
				config={makeConfig({
					vocabulary_automation_enabled: true,
					vocabulary_auto_confidence_threshold: 0.7,
					vocabulary_auto_apply_threshold: 0.9,
				})}
				updateConfig={noopUpdate}
				updateConfigDebounced={updateConfigDebounced}
				isVisible={alwaysVisible}
			/>,
		);
		const suggest = sliderInstances.find((s) => s.value === 0.7);
		suggest?.onChange(0.9);
		expect(updateConfigDebounced).toHaveBeenCalledWith(
			"vocabulary_auto_confidence_threshold",
			0.9,
		);
	});
});

describe("LlmPolishingSettingsSection — LLM API URL validation", () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	afterEach(() => {
		cleanup();
	});

	it("shows no error while typing (validation does not block typing)", () => {
		const updateConfigDebounced = vi.fn();
		render(
			<LlmPolishingSettingsSection
				config={makeConfig({ llm_polish: true, llm_api_url: "" })}
				updateConfig={noopUpdate}
				updateConfigDebounced={updateConfigDebounced}
				isVisible={alwaysVisible}
			/>,
		);
		const input = screen.getByLabelText("API URL") as HTMLInputElement;
		fireEvent.change(input, { target: { value: "not a url" } });
		expect(updateConfigDebounced).toHaveBeenCalledWith(
			"llm_api_url",
			"not a url",
		);
		expect(screen.queryByTestId("llm-api-url-error")).toBeNull();
	});

	it("shows the inline error on blur for a non-http(s) value", () => {
		render(
			<LlmPolishingSettingsSection
				config={makeConfig({ llm_polish: true, llm_api_url: "" })}
				updateConfig={noopUpdate}
				updateConfigDebounced={noopUpdate}
				isVisible={alwaysVisible}
			/>,
		);
		const input = screen.getByLabelText("API URL") as HTMLInputElement;
		fireEvent.change(input, { target: { value: "ftp://bad.example" } });
		fireEvent.blur(input);
		expect(screen.getByTestId("llm-api-url-error")).toBeTruthy();
		expect(input.getAttribute("aria-invalid")).toBe("true");
	});

	it("rejects scheme-less values on blur", () => {
		render(
			<LlmPolishingSettingsSection
				config={makeConfig({
					llm_polish: true,
					llm_api_url: "api.openai.com/v1",
				})}
				updateConfig={noopUpdate}
				updateConfigDebounced={noopUpdate}
				isVisible={alwaysVisible}
			/>,
		);
		const input = screen.getByLabelText("API URL") as HTMLInputElement;
		fireEvent.blur(input);
		expect(screen.getByTestId("llm-api-url-error")).toBeTruthy();
	});

	it("clears the error once a valid https URL is entered and blurred", () => {
		render(
			<LlmPolishingSettingsSection
				config={makeConfig({ llm_polish: true, llm_api_url: "" })}
				updateConfig={noopUpdate}
				updateConfigDebounced={noopUpdate}
				isVisible={alwaysVisible}
			/>,
		);
		const input = screen.getByLabelText("API URL") as HTMLInputElement;
		fireEvent.change(input, { target: { value: "garbage" } });
		fireEvent.blur(input);
		expect(screen.getByTestId("llm-api-url-error")).toBeTruthy();

		fireEvent.change(input, {
			target: { value: "https://api.groq.com/openai/v1" },
		});
		fireEvent.blur(input);
		expect(screen.queryByTestId("llm-api-url-error")).toBeNull();
	});

	it("accepts http and https URLs on blur without error", () => {
		render(
			<LlmPolishingSettingsSection
				config={makeConfig({
					llm_polish: true,
					llm_api_url: "http://localhost:8000/v1",
				})}
				updateConfig={noopUpdate}
				updateConfigDebounced={noopUpdate}
				isVisible={alwaysVisible}
			/>,
		);
		const input = screen.getByLabelText("API URL") as HTMLInputElement;
		fireEvent.blur(input);
		expect(screen.queryByTestId("llm-api-url-error")).toBeNull();
	});

	it("accepts an empty value on blur (server falls back to the default endpoint)", () => {
		render(
			<LlmPolishingSettingsSection
				config={makeConfig({ llm_polish: true, llm_api_url: "" })}
				updateConfig={noopUpdate}
				updateConfigDebounced={noopUpdate}
				isVisible={alwaysVisible}
			/>,
		);
		const input = screen.getByLabelText("API URL") as HTMLInputElement;
		fireEvent.blur(input);
		expect(screen.queryByTestId("llm-api-url-error")).toBeNull();
	});
});
