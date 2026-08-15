/**
 * Unit tests for the consolidated first-run consent step
 * (`ConsentStep`).
 *
 * Contract under test:
 *   - Renders the Privacy & Consent heading + description.
 *   - Renders a row for EVERY consent-gated field with its
 *     plain-language label (labels are the settings.privacy.* keys,
 *     the single source of truth shared with Settings → Privacy).
 *   - Each row's switch reflects the `consents` prop state.
 *   - Toggling a switch calls onToggleConsent(field, value) — the
 *     wizard persists it immediately via set_config.
 *   - "Agree to All" calls onAgreeToAll (single batched grant).
 */
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

// The step uses the real i18n layer (English default), the real Button
// (radix-ui button renders a <button>), and the real Switch (Radix —
// jsdom can't drive its pointer-capture machinery, so mock it like the
// CloudProvidersPanel tests do).
vi.mock("@/components/ui/switch", () => ({
	Switch: (props: {
		checked: boolean;
		onCheckedChange: (granted: boolean) => void;
		"aria-label"?: string;
	}) => (
		<button
			type="button"
			data-testid="consent-switch"
			role="switch"
			aria-checked={props.checked}
			aria-label={props["aria-label"]}
			onClick={() => props.onCheckedChange(!props.checked)}
		/>
	),
}));

import ConsentStep from "../ConsentStep";

// The six fields surfaced on the step (mirrors CONSENT_STEP_FIELDS in
// ConsentStep.tsx — any drift here fails the label assertions below).
const EXPECTED_FIELDS = [
	"voice_biometric_consent",
	"huggingface_consent",
	"cloud_openai_consent",
	"cloud_groq_consent",
	"cloud_deepgram_consent",
	"llm_polish_consent",
] as const;

function renderStep(
	overrides: Partial<{
		consents: Record<string, boolean>;
		onToggleConsent: (field: string, value: boolean) => void;
		onAgreeToAll: () => void;
	}> = {},
) {
	const props = {
		headingRef: () => {},
		consents: {},
		onToggleConsent: vi.fn(),
		onAgreeToAll: vi.fn(),
		...overrides,
	};
	render(
		<ConsentStep
			headingRef={props.headingRef}
			consents={props.consents}
			onToggleConsent={props.onToggleConsent}
			onAgreeToAll={props.onAgreeToAll}
		/>,
	);
	return props;
}

describe("ConsentStep — consolidated first-run consent", () => {
	afterEach(() => cleanup());

	it("renders the Privacy & Consent heading and description", () => {
		renderStep();
		expect(screen.getByText("Privacy & Consent")).toBeTruthy();
		expect(screen.getByText(/Choose what you agree to/i)).toBeTruthy();
	});

	it("renders one row per consent-gated field (all 6)", () => {
		renderStep();
		// One switch per field.
		const switches = screen.getAllByTestId("consent-switch");
		expect(switches).toHaveLength(EXPECTED_FIELDS.length);

		// Every field's plain-language label is present.
		expect(screen.getByText("Voice biometric processing")).toBeTruthy();
		expect(screen.getByText("HuggingFace model downloads")).toBeTruthy();
		expect(screen.getByText("OpenAI cloud speech recognition")).toBeTruthy();
		expect(screen.getByText("Groq cloud speech recognition")).toBeTruthy();
		expect(screen.getByText("Deepgram cloud speech recognition")).toBeTruthy();
		expect(screen.getByText("LLM text polishing")).toBeTruthy();
	});

	it("switch state reflects the consents prop", () => {
		renderStep({
			consents: {
				voice_biometric_consent: true,
				huggingface_consent: false,
			},
		});
		const switches = screen.getAllByTestId("consent-switch");
		// aria-label is the field's label text.
		const byLabel = (label: string) =>
			switches.find((s) => s.getAttribute("aria-label") === label);
		expect(
			byLabel("Voice biometric processing")?.getAttribute("aria-checked"),
		).toBe("true");
		expect(
			byLabel("HuggingFace model downloads")?.getAttribute("aria-checked"),
		).toBe("false");
		// Defaults to unchecked when the field is absent from the prop.
		expect(
			byLabel("Groq cloud speech recognition")?.getAttribute("aria-checked"),
		).toBe("false");
	});

	it("toggling a switch calls onToggleConsent with the field and new value", async () => {
		const user = userEvent.setup();
		const onToggleConsent = vi.fn();
		renderStep({ onToggleConsent });

		const switches = screen.getAllByTestId("consent-switch");
		const llm = switches.find(
			(s) => s.getAttribute("aria-label") === "LLM text polishing",
		);
		expect(llm).toBeTruthy();
		await user.click(llm as HTMLElement);

		expect(onToggleConsent).toHaveBeenCalledWith("llm_polish_consent", true);
	});

	it("Agree to All grants every consent via onAgreeToAll", async () => {
		const user = userEvent.setup();
		const onAgreeToAll = vi.fn();
		renderStep({ onAgreeToAll });

		// The button carries an aria-label ("Agree to all privacy
		// consents") which overrides its visible text as the
		// accessible name — assert via the aria-label.
		const btn = screen.getByRole("button", {
			name: "Agree to all privacy consents",
		});
		expect(btn.textContent).toBe("Agree to All");
		await user.click(btn);
		expect(onAgreeToAll).toHaveBeenCalledTimes(1);
	});
});
