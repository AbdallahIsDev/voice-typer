/**
 * ModelCardActions unit tests —  /
 *
 * Coverage:
 *   1. All five visual states render the correct button label + icon:
 *      - Branch 1a: Active + available (disabled "Active" tick, NO Delete —
 *        the backend refuses to delete an in-use model, so a Delete
 *        button would be a dead-end).
 *      - Branch 1b: Active + MISSING from disk ("Download" to restore +
 *        Delete to clear the stale selection —  STALE-ACTIVE).
 *      - Branch 4: Deps-installable, not depsOk ("Download Deps" button).
 *      - Branch 2: Not downloaded, not always-available ("Download" button).
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
 *   5. DeleteButton renders in Branch 1b (active-but-missing) + Branch 3
 *      (downloaded) and only when `!alwaysAvailable`.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ModelCardActions } from "@/components/models/ModelCardActions";
import type { ModelInfo } from "@/lib/utils/models";

// Mock the HugeiconsIcon wrapper so we can assert which icon was used via
// the data-name attribute (without pulling in the SVG renderer).
vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: ({
		children,
		icon,
	}: {
		children?: React.ReactNode;
		icon?: { name?: string };
	}) => (
		<span data-testid="hugeicon" data-name={icon?.name}>
			{children}
		</span>
	),
}));

vi.mock("@hugeicons/core-free-icons", () => {
	const make = (name: string) => ({ name });
	return {
		Delete01Icon: make("Delete01Icon"),
		Download01Icon: make("Download01Icon"),
		Tick02Icon: make("Tick02Icon"),
	};
});

const baseModel: ModelInfo = {
	name: "small.en",
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
			name: /Active: small\.en/i,
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
		expect(
			screen.queryByRole("button", { name: /Delete small\.en/i }),
		).toBeNull();
	});

	it("Branch 1b (Active + missing from disk): renders Download + Delete (stale selection)", () => {
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
		// The active model was removed from disk out-of-band. Instead of a
		// dead-end disabled "Active" tick, offer Download (restore it) +
		// Delete (clear the stale selection — the backend switches to
		// another model).
		const dlBtn = screen.getByRole("button", {
			name: /Download small\.en/i,
		});
		expect(dlBtn).toHaveTextContent("Download");
		// No disabled "Active" tick anymore.
		expect(
			screen.queryByRole("button", { name: /Active: small\.en/i }),
		).toBeNull();
		// Delete affordance present so the phantom active state can be
		// cleared.
		expect(
			screen.getByRole("button", { name: /Delete small\.en/i }),
		).toBeInTheDocument();
	});

	it("Branch 1 (Active): hides Delete icon when model is always-available", () => {
		render(
			<ModelCardActions
				model={{
					...baseModel,
					isActive: true,
					downloaded: true,
					alwaysAvailable: true,
				}}
				isSelectingThis={false}
				isDownloadingThis={false}
				anyDownloading={false}
				onSelect={noop}
				onDownload={noop}
				onDelete={noop}
			/>,
		);
		expect(
			screen.queryByRole("button", { name: /Delete small\.en/i }),
		).toBeNull();
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
			name: /Download dependencies for small\.en/i,
		});
		expect(depsBtn).toHaveTextContent("Download Deps");
		expect(depsBtn).not.toHaveAttribute("aria-busy", "true");
		// STALE-ACTIVE: a NON-active deps model shows NO Delete icon (only
		// active-but-missing models get the stale-clear affordance).
		expect(
			screen.queryByRole("button", { name: /Delete small\.en/i }),
		).toBeNull();
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
			name: /Download small\.en/i,
		});
		expect(dlBtn).toHaveTextContent("Download");
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
			name: /Select small\.en/i,
		});
		expect(selectBtn).toHaveTextContent("Select");
		//#9: Select uses Tick02Icon (was PlayIcon — semantically wrong).
		// Branch 3 renders BOTH a Select button and a Delete button, so we
		// scope the icon assertion to the Select button itself.
		expect(selectBtn.querySelector('[data-testid="hugeicon"]')).toHaveAttribute(
			"data-name",
			"Tick02Icon",
		);
		// Downloaded && !alwaysAvailable → Delete button also rendered.
		expect(
			screen.getByRole("button", { name: /Delete small\.en/i }),
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
		expect(dlBtn).toHaveTextContent("Downloading…");
		// The stale per-model aria-label is NOT used while in-flight.
		expect(dlBtn.getAttribute("aria-label")).not.toMatch(/Download small\.en/);
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
		const dlBtn = screen.getByRole("button", { name: /Download small\.en/i });
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
			name: /Download dependencies for small\.en/i,
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

	it("Branch 4 (Deps-installable): renders Delete for an ACTIVE-but-missing model (stale selection)", () => {
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
		// ...and the Delete icon clears the stale active selection (the
		// backend switches to another model). Without it the phantom
		// active parakeet would be stuck on the card.
		expect(
			screen.getByRole("button", { name: /Delete parakeet/i }),
		).toBeInTheDocument();
	});

	it("DeleteButton is hidden in Branch 2 (not-downloaded, non-active) even if always-available is false", () => {
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
		expect(
			screen.queryByRole("button", { name: /Delete small\.en/i }),
		).toBeNull();
	});

	it("DeleteButton is hidden in Branch 3 (downloaded) when always-available is true", () => {
		render(
			<ModelCardActions
				model={{
					...baseModel,
					downloaded: true,
					alwaysAvailable: true,
				}}
				isSelectingThis={false}
				isDownloadingThis={false}
				anyDownloading={false}
				onSelect={noop}
				onDownload={noop}
				onDelete={noop}
			/>,
		);
		expect(
			screen.queryByRole("button", { name: /Delete small\.en/i }),
		).toBeNull();
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
		screen.getByRole("button", { name: /Delete small\.en/i }).click();
		expect(onDelete).toHaveBeenCalledTimes(1);
	});
});
