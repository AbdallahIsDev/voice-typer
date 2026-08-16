/**
 * ShareStatsMenu tests.
 *
 * Verifies:
 *   - The trigger is an icon-only button with the share aria-label.
 *   - The dropdown lists Download image / Copy image to clipboard /
 *     Save As… and each action wires to the corresponding handler.
 *   - Download image success shows the "Saved to Downloads" toast with
 *     a "Show in folder" action that calls revealInFolder.
 *   - Copy success shows the "Copied to clipboard" toast.
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
	ShareStatsMenu,
} from "@/components/dashboard/ShareStatsMenu";
import { TooltipProvider } from "@/components/ui/tooltip";
import { t } from "@/i18n/i18n";

/** The app renders ShareStatsMenu inside App.tsx's TooltipProvider —
 * replicate that wrapper for the component-level tests. */
function renderMenu(ui: React.ReactNode) {
	return render(<TooltipProvider>{ui}</TooltipProvider>);
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

describe("ShareStatsMenu", () => {
	afterEach(() => {
		cleanup();
		vi.clearAllMocks();
	});

	it("renders an icon-only trigger with the share aria-label", () => {
		renderMenu(<ShareStatsMenu actions={makeActions()} />);

		const button = screen.getByRole("button", { name: "Share stats" });
		expect(button).toBeTruthy();
		// Icon-only: no visible text label inside the button.
		expect(button.textContent?.trim() ?? "").toBe("");
	});

	it("disables the trigger when disabled", () => {
		renderMenu(<ShareStatsMenu actions={makeActions()} disabled />);
		expect(screen.getByRole("button", { name: "Share stats" })).toBeDisabled();
	});

	it("opens the dropdown with the three share actions", async () => {
		const user = userEvent.setup();
		renderMenu(<ShareStatsMenu actions={makeActions()} />);

		await user.click(screen.getByRole("button", { name: "Share stats" }));

		await waitFor(() => {
			expect(screen.getByText("Download image")).toBeTruthy();
			expect(screen.getByText("Copy image to clipboard")).toBeTruthy();
			expect(screen.getByText("Save As…")).toBeTruthy();
		});
	});

	it("Download image saves to Downloads and shows the toast with a Show-in-folder action", async () => {
		const user = userEvent.setup();
		const actions = makeActions();
		renderMenu(<ShareStatsMenu actions={actions} />);

		await user.click(screen.getByRole("button", { name: "Share stats" }));
		await user.click(await screen.findByText("Download image"));

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
		renderMenu(<ShareStatsMenu actions={actions} />);

		await user.click(screen.getByRole("button", { name: "Share stats" }));
		await user.click(await screen.findByText("Download image"));

		const successCall = vi
			.mocked(toast.success)
			.mock.calls.find((c) => c[0] === t("stats.shareImage.savedToDownloads"));
		const opts = successCall?.[1] as
			| { action?: { onClick?: () => void } }
			| undefined;
		expect(opts?.action).toBeDefined();
		// Fire the toast action — it must reveal the saved file.
		opts?.action?.onClick?.();
		await waitFor(() => {
			expect(actions.revealInFolder).toHaveBeenCalledWith("/tmp/x.png");
		});
	});

	it("Copy image copies via the action and toasts success", async () => {
		const user = userEvent.setup();
		const actions = makeActions();
		renderMenu(<ShareStatsMenu actions={actions} />);

		await user.click(screen.getByRole("button", { name: "Share stats" }));
		await user.click(await screen.findByText("Copy image to clipboard"));

		await waitFor(() => {
			expect(actions.copyImageToClipboard).toHaveBeenCalledWith(
				"voice-typer-stats",
			);
		});
		expect(toast.success).toHaveBeenCalledWith(
			t("stats.shareImage.copiedToClipboard"),
		);
	});

	it("Save As… opens the native dialog via the action", async () => {
		const user = userEvent.setup();
		const actions = makeActions();
		renderMenu(<ShareStatsMenu actions={actions} />);

		await user.click(screen.getByRole("button", { name: "Share stats" }));
		await user.click(await screen.findByText("Save As…"));

		await waitFor(() => {
			expect(actions.saveImageAs).toHaveBeenCalledWith("voice-typer-stats");
		});
	});

	it("a failed copy does NOT show the success toast", async () => {
		const user = userEvent.setup();
		const actions = makeActions({
			copyImageToClipboard: vi.fn().mockResolvedValue(false),
		});
		renderMenu(<ShareStatsMenu actions={actions} />);

		await user.click(screen.getByRole("button", { name: "Share stats" }));
		await user.click(await screen.findByText("Copy image to clipboard"));

		await waitFor(() => {
			expect(actions.copyImageToClipboard).toHaveBeenCalled();
		});
		expect(toast.success).not.toHaveBeenCalledWith(
			t("stats.shareImage.copiedToClipboard"),
		);
	});
});
