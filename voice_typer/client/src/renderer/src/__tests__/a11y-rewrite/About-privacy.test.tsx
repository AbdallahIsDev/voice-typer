/**
 * vitest rewrite — behavioral test for the privacy disclosure.
 *
 * Replaces the following string-pattern Python test from
 * `tests/test_consent_and_privacy.py`:
 *   - TestAboutPageHasPrivacyDisclosure::test_about_page_has_privacy_section
 *
 * The Python test asserted on substring presence inside `About.tsx`
 * (or `en.json`) for the literals "Audio processing", "Model weights",
 * "HuggingFace", "Cloud ASR", "Voice biometrics", and "BIPA".  These
 * pass even when the privacy section is conditionally hidden, when
 * the i18n keys are mistyped, or when the disclosure is rendered in
 * a non-user-visible way.  The vitest version below mounts the real
 * Privacy page and asserts each disclosure heading is rendered into
 * the DOM as visible text.
 *
 * IA split: the privacy disclosure moved OUT of the About page (now
 * product identity) onto its own Privacy destination in the sidebar —
 * so this test mounts `@/pages/Privacy` rather than `@/pages/About`.
 *
 * The corresponding Python test is skipped via `@pytest.mark.skip`
 * with a pointer back to this file.  It is NOT deleted.
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockCall } = vi.hoisted(() => ({
	mockCall: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
}));

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: () => <span data-testid="hugeicon" />,
}));

vi.mock("@hugeicons/core-free-icons", async () => {
	const { createHugeiconsMock } = await import(
		"@/__tests__/helpers/hugeicons-mock"
	);
	return createHugeiconsMock();
});

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

import PrivacyPage from "@/pages/Privacy";

describe("Privacy disclosure — rewrite of test_about_page_has_privacy_section", () => {
	beforeEach(() => {
		mockCall.mockReset();
		// Make every IPC call return a minimal shape so Privacy
		// doesn't blow up on its optional data fetches.
		mockCall.mockImplementation((cmd: string) => {
			switch (cmd) {
				case "get_config":
					return Promise.resolve({
						theme_mode: "system",
						onboarding_completed: true,
					});
				case "get_status":
					return Promise.resolve({
						status: "idle",
						loaded_via: "",
						active_model: "",
					});
				default:
					return Promise.resolve({});
			}
		});
	});

	afterEach(() => {
		cleanup();
	});

	it("renders the privacy disclosure section with all required headings", async () => {
		render(<PrivacyPage />);

		// The Python invariant: About.tsx (or en.json)
		// contains the strings "Audio processing",
		// "Model weights", "HuggingFace", "Cloud ASR",
		// "Voice biometrics", "BIPA".  Behavioral: each
		// heading is rendered into the DOM as visible text.
		//
		// The Privacy page renders these via i18n keys
		// (about.audioProcessingTitle, etc.) whose en.json
		// values are "Audio processing", "Model weights",
		// "Cloud speech recognition", "Voice biometrics"
		// (no trailing period — the de-punctuation pass).
		await waitFor(() => {
			expect(screen.getAllByText(/Audio processing/i).length).toBeGreaterThan(
				0,
			);
		});
		// Each heading may appear in both the section title
		// and the description, so use getAllByText.
		expect(screen.getAllByText(/Model weights/i).length).toBeGreaterThan(0);
		expect(
			screen.getAllByText(/Cloud speech recognition/i).length,
		).toBeGreaterThan(0);
		expect(screen.getAllByText(/Voice biometrics/i).length).toBeGreaterThan(0);

		// Hugging Face and BIPA appear in the descriptive
		// body text under the headings (en.json values:
		// modelWeightsDesc mentions "Hugging Face";
		// voiceBiometricsDesc mentions "BIPA").
		const bodyText = document.body.textContent ?? "";
		expect(bodyText).toMatch(/Hugging Face/);
		expect(bodyText).toMatch(/BIPA/);
	});

	it("renders the privacy disclosure heading itself", async () => {
		render(<PrivacyPage />);

		// The privacy page heading is rendered via the
		// i18n key about.privacyTitle ("Privacy" in en.json).
		await waitFor(() => {
			expect(screen.getAllByText(/^Privacy$/).length).toBeGreaterThan(0);
		});
	});
});
