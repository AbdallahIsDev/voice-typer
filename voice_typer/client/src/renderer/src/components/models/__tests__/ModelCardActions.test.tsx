/**
 * ModelCardActions unit tests —  /
 *
 * Coverage:
 *   1. All five visual states render the correct button label + icon:
 *      - Branch 1a: Active + available (disabled "Active" tick, NO Delete —
 *        the backend refuses to delete an in-use model, so a Delete
 *        button would be a dead-end).
 *      - Branch 4: Deps-installable, not depsOk ("Download Deps" button;
 *        Delete only when the model is downloaded).
 *      - Branch 2: Not downloaded ("Download" button, NO Delete — a
 *        not-installed model has nothing to remove, even when it is the
 *        active default like small.en before first download).
 *      - Branch 3: Downloaded ("Select" button + Delete).
 *   2. : the Download + Download Deps buttons expose aria-busy=true
 *      while their respective async action is in-flight, and swap their
 *      aria-label to the "Downloading…" string so SR users hear the
 *      in-progress state (not the stale per-model label).
 *   3.  #8: the oneAtATimeTitle() English fallback is GONE — the
 *      disabled-button title is sourced directly from
 *      `t("models.download.oneAtATime")` (which IS in the catalog).
 *   4.  #9: the Select button uses Tick02Icon (not PlayIcon) —
 *      Select is a "mark active" affordance, not a "play media" one.
 *   5. DeleteButton renders in Branch 3 (downloaded) and Branch 4
 *      (downloaded deps-required); NEVER for a not-downloaded model.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ModelCardActions } from "@/components/models/ModelCardActions";
import type { ModelInfo } from "@/lib/utils/models";

// Mock the HugeiconsIcon wrapper so we can assert which icon was used via
// the data-name attribute (without pulling in the SVG renderer). The
// className is forwarded so tests can assert icon sizing classes.
vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: ({
		children,
		icon,
		className,
	}: {
		children?: React.ReactNode;
		icon?: { name?: string };
		className?: string;
	}) => (
		<span data-testid="hugeicon" data-name={icon?.name} className={className}>
			{children}
		</span>
	),
}));

vi.mock("@hugeicons/core-free-icons", async () => {
	const { createHugeiconsMock } = await import(
		"@/__tests__/helpers/hugeicons-mock"
	);
	return createHugeiconsMock();
});

const baseModel: ModelInfo = {
	name: "tiny",
	size: "~466MB",
	speed: "Fast",
	backend: "whisper",
	downloaded: false,
	depsOk: true,
	isActive: false,
};

const noop = vi.fn();

describe("ModelCardActions — visual states (4 branches)", () => {
	afterEach(() => {
		cleanup();
	});

	it("Branch 1a (Active + downloaded): renders disabled Active tick with NO Delete icon", () => {
		render(
			<ModelCardActions
				model={{ ...baseModel, isActive: true, downloaded: true }}
				isSelectingThis={false}
				isDownloadingThis={false}
				anyDownloading={false}
				onSelect={noop}
				onDownload={noop}
				onDelete={noop}
			/>,
		);
		// Active button is disabled.
		const activeBtn = screen.getByRole("button", {
			name: /Active: tiny/i,
		});
		expect(activeBtn).toBeDisabled();
		// Uses the Tick02Icon (not PlayIcon — Select/Active are tick affordances).
		expect(screen.getAllByTestId("hugeicon")[0]).toHaveAttribute(
			"data-name",
			"Tick02Icon",
		);
		// STALE-ACTIVE: NO Delete icon on an in-use model — the backend
		// refuses to delete it ("Cannot delete the active model"), so a
		// Delete button here would always error. Users switch first.
		expect(screen.queryByRole("button", { name: /Delete tiny/i })).toBeNull();
	});

	it("Branch 2 (Active + missing from disk): renders ONLY Download — no Delete for a not-installed model", () => {
		render(
			<ModelCardActions
				model={{ ...baseModel, isActive: true, downloaded: false }}
				isSelectingThis={false}
				isDownloadingThis={false}
				anyDownloading={false}
				onSelect={noop}
				onDownload={noop}
				onDelete={noop}
			/>,
		);
		// The active model is missing from disk (e.g. the default
		// `small.en` before the user downloads anything). Offer Download
		// (restore it) but NO Delete — a not-installed model has nothing
		// to remove, and a trash icon next to "Download" falsely implies
		// an installed model.
		const dlBtn = screen.getByRole("button", {
			name: /Download tiny/i,
		});
		// The Download button is icon-only (2026-08-15 user request):
		// the download icon + the downloadAria label carry the
		// affordance, no visible "Download" text.
		expect(dlBtn.querySelector('[data-testid="hugeicon"]')).toHaveAttribute(
			"data-name",
			"Download01Icon",
		);
		expect(dlBtn).not.toHaveTextContent("Download");
		// No disabled "Active" tick anymore.
		expect(screen.queryByRole("button", { name: /Active: tiny/i })).toBeNull();
		// NO Delete affordance for a not-downloaded model.
		expect(screen.queryByRole("button", { name: /Delete tiny/i })).toBeNull();
	});

	it("Branch 1 (Active + downloaded): hides Delete icon (in-use model can't be deleted)", () => {
		render(
			<ModelCardActions
				model={{ ...baseModel, isActive: true, downloaded: true }}
				isSelectingThis={false}
				isDownloadingThis={false}
				anyDownloading={false}
				onSelect={noop}
				onDownload={noop}
				onDelete={noop}
			/>,
		);
		expect(screen.queryByRole("button", { name: /Delete tiny/i })).toBeNull();
	});

	it("Branch 4 (Deps-installable): renders 'Download Deps' button with depsAria label", () => {
		render(
			<ModelCardActions
				model={{
					...baseModel,
					depsInstallable: true,
					depsOk: false,
				}}
				isSelectingThis={false}
				isDownloadingThis={false}
				anyDownloading={false}
				onSelect={noop}
				onDownload={noop}
				onDelete={noop}
				onInstallDeps={noop}
			/>,
		);
		const depsBtn = screen.getByRole("button", {
			name: /Download dependencies for tiny/i,
		});
		expect(depsBtn).toHaveTextContent("Download Deps");
		expect(depsBtn).not.toHaveAttribute("aria-busy", "true");
		// Not downloaded → no Delete icon (nothing to remove).
		expect(screen.queryByRole("button", { name: /Delete tiny/i })).toBeNull();
	});

	it("Branch 2 (Not downloaded): renders 'Download' button with downloadAria label", () => {
		render(
			<ModelCardActions
				model={{ ...baseModel, downloaded: false }}
				isSelectingThis={false}
				isDownloadingThis={false}
				anyDownloading={false}
				onSelect={noop}
				onDownload={noop}
				onDelete={noop}
			/>,
		);
		const dlBtn = screen.getByRole("button", {
			name: /Download tiny/i,
		});
		// Icon-only: the download icon is present, no visible "Download"
		// text (2026-08-15 user request).
		expect(dlBtn.querySelector('[data-testid="hugeicon"]')).toHaveAttribute(
			"data-name",
			"Download01Icon",
		);
		expect(dlBtn).not.toHaveTextContent("Download");
		expect(dlBtn).not.toHaveAttribute("aria-busy", "true");
	});

	it("Branch 3 (Downloaded): renders 'Select' button using Tick02Icon (not PlayIcon)", () => {
		render(
			<ModelCardActions
				model={{ ...baseModel, downloaded: true }}
				isSelectingThis={false}
				isDownloadingThis={false}
				anyDownloading={false}
				onSelect={noop}
				onDownload={noop}
				onDelete={noop}
			/>,
		);
		const selectBtn = screen.getByRole("button", {
			name: /Select tiny/i,
		});
		expect(selectBtn).toHaveTextContent("Select");
		//#9: Select uses Tick02Icon (was PlayIcon — semantically wrong).
		// Branch 3 renders BOTH a Select button and a Delete button, so we
		// scope the icon assertion to the Select button itself.
		expect(selectBtn.querySelector('[data-testid="hugeicon"]')).toHaveAttribute(
			"data-name",
			"Tick02Icon",
		);
		// Downloaded → Delete button also rendered.
		expect(
			screen.getByRole("button", { name: /Delete tiny/i }),
		).toBeInTheDocument();
	});
});

describe("ModelCardActions — BG-76 (aria-busy + aria-label swap on async buttons)", () => {
	afterEach(() => {
		cleanup();
	});

	it("Download button exposes aria-busy=true and swaps aria-label to 'Downloading…' while in-flight", () => {
		render(
			<ModelCardActions
				model={{ ...baseModel, downloaded: false }}
				isSelectingThis={false}
				isDownloadingThis={true}
				anyDownloading={true}
				onSelect={noop}
				onDownload={noop}
				onDelete={noop}
			/>,
		);
		const dlBtn = screen.getByRole("button", { name: /Downloading…/i });
		expect(dlBtn).toHaveAttribute("aria-busy", "true");
		// Icon-only: the in-flight state is the SPINNING download icon,
		// not visible text (2026-08-15 user request).
		expect(dlBtn.querySelector('[data-testid="hugeicon"]')).toHaveAttribute(
			"data-name",
			"Download01Icon",
		);
		expect(dlBtn).not.toHaveTextContent("Downloading…");
		// The stale per-model aria-label is NOT used while in-flight.
		expect(dlBtn.getAttribute("aria-label")).not.toMatch(/Download tiny/);
	});

	it("Download Deps button exposes aria-busy=true and swaps aria-label to 'Downloading…' while deps-install in-flight", () => {
		render(
			<ModelCardActions
				model={{
					...baseModel,
					depsInstallable: true,
					depsOk: false,
				}}
				isSelectingThis={false}
				isDownloadingThis={false}
				anyDownloading={true}
				isInstallingDepsThis={true}
				onSelect={noop}
				onDownload={noop}
				onDelete={noop}
				onInstallDeps={noop}
			/>,
		);
		const depsBtn = screen.getByRole("button", { name: /Downloading…/i });
		expect(depsBtn).toHaveAttribute("aria-busy", "true");
		expect(depsBtn).toHaveTextContent("Downloading…");
	});

	it("Select button exposes aria-busy=true and swaps aria-label to 'Selecting…' while in-flight", () => {
		render(
			<ModelCardActions
				model={{ ...baseModel, downloaded: true }}
				isSelectingThis={true}
				isDownloadingThis={false}
				anyDownloading={false}
				onSelect={noop}
				onDownload={noop}
				onDelete={noop}
			/>,
		);
		const selectBtn = screen.getByRole("button", { name: /Selecting…/i });
		expect(selectBtn).toHaveAttribute("aria-busy", "true");
		expect(selectBtn).toBeDisabled();
	});
});

describe("ModelCardActions — BG-R16 #8 (oneAtATimeTitle fallback removed)", () => {
	afterEach(() => {
		cleanup();
	});

	it("disabled Download button title is sourced from models.download.oneAtATime (no English fallback)", () => {
		render(
			<ModelCardActions
				model={{ ...baseModel, downloaded: false }}
				isSelectingThis={false}
				isDownloadingThis={false}
				anyDownloading={true}
				onSelect={noop}
				onDownload={noop}
				onDelete={noop}
			/>,
		);
		const dlBtn = screen.getByRole("button", { name: /Download tiny/i });
		// Catalog value: "Only one download at a time — wait for the current
		// download to finish or cancel it". Assert the localized sentence is
		// present (NOT the dead-code English fallback "Only one download at a
		// time" which would miss the em-dash + explanatory suffix).
		const title = dlBtn.getAttribute("title") ?? "";
		expect(title).toContain("Only one download at a time");
		expect(title).toContain("cancel it");
	});

	it("disabled Download Deps button title is also sourced from models.download.oneAtATime", () => {
		render(
			<ModelCardActions
				model={{
					...baseModel,
					depsInstallable: true,
					depsOk: false,
				}}
				isSelectingThis={false}
				isDownloadingThis={false}
				anyDownloading={true}
				onSelect={noop}
				onDownload={noop}
				onDelete={noop}
				onInstallDeps={noop}
			/>,
		);
		const depsBtn = screen.getByRole("button", {
			name: /Download dependencies for tiny/i,
		});
		const title = depsBtn.getAttribute("title") ?? "";
		expect(title).toContain("Only one download at a time");
		expect(title).toContain("cancel it");
	});
});

describe("ModelCardActions — DeleteButton rendering", () => {
	afterEach(() => {
		cleanup();
	});

	it("Branch 4 (Deps-installable, NOT downloaded): renders Download Deps with NO Delete", () => {
		render(
			<ModelCardActions
				model={{
					...baseModel,
					name: "parakeet",
					backend: "parakeet",
					isActive: true,
					downloaded: false,
					depsInstallable: true,
					depsOk: false,
				}}
				isSelectingThis={false}
				isDownloadingThis={false}
				anyDownloading={false}
				onSelect={noop}
				onDownload={noop}
				onDelete={noop}
				onInstallDeps={noop}
			/>,
		);
		// "Download Deps" is the restore affordance...
		expect(
			screen.getByRole("button", {
				name: /Download dependencies for parakeet/i,
			}),
		).toBeInTheDocument();
		// ...but the model is NOT on disk, so there is nothing to delete.
		expect(
			screen.queryByRole("button", { name: /Delete parakeet/i }),
		).toBeNull();
	});

	it("Branch 4 (Deps-installable, DOWNLOADED): renders Download Deps WITH Delete", () => {
		render(
			<ModelCardActions
				model={{
					...baseModel,
					name: "parakeet",
					backend: "parakeet",
					isActive: false,
					downloaded: true,
					depsInstallable: true,
					depsOk: false,
				}}
				isSelectingThis={false}
				isDownloadingThis={false}
				anyDownloading={false}
				onSelect={noop}
				onDownload={noop}
				onDelete={noop}
				onInstallDeps={noop}
			/>,
		);
		expect(
			screen.getByRole("button", {
				name: /Download dependencies for parakeet/i,
			}),
		).toBeInTheDocument();
		// Downloaded → the model CAN be removed.
		expect(
			screen.getByRole("button", { name: /Delete parakeet/i }),
		).toBeInTheDocument();
	});

	it("Qwen Branch 4 (deps-installable, DOWNLOADED): renders Download Deps (not Select) WITH Delete — qwen_asr missing but weights on disk", () => {
		render(
			<ModelCardActions
				model={{
					...baseModel,
					name: "qwen",
					backend: "qwen",
					isActive: false,
					downloaded: true,
					depsInstallable: true,
					depsOk: false,
				}}
				isSelectingThis={false}
				isDownloadingThis={false}
				anyDownloading={false}
				onSelect={noop}
				onDownload={noop}
				onDelete={noop}
				onInstallDeps={noop}
			/>,
		);
		// The qwen_asr pip dependency is missing, so the card offers
		// "Download Deps" — NOT the "Select" button (which would load a
		// model whose engine can't import).
		expect(
			screen.getByRole("button", {
				name: /Download dependencies for qwen/i,
			}),
		).toBeInTheDocument();
		expect(screen.queryByRole("button", { name: /Select qwen/i })).toBeNull();
		// Weights ARE on disk → the model can be removed.
		expect(
			screen.getByRole("button", { name: /Delete qwen/i }),
		).toBeInTheDocument();
	});

	it("Qwen Branch 4 (deps-installable, NOT downloaded): renders Download Deps with NO Delete", () => {
		render(
			<ModelCardActions
				model={{
					...baseModel,
					name: "qwen",
					backend: "qwen",
					isActive: false,
					downloaded: false,
					depsInstallable: true,
					depsOk: false,
				}}
				isSelectingThis={false}
				isDownloadingThis={false}
				anyDownloading={false}
				onSelect={noop}
				onDownload={noop}
				onDelete={noop}
				onInstallDeps={noop}
			/>,
		);
		expect(
			screen.getByRole("button", {
				name: /Download dependencies for qwen/i,
			}),
		).toBeInTheDocument();
		// Nothing on disk → nothing to delete.
		expect(screen.queryByRole("button", { name: /Delete qwen/i })).toBeNull();
	});

	it("DeleteButton is hidden in Branch 2 (not-downloaded, non-active)", () => {
		render(
			<ModelCardActions
				model={{ ...baseModel, downloaded: false }}
				isSelectingThis={false}
				isDownloadingThis={false}
				anyDownloading={false}
				onSelect={noop}
				onDownload={noop}
				onDelete={noop}
			/>,
		);
		expect(screen.queryByRole("button", { name: /Delete tiny/i })).toBeNull();
	});

	it("Branch 3 (downloaded): Delete button IS rendered for a downloaded model (the trash affordance = installed + removable)", () => {
		render(
			<ModelCardActions
				model={{ ...baseModel, downloaded: true }}
				isSelectingThis={false}
				isDownloadingThis={false}
				anyDownloading={false}
				onSelect={noop}
				onDownload={noop}
				onDelete={noop}
			/>,
		);
		expect(
			screen.getByRole("button", { name: /Delete tiny/i }),
		).toBeInTheDocument();
	});

	it("clicking DeleteButton invokes onDelete", () => {
		const onDelete = vi.fn();
		render(
			<ModelCardActions
				model={{ ...baseModel, downloaded: true }}
				isSelectingThis={false}
				isDownloadingThis={false}
				anyDownloading={false}
				onSelect={noop}
				onDownload={noop}
				onDelete={onDelete}
			/>,
		);
		screen.getByRole("button", { name: /Delete tiny/i }).click();
		expect(onDelete).toHaveBeenCalledTimes(1);
	});
});

describe("ModelCardActions — download button size display + fixed width (2026-08-21)", () => {
	afterEach(() => {
		cleanup();
	});

	it("Branch 2 Download button shows the normalized size (no ~, number + space + unit)", () => {
		render(
			<ModelCardActions
				model={{ ...baseModel, size: "~466MB", downloaded: false }}
				isSelectingThis={false}
				isDownloadingThis={false}
				anyDownloading={false}
				onSelect={noop}
				onDownload={noop}
				onDelete={noop}
			/>,
		);
		const dlBtn = screen.getByRole("button", { name: /Download tiny/i });
		// The visible size is the canonical "466 MB" — the `~` is gone
		// and a space separates the number from the unit.
		expect(dlBtn).toHaveTextContent("466 MB");
		expect(dlBtn).not.toHaveTextContent("~");
		expect(dlBtn).not.toHaveTextContent("466MB");
	});

	it("download buttons are left-aligned so icon + text share one start position", () => {
		render(
			<ModelCardActions
				model={{ ...baseModel, size: "75 MB", downloaded: false }}
				isSelectingThis={false}
				isDownloadingThis={false}
				anyDownloading={false}
				onSelect={noop}
				onDownload={noop}
				onDelete={noop}
			/>,
		);
		const dlBtn = screen.getByRole("button", { name: /Download tiny/i });
		// `justify-start` (the shared DOWNLOAD_CONTENT_ALIGNMENT token)
		// overrides the Button base's centered `justify-center`.
		expect(dlBtn.className).toContain("justify-start");
		// The download icon is the compact 14px size (DOWNLOAD_ICON_CLASS)
		// so it visually matches the 11px size text instead of dominating.
		const icon = dlBtn.querySelector('[data-testid="hugeicon"]');
		expect(icon?.className).toContain("h-3.5");
		expect(icon?.className).toContain("w-3.5");
	});

	it("every model-size Download button shares one fixed width, regardless of the size shown", () => {
		const { rerender } = render(
			<ModelCardActions
				model={{ ...baseModel, size: "75 MB", downloaded: false }}
				isSelectingThis={false}
				isDownloadingThis={false}
				anyDownloading={false}
				onSelect={noop}
				onDownload={noop}
				onDelete={noop}
			/>,
		);
		const buttonFor = (size: string) => {
			rerender(
				<ModelCardActions
					model={{ ...baseModel, size, downloaded: false }}
					isSelectingThis={false}
					isDownloadingThis={false}
					anyDownloading={false}
					onSelect={noop}
					onDownload={noop}
					onDelete={noop}
				/>,
			);
			return screen.getByRole("button", { name: /Download tiny/i });
		};

		// The shared DOWNLOAD_SIZE_BUTTON_WIDTH token must be applied to
		// every size button so "75 MB" / "809 MB" / "3 GB" render with
		// identical width + alignment.
		const widths = ["75 MB", "809 MB", "3 GB", "2.5 GB"].map(
			(size) => buttonFor(size).className,
		);
		expect(widths.every((cls) => cls.includes("w-[88px]"))).toBe(true);
	});
});
