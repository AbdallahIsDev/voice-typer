/**
 * ShareStatsDialog tests.
 *
 * Verifies:
 *   - The trigger is an icon-only button with the share aria-label.
 *   - Opening the dialog shows the preview + the three export actions
 *     (single-line "Copy to clipboard", "Save As" without ellipsis)
 *     and the four social share targets.
 *   - Download image success shows the "Saved to Downloads" toast with
 *     a "Show in folder" action that calls revealInFolder.
 *   - Copy success shows the "Copied to clipboard" toast.
 *   - Social share: copies the image, opens the platform composer URL,
 *     and toasts the "paste into the composer" instruction.
 *   - The trigger is disabled when the `disabled` prop is set.
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({
	toast: {
		success: vi.fn(),
		error: vi.fn(),
		warning: vi.fn(),
	},
}));

import { toast } from "sonner";
import {
	type ShareStatsActions,
	ShareStatsDialog,
} from "@/components/dashboard/ShareStatsDialog";
import { t } from "@/i18n/i18n";
import { FALLBACK_THEME_PALETTE } from "@/lib/theme-palette";
import type { ShareStats } from "@/types/stats";

const TEST_STATS: ShareStats = {
	wpm: 92,
	wpmDisplay: "92",
	minutesSaved: 18,
	minutesSavedDisplay: "18",
	modeDisplay: "Offline",
	modeDetail: "Local Model",
	fasterThanAvg: "120% faster than avg typer",
	hasTodayActivity: true,
	dictations: "42",
	activeDays: "12",
	activeDaysDetail: "5-day streak",
	chars: "8400",
	recordingTime: "1h 12m",
	model: "Tiny",
	device: "GPU",
};

function renderDialog(ui: React.ReactNode) {
	return render(ui);
}

function makeActions(
	overrides?: Partial<ShareStatsActions>,
): ShareStatsActions {
	return {
		downloadImage: vi.fn().mockResolvedValue("/tmp/x.png"),
		saveImageAs: vi.fn().mockResolvedValue(null),
		copyImageToClipboard: vi.fn().mockResolvedValue(true),
		revealInFolder: vi.fn().mockResolvedValue(undefined),
		...overrides,
	};
}

describe("ShareStatsDialog", () => {
	afterEach(() => {
		cleanup();
		vi.clearAllMocks();
		vi.restoreAllMocks();
	});

	it("renders an icon-only trigger with the share aria-label", () => {
		renderDialog(
			<ShareStatsDialog
				actions={makeActions()}
				stats={TEST_STATS}
				palette={FALLBACK_THEME_PALETTE}
			/>,
		);

		const button = screen.getByRole("button", { name: "Share stats" });
		expect(button).toBeTruthy();
		// Icon-only: no visible text label inside the button.
		expect(button.textContent?.trim() ?? "").toBe("");
	});

	it("disables the trigger when disabled", () => {
		renderDialog(
			<ShareStatsDialog
				actions={makeActions()}
				stats={TEST_STATS}
				palette={FALLBACK_THEME_PALETTE}
				disabled
			/>,
		);
		expect(screen.getByRole("button", { name: "Share stats" })).toBeDisabled();
	});

	it("opens the preview popup with the export actions and social targets", async () => {
		const user = userEvent.setup();
		renderDialog(
			<ShareStatsDialog
				actions={makeActions()}
				stats={TEST_STATS}
				palette={FALLBACK_THEME_PALETTE}
			/>,
		);

		await user.click(screen.getByRole("button", { name: "Share stats" }));

		await waitFor(() => {
			expect(screen.getByText(t("stats.shareImage.previewTitle"))).toBeTruthy();
			// Single-line labels (no wrapping): short copy + no ellipsis.
			expect(
				screen.getByText(t("stats.shareImage.downloadImage")),
			).toBeTruthy();
			expect(screen.getByText(t("stats.shareImage.copyImage"))).toBeTruthy();
			expect(screen.getByText(t("stats.shareImage.saveAs"))).toBeTruthy();
			expect(t("stats.shareImage.copyImage")).toBe("Copy to clipboard");
			expect(t("stats.shareImage.saveAs")).toBe("Save As");
			// Social targets.
			for (const key of [
				"stats.shareImage.socialWhatsapp",
				"stats.shareImage.socialTelegram",
				"stats.shareImage.socialTwitter",
				"stats.shareImage.socialFacebook",
			]) {
				expect(screen.getByText(t(key))).toBeTruthy();
			}
		});
	});

	it("Download image saves to Downloads and shows the toast with a Show-in-folder action", async () => {
		const user = userEvent.setup();
		const actions = makeActions();
		renderDialog(
			<ShareStatsDialog
				actions={actions}
				stats={TEST_STATS}
				palette={FALLBACK_THEME_PALETTE}
			/>,
		);

		await user.click(screen.getByRole("button", { name: "Share stats" }));
		await user.click(
			await screen.findByText(t("stats.shareImage.downloadImage")),
		);

		await waitFor(() => {
			expect(actions.downloadImage).toHaveBeenCalledWith("voice-typer-stats");
		});
		expect(toast.success).toHaveBeenCalledWith(
			t("stats.shareImage.savedToDownloads"),
			{
				action: {
					label: t("stats.shareImage.showInFolder"),
					onClick: expect.any(Function),
				},
			},
		);
	});

	it("Show in folder reveals the saved path", async () => {
		const user = userEvent.setup();
		const actions = makeActions();
		renderDialog(
			<ShareStatsDialog
				actions={actions}
				stats={TEST_STATS}
				palette={FALLBACK_THEME_PALETTE}
			/>,
		);

		await user.click(screen.getByRole("button", { name: "Share stats" }));
		await user.click(
			await screen.findByText(t("stats.shareImage.downloadImage")),
		);

		const successCall = vi
			.mocked(toast.success)
			.mock.calls.find((c) => c[0] === t("stats.shareImage.savedToDownloads"));
		const opts = successCall?.[1] as
			| { action?: { onClick?: () => void } }
			| undefined;
		expect(opts?.action).toBeDefined();
		opts?.action?.onClick?.();
		await waitFor(() => {
			expect(actions.revealInFolder).toHaveBeenCalledWith("/tmp/x.png");
		});
	});

	it("Copy image copies via the action and toasts success", async () => {
		const user = userEvent.setup();
		const actions = makeActions();
		renderDialog(
			<ShareStatsDialog
				actions={actions}
				stats={TEST_STATS}
				palette={FALLBACK_THEME_PALETTE}
			/>,
		);

		await user.click(screen.getByRole("button", { name: "Share stats" }));
		await user.click(await screen.findByText(t("stats.shareImage.copyImage")));

		await waitFor(() => {
			expect(actions.copyImageToClipboard).toHaveBeenCalledWith(
				"voice-typer-stats",
			);
		});
		expect(toast.success).toHaveBeenCalledWith(
			t("stats.shareImage.copiedToClipboard"),
		);
	});

	it("Save As opens the native dialog via the action", async () => {
		const user = userEvent.setup();
		const actions = makeActions();
		renderDialog(
			<ShareStatsDialog
				actions={actions}
				stats={TEST_STATS}
				palette={FALLBACK_THEME_PALETTE}
			/>,
		);

		await user.click(screen.getByRole("button", { name: "Share stats" }));
		await user.click(await screen.findByText(t("stats.shareImage.saveAs")));

		await waitFor(() => {
			expect(actions.saveImageAs).toHaveBeenCalledWith("voice-typer-stats");
		});
	});

	it("a failed copy does NOT show the success toast", async () => {
		const user = userEvent.setup();
		const actions = makeActions({
			copyImageToClipboard: vi.fn().mockResolvedValue(false),
		});
		renderDialog(
			<ShareStatsDialog
				actions={actions}
				stats={TEST_STATS}
				palette={FALLBACK_THEME_PALETTE}
			/>,
		);

		await user.click(screen.getByRole("button", { name: "Share stats" }));
		await user.click(await screen.findByText(t("stats.shareImage.copyImage")));

		await waitFor(() => {
			expect(actions.copyImageToClipboard).toHaveBeenCalled();
		});
		expect(toast.success).not.toHaveBeenCalledWith(
			t("stats.shareImage.copiedToClipboard"),
		);
	});

	it("preview frame matches the image's aspect ratio and scales without dead space", async () => {
		const user = userEvent.setup();
		renderDialog(
			<ShareStatsDialog
				actions={makeActions()}
				stats={TEST_STATS}
				palette={FALLBACK_THEME_PALETTE}
			/>,
		);

		await user.click(screen.getByRole("button", { name: "Share stats" }));
		const dialog = await screen.findByRole("dialog");

		// The frame is sized by CSS aspect-ratio (the export's fixed
		// 1200:630 shape) — its height is correct from the first frame
		// with no JS measurement, so the preview can never clip or
		// leave dead space (Part E).
		const previewFrame = dialog.querySelector(
			".overflow-hidden.rounded-xl",
		) as HTMLElement | null;
		expect(previewFrame).toBeTruthy();
		expect(previewFrame?.style.aspectRatio ?? "").toBe("1200 / 630");
		// The scaled image is taken out of flow (absolute) so its
		// layout box can never size the dialog; it scales by the
		// --preview-scale custom property (written on the frame before
		// first paint) from the top-left, filling the frame exactly.
		const scaled = previewFrame?.firstElementChild as HTMLElement | null;
		expect(scaled?.className).toContain("absolute");
		expect(scaled?.style.transform ?? "").toContain("--preview-scale");
		expect(scaled?.style.transformOrigin ?? "").toBe("top left");
		// No spacer child — the frame's only child is the absolute
		// preview.
		expect(previewFrame?.children.length).toBe(1);

		// The action buttons sit in a framed container (rounded +
		// border + padding), not as full-bleed fragments.
		const downloadButton = screen.getByText(
			t("stats.shareImage.downloadImage"),
		);
		const actionsFrame = downloadButton.closest("div.rounded-xl");
		expect(actionsFrame).toBeTruthy();
		expect(actionsFrame?.className).toContain("p-3");
	});

	it("Telegram share URL includes the required url param (t.me/share/url?url=...&text=...)", async () => {
		const user = userEvent.setup();
		const actions = makeActions();
		const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
		renderDialog(
			<ShareStatsDialog
				actions={actions}
				stats={TEST_STATS}
				palette={FALLBACK_THEME_PALETTE}
			/>,
		);

		await user.click(screen.getByRole("button", { name: "Share stats" }));
		await user.click(
			await screen.findByText(t("stats.shareImage.socialTelegram")),
		);

		await waitFor(() => {
			expect(actions.copyImageToClipboard).toHaveBeenCalled();
		});
		// Regression: the URL previously omitted the `url` param
		// (t.me/share/url?text=...) — Telegram's web handler redirects
		// to telegram.org instead of the share picker. The `url` param
		// is required for the app/forward flow to open.
		const opened = openSpy.mock.calls[0]?.[0] as string;
		expect(opened).toMatch(/^https:\/\/t\.me\/share\/url\?url=/);
		expect(opened).toContain("&text=");
		expect(opened).toContain(encodeURIComponent("github.com/AbdallahIsDev"));
	});

	it("social share copies the image, opens the composer, and tells the user to paste", async () => {
		const user = userEvent.setup();
		const actions = makeActions();
		const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
		renderDialog(
			<ShareStatsDialog
				actions={actions}
				stats={TEST_STATS}
				palette={FALLBACK_THEME_PALETTE}
			/>,
		);

		await user.click(screen.getByRole("button", { name: "Share stats" }));
		await user.click(
			await screen.findByText(t("stats.shareImage.socialWhatsapp")),
		);

		await waitFor(() => {
			expect(actions.copyImageToClipboard).toHaveBeenCalledWith(
				"voice-typer-stats",
			);
		});
		// Composer opened with the prefilled caption.
		expect(openSpy).toHaveBeenCalledWith(
			expect.stringContaining("wa.me"),
			"_blank",
			expect.any(String),
		);
		expect(toast.success).toHaveBeenCalledWith(
			t("stats.shareImage.socialCopiedTo", {
				platform: t("stats.shareImage.socialWhatsapp"),
			}),
		);
	});
});
