/**
 * ModelCardActions unit tests —  /
 *
 * Coverage:
 *   1. All visual states render the correct button label + icon:
 *      - Branch 1a: Active + available (disabled "Active" tick + Delete —
 *        ACTIVE-DELETE: the backend removes the files and reassigns the
 *        selection, so deleting the active model is allowed).
 *      - Branch 2: Not downloaded ("Download" button, NO Delete — a
 *        not-installed model has nothing to remove, even when it is the
 *        active default like small.en before first download).
 *      - Branch 3: Downloaded ("Select" button + Delete).
 *   2. : the Download button exposes aria-busy=true
 *      while its async action is in-flight,
 *      aria-label to the "Downloading…" string so SR users hear the
 *      in-progress state (not the stale per-model label).
 *   3.  #8: the oneAtATimeTitle() English fallback is GONE — the
 *      disabled-button title is sourced directly from
 *      `t("models.download.oneAtATime")` (which IS in the catalog).
 *   4.  #9: the Select button uses Tick02Icon (not PlayIcon) —
 *      Select is a "mark active" affordance, not a "play media" one.
 *   5. DeleteButton renders in Branch 3 (downloaded); NEVER for a
 *      not-downloaded model.
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

describe("ModelCardActions — visual states (3 branches)", () => {
	afterEach(() => {
		cleanup();
	});

	it("Branch 1a (Active + downloaded): renders disabled Active tick WITH Delete icon (ACTIVE-DELETE)", () => {
		const onDelete = vi.fn();
		render(
			<ModelCardActions
				model={{ ...baseModel, isActive: true, downloaded: true }}
				isSelectingThis={false}
				isDownloadingThis={false}
				onSelect={noop}
				onDownload={noop}
				onDelete={onDelete}
			/>,
		);
		// Active button is disabled.
		const activeBtn = screen.getByRole("button", {
			name: /Active: tiny/i,
		});
		expect(activeBtn).toBeDisabled();
		// Uses the Tick02Icon (not PlayIcon — Select/Active are tick affordances).
		// Delete icon renders FIRST, so the Active tick's icon is the second one.
		expect(screen.getAllByTestId("hugeicon")[1]).toHaveAttribute(
			"data-name",
			"Tick02Icon",
		);
		// ACTIVE-DELETE: the Delete icon IS present on the active card —
		// the backend removes the files and reassigns the selection (first
		// other downloaded model, or the "no model selected" state), so
		// deleting the active model no longer dead-ends single-model users.
		const deleteBtn = screen.getByRole("button", { name: /Delete tiny/i });
		expect(deleteBtn).toBeEnabled();
		// The delete click is surfaced to the handler (confirm dialog is
		// the caller's responsibility, as for any other model).
		deleteBtn.click();
		expect(onDelete).toHaveBeenCalledTimes(1);
	});

	it("Branch 2 (Active + missing from disk): renders ONLY Download — no Delete for a not-installed model", () => {
		render(
			<ModelCardActions
				model={{ ...baseModel, isActive: true, downloaded: false }}
				isSelectingThis={false}
				isDownloadingThis={false}
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

	it("Branch 1 (Active + downloaded): Delete icon present, wired to handler (ACTIVE-DELETE)", () => {
		const onDelete = vi.fn();
		render(
			<ModelCardActions
				model={{ ...baseModel, isActive: true, downloaded: true }}
				isSelectingThis={false}
				isDownloadingThis={false}
				onSelect={noop}
				onDownload={noop}
				onDelete={onDelete}
			/>,
		);
		const deleteBtn = screen.getByRole("button", { name: /Delete tiny/i });
		deleteBtn.click();
		expect(onDelete).toHaveBeenCalledTimes(1);
	});

	it("Branch 2 (Not downloaded): renders 'Download' button with downloadAria label", () => {
		render(
			<ModelCardActions
				model={{ ...baseModel, downloaded: false }}
				isSelectingThis={false}
				isDownloadingThis={false}
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

describe("ModelCardActions — aria-busy + aria-label swap on async buttons", () => {
	afterEach(() => {
		cleanup();
	});

	it("Download button exposes aria-busy=true and swaps aria-label to 'Downloading…' while in-flight", () => {
		render(
			<ModelCardActions
				model={{ ...baseModel, downloaded: false }}
				isSelectingThis={false}
				isDownloadingThis={true}
				onSelect={noop}
				onDownload={noop}
				onDelete={noop}
			/>,
		);
		const dlBtn = screen.getByRole("button", { name: /Downloading…/i });
		expect(dlBtn).toHaveAttribute("aria-busy", "true");
		// In-flight presentation (2026-09-03 user request): the icon swaps
		// to a LOADING spinner (spinning) — the static download icon
		// spinning around itself read as broken — and the visible text
		// swaps to the localized "Downloading…" (the frozen size number
		// inside a disabled spinner button misread as "downloaded").
		expect(dlBtn.querySelector('[data-testid="hugeicon"]')).toHaveAttribute(
			"data-name",
			"Loading03Icon",
		);
		expect(dlBtn).toHaveTextContent("Downloading…");
		// The fixed size width is dropped for the in-flight state (fit
		// content) — no `w-24` on the spinner button.
		expect(dlBtn.className).not.toContain("w-24");
		// The stale per-model aria-label is NOT used while in-flight.
		expect(dlBtn.getAttribute("aria-label")).not.toMatch(/Download tiny/);
	});

	it("Download button at rest keeps the download glyph + model size + fixed width", () => {
		render(
			<ModelCardActions
				model={{ ...baseModel, size: "75 MB", downloaded: false }}
				isSelectingThis={false}
				isDownloadingThis={false}
				onSelect={noop}
				onDownload={noop}
				onDelete={noop}
			/>,
		);
		const dlBtn = screen.getByRole("button", { name: /Download tiny/i });
		expect(dlBtn.querySelector('[data-testid="hugeicon"]')).toHaveAttribute(
			"data-name",
			"Download01Icon",
		);
		// Size text present at rest; fixed width token applied.
		expect(dlBtn.textContent).toContain("75 MB");
		expect(dlBtn.className).toContain("w-24");
		expect(dlBtn.className).not.toContain("Downloading");
	});

	it("Select button exposes aria-busy=true and swaps aria-label to 'Selecting…' while in-flight", () => {
		render(
			<ModelCardActions
				model={{ ...baseModel, downloaded: true }}
				isSelectingThis={true}
				isDownloadingThis={false}
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

	it("Download button stays ENABLED while another model's transfer is active (the backend queues the request)", () => {
		render(
			<ModelCardActions
				model={{ ...baseModel, downloaded: false }}
				isSelectingThis={false}
				isDownloadingThis={false}
				onSelect={noop}
				onDownload={noop}
				onDelete={noop}
			/>,
		);
		const dlBtn = screen.getByRole("button", { name: /Download tiny/i });
		// The queue's primary flow: clicking Download while a transfer runs
		// QUEUES the request instead of erroring — the button must not be
		// disabled, and the "one at a time" hint must NOT linger as a lie
		// (no title at all: the request is accepted, it just queues).
		expect(dlBtn).toBeEnabled();
		expect(dlBtn.getAttribute("title")).toBeFalsy();
	});
});

describe("ModelCardActions — DeleteButton rendering", () => {
	afterEach(() => {
		cleanup();
	});

	it("DeleteButton is hidden in Branch 2 (not-downloaded, non-active)", () => {
		render(
			<ModelCardActions
				model={{ ...baseModel, downloaded: false }}
				isSelectingThis={false}
				isDownloadingThis={false}
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
				onSelect={noop}
				onDownload={noop}
				onDelete={noop}
			/>,
		);
		const dlBtn = screen.getByRole("button", { name: /Download tiny/i });
		// `justify-start` (the shared DOWNLOAD_CONTENT_ALIGNMENT token)
		// overrides the Button base's centered `justify-center`.
		expect(dlBtn.className).toContain("justify-start");
		// gap-2 + text-xs: the shared compact button sizing tokens.
		expect(dlBtn.className).toContain("gap-2");
		expect(dlBtn.className).toContain("text-xs");
		// The download icon is the compact 12px size (DOWNLOAD_ICON_CLASS)
		// so it visually matches the 11px size text instead of dominating.
		const icon = dlBtn.querySelector('[data-testid="hugeicon"]');
		expect(icon?.className).toContain("h-3");
		expect(icon?.className).toContain("w-3");
	});

	it("every model-size Download button shares one fixed width, regardless of the size shown", () => {
		const { rerender } = render(
			<ModelCardActions
				model={{ ...baseModel, size: "75 MB", downloaded: false }}
				isSelectingThis={false}
				isDownloadingThis={false}
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
		expect(widths.every((cls) => cls.includes("w-24"))).toBe(true);
	});
});

describe("ModelCardActions — download-queue state (queued model card)", () => {
	/** Renders Branch 2 (not downloaded) with the given queue position. */
	const renderQueued = (
		queuePosition: number | null,
		onCancelQueued?: () => void,
	) => {
		render(
			<ModelCardActions
				model={{ ...baseModel, downloaded: false }}
				isSelectingThis={false}
				isDownloadingThis={false}
				queuePosition={queuePosition}
				onCancelQueued={onCancelQueued}
				onSelect={noop}
				onDownload={noop}
				onDelete={noop}
			/>,
		);
	};

	it("queued model shows the localized 'Queued' label instead of the size", () => {
		renderQueued(2);
		const btn = screen.getByRole("button", { name: /Queued/i });
		expect(btn).toHaveTextContent("Queued");
		// The model size must NOT render while queued — a size number
		// would imply the transfer is running.
		expect(btn).not.toHaveTextContent("466");
	});

	it("queued button aria-label + title carry the queue position", () => {
		renderQueued(2);
		const btn = screen.getByRole("button", { name: /Queued/i });
		const expected = "Queued — position 2 in the download queue";
		expect(btn).toHaveAttribute("aria-label", expected);
		expect(btn).toHaveAttribute("title", expected);
	});

	it("queued button is disabled and NOT aria-busy (waiting, not transferring)", () => {
		renderQueued(1);
		const btn = screen.getByRole("button", { name: /Queued/i });
		expect(btn).toBeDisabled();
		expect(btn).not.toHaveAttribute("aria-busy", "true");
	});

	it("queued state skips the 'one at a time' hint (the request IS accepted)", () => {
		renderQueued(1);
		const btn = screen.getByRole("button", { name: /Queued/i });
		expect(btn).not.toHaveAttribute(
			"title",
			"Only one download at a time — wait for the current download to finish or cancel it",
		);
	});

	it("queued model renders a Cancel affordance wired to the queue-removal handler", () => {
		const onCancelQueued = vi.fn();
		renderQueued(1, onCancelQueued);
		// Accessible name from the catalog key
		// models.download.cancelQueuedAria ("Cancel queued download of {name}").
		const cancelBtn = screen.getByRole("button", {
			name: /Cancel queued download of tiny/i,
		});
		expect(cancelBtn).toBeEnabled();
		cancelBtn.click();
		expect(onCancelQueued).toHaveBeenCalledTimes(1);
	});

	it("no Cancel affordance without a queue-removal handler (optional prop)", () => {
		renderQueued(1, undefined);
		expect(
			screen.queryByRole("button", { name: /Cancel queued download of tiny/i }),
		).toBeNull();
	});

	it("no Cancel affordance when the model is not queued", () => {
		const onCancelQueued = vi.fn();
		renderQueued(null, onCancelQueued);
		expect(
			screen.queryByRole("button", { name: /Cancel queued download of tiny/i }),
		).toBeNull();
	});

	it("null / non-positive queue position keeps the normal at-rest download button", () => {
		renderQueued(null);
		expect(
			screen.getByRole("button", { name: /Download tiny/i }),
		).toBeInTheDocument();
		cleanup();
		renderQueued(0);
		expect(
			screen.getByRole("button", { name: /Download tiny/i }),
		).toBeInTheDocument();
	});
});
