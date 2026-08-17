/**
 * Tests for the  fix on the About page.
 *
 * Scenario under test: the previous About page rendered a "Full Privacy
 * Policy" button in the Privacy section footer that pointed at the same
 * SECURITY.md URL as the "Security Policy" button in the Resources
 * section. The two byte-identical buttons were confusing UX — users
 * clicked "Full Privacy Policy" expecting a privacy-specific document
 * and landed on the security policy instead.
 *
 *  removes the duplicate button and adds a one-line note in the
 * Privacy section body explaining that the Security Policy (linked in
 * the Resources section below) covers privacy practices too.
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// Shared stable-mocks preamble (see helpers/stableMocks.tsx): the
// assertable singletons + one vi.mock line per module.
import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
	nextThemesMock,
	pythonMock,
	sonnerMock,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";

const { mockCall } = stableMocks;

vi.mock("@/hooks/usePython", () => pythonMock());
vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());
vi.mock("sonner", () => sonnerMock());
vi.mock("next-themes", () => nextThemesMock());

describe("About page — BG-59 privacy URL fix", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockCall.mockImplementation((type: string) => {
			if (type === "get_status") {
				return Promise.resolve({
					status: "idle",
					config_dir: "/tmp",
					loaded_via: "cpu/int8/tiny.en",
				});
			}
			if (type === "get_config") {
				return Promise.resolve({
					asr_backend: "whisper",
					model_size: "large-v3-turbo",
					device: "cpu",
					hotkey: "F2",
					microphone: null,
				});
			}
			return Promise.resolve({});
		});
	});

	afterEach(() => {
		cleanup();
	});

	it("does NOT render the 'Full Privacy Policy' button (removed — duplicate of Security Policy)", async () => {
		const { default: AboutPage } = await import("@/pages/About");
		render(<AboutPage />);

		await waitFor(() => {
			expect(screen.getByRole("heading", { name: "Privacy" })).toBeTruthy();
		});

		// The "Full Privacy Policy" button is gone — the i18n key
		// (about.fullPrivacyPolicy) was removed from every locale
		// (unused dead key, cleaned up with the note removal), and
		// the UI renders no "Full Privacy Policy" surface at all.
		expect(screen.queryByText("Full Privacy Policy")).toBeNull();
	});

	it("does NOT render the 'See the full privacy policy below' note (removed — the full privacy content is already shown inline above it)", async () => {
		const { default: AboutPage } = await import("@/pages/About");
		render(<AboutPage />);

		await waitFor(() => {
			expect(screen.getByRole("heading", { name: "Privacy" })).toBeTruthy();
		});

		// The trailing "Privacy policy — See the full privacy policy
		// below…" line pointed at nothing (the full disclosure is
		// rendered in the rows above it). Both the label and the note
		// are gone; the Security Policy link lives in Resources.
		expect(screen.queryByText("Privacy policy")).toBeNull();
		expect(screen.queryByText(/See the full privacy policy below/)).toBeNull();
	});

	it("still renders the Security Policy button in the Resources section", async () => {
		const { default: AboutPage } = await import("@/pages/About");
		render(<AboutPage />);

		await waitFor(() => {
			expect(
				screen.getByRole("heading", { name: "Resources & Feedback" }),
			).toBeTruthy();
		});

		// The Security Policy button is still rendered in Resources.
		// (This is the canonical place to surface SECURITY.md now that
		// the Privacy-section duplicate has been removed.)
		expect(screen.getByText("Security Policy")).toBeTruthy();
	});

	it("renders exactly ONE anchor pointing at SECURITY.md (the Resources-section Security Policy button)", async () => {
		const { default: AboutPage } = await import("@/pages/About");
		const { container } = render(<AboutPage />);

		await waitFor(() => {
			expect(
				screen.getByRole("heading", { name: "Resources & Feedback" }),
			).toBeTruthy();
		});

		//Before , two anchors pointed at SECURITY.md (one in the
		//Privacy footer, one in Resources). After , only one
		// anchor should — the Resources-section Security Policy link.
		const securityAnchors = container.querySelectorAll(
			'a[href*="SECURITY.md"]',
		);
		expect(securityAnchors.length).toBe(1);
	});
});
