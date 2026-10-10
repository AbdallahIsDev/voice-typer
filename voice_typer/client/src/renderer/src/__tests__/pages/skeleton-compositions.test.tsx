import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { SettingsSkeleton } from "@/components/settings/SettingsSkeleton";
// The pages stub `t()` through the i18n module in other suites; here
// the skeletons are mounted directly with the real catalogue, so the
// accessible name is the real localized string. Resolve it once.
import en from "@/i18n/translations/en.json";
import {
	DashboardSkeleton,
	DashboardSkeletonBody,
} from "@/pages/dashboard/components/DashboardSkeleton";
import { HistorySkeleton } from "@/pages/history/components/HistorySkeleton";
import { MicrophoneSkeleton } from "@/pages/microphone/components/MicrophoneSkeleton";
import { ModelsSkeleton } from "@/pages/models/components/ModelsSkeleton";
import { SettingsPageSkeleton } from "@/pages/settings/components/SettingsPageSkeleton";
import { TemplatesSkeleton } from "@/pages/templates/components/TemplatesSkeleton";
import { VocabularySkeleton } from "@/pages/vocabulary/components/VocabularySkeleton";

const LOADING_LABEL = en.a11y.loading as string;

afterEach(() => {
	cleanup();
});

describe("HistorySkeleton (list area only)", () => {
	it("renders day-section cards with divided rows matching ActivityList", () => {
		render(<HistorySkeleton />);
		const status = screen.getByRole("status", { name: LOADING_LABEL });
		expect(status).toHaveAttribute("aria-busy", "true");
		// Two per-day section cards (rounded-lg, like ActivityList).
		const cards = status.querySelectorAll("section.rounded-lg");
		expect(cards.length).toBe(2);
		// Each card: a day header block + divide-y row container with
		// 3 rows of the px-4 py-2 ActivityList shape.
		for (const card of cards) {
			expect(card.querySelector(".divide-y")).not.toBeNull();
			expect(
				card.querySelectorAll(".divide-y > .flex.items-center.gap-3").length,
			).toBe(3);
		}
		// No page shell / toolbar duplication: heading/toolbar belong to
		// the page above the inline slot.
		expect(status.querySelector("h1")).toBeNull();
	});
});

describe("VocabularySkeleton", () => {
	it("renders toolbar + single columned list card with 8 grid rows", () => {
		render(<VocabularySkeleton />);
		const status = screen.getByRole("status", { name: LOADING_LABEL });
		// ONE list card, overflow-clipped, with a column header + rows.
		const card = status.querySelector(".overflow-clip.rounded-lg");
		expect(card).not.toBeNull();
		// Column header grid mirrors CollectionListHeader (auto 1fr auto
		// / sm: auto 1fr 1fr 6.25rem).
		const header = card?.querySelector(
			".grid.grid-cols-\\[auto_minmax\\(0\\,1fr\\)_auto\\]",
		);
		expect(header).not.toBeNull();
		// 8 rows in the divide-y body, each a grid row.
		const rows = card?.querySelectorAll(".divide-y > .grid");
		expect(rows?.length).toBe(8);
	});
});

describe("TemplatesSkeleton", () => {
	it("renders the columned card with two-line trigger + pill rows", () => {
		render(<TemplatesSkeleton />);
		const status = screen.getByRole("status", { name: LOADING_LABEL });
		const card = status.querySelector(".overflow-clip.rounded-lg");
		expect(card).not.toBeNull();
		const rows = card?.querySelectorAll(".divide-y > .grid");
		expect(rows?.length).toBe(8);
		// TemplateListRow stacks trigger + match-mode pill in col 2.
		const firstRow = rows?.[0];
		expect(
			firstRow?.querySelector(".flex-col.items-start > .rounded-full"),
		).not.toBeNull();
		// 2 trailing action placeholders (Delete + Edit).
		expect(
			firstRow?.querySelectorAll(".justify-self-end [data-slot=skeleton]")
				.length,
		).toBe(2);
	});
});

describe("ModelsSkeleton", () => {
	it("renders full-width segmented control + collapsed family cards", () => {
		render(<ModelsSkeleton />);
		const status = screen.getByRole("status", { name: LOADING_LABEL });
		// Full-width segmented control (no max-w cap), 2 segments, first
		// carries the bg-input indicator like the loaded control.
		const segments = status.querySelectorAll(".grid-cols-2 > [data-slot]");
		expect(segments.length).toBe(2);
		expect(segments[0]?.className).toContain("bg-input");
		// 3 collapsed family accordion cards (OpenAI / NVIDIA / Qwen).
		const families = status.querySelectorAll(
			".rounded-lg.border > .flex.items-center.justify-between",
		);
		expect(families.length).toBe(3);
	});
});

describe("MicrophoneSkeleton", () => {
	it("renders test card (level bar + controls) + preset row + device radios", () => {
		render(<MicrophoneSkeleton />);
		const status = screen.getByRole("status", { name: LOADING_LABEL });
		// LevelBar track placeholder (h-1.5) inside the test card.
		expect(status.querySelector(".h-1\\.5")).not.toBeNull();
		// Preset accordion value-chip placeholder.
		expect(status.querySelector(".h-6.w-20.rounded-lg")).not.toBeNull();
		// 3 device rows with trailing radio placeholders.
		const deviceRows = status.querySelectorAll(
			".rounded-lg.border .divide-y > .flex.items-center.gap-3",
		);
		expect(deviceRows.length).toBe(3);
	});
});

describe("SettingsPageSkeleton (hub)", () => {
	it("renders ONE hub card with 9 section rows", () => {
		render(<SettingsPageSkeleton />);
		const status = screen.getByRole("status", { name: LOADING_LABEL });
		// The shell's heading→card tier must match the real page
		// (`pages/Settings.tsx`): gap-4, NOT the data pages' gap-6 and
		// not the stale gap-8 this skeleton carried while the page moved
		// on. A skeleton with a wider shell shifts the card on hydration.
		expect(status.className).toContain("gap-4");
		expect(status.className).not.toContain("gap-8");
		const card = status.querySelector(".overflow-hidden.rounded-lg.divide-y");
		expect(card).not.toBeNull();
		expect(card?.querySelectorAll(".divide-y > .flex").length).toBe(9);
	});
});

describe("SettingsSkeleton (section rows)", () => {
	it("renders section header + card-wrapped rows with switch-shaped controls", () => {
		render(<SettingsSkeleton rows={3} />);
		const status = screen.getByRole("status", { name: LOADING_LABEL });
		// Section header block (h2 text-lg → h-7) precedes the card.
		expect(status.querySelector(".h-7")).not.toBeNull();
		const card = status.querySelector(".rounded-lg.border.divide-y");
		expect(card).not.toBeNull();
		expect(card?.querySelectorAll(".divide-y > .flex").length).toBe(3);
		// Switch-shaped control (h-5 w-11 rounded-full), not h-6 w-12.
		expect(
			status.querySelector('[data-slot="skeleton"].h-5.w-11.rounded-full'),
		).not.toBeNull();
	});
});

describe("DashboardSkeleton", () => {
	it("mirrors the dashboard blocks and stays a NON-live region", () => {
		render(<DashboardSkeleton />);
		// <section> with aria-busy, NOT an <output> (zero live regions
		// at first paint, data-pages-live-region-guards contract).
		const region = document.querySelector("section[aria-busy=true]");
		expect(region).not.toBeNull();
		expect(screen.queryByRole("status")).toBeNull();
		// 6 stat cells in two divided rows (one merged card) + 7 chart
		// bars + 7 x-labels.
		expect(region?.querySelectorAll(".divide-y > .min-h-24").length).toBe(6);
		// The heading's action row: share + refresh, both `size="icon"`.
		expect(region?.querySelectorAll(".h-9.w-9").length).toBe(2);
		// The range control lives in the app title bar, so the page
		// skeleton must NOT reserve a row of pills for it.
		expect(region?.querySelectorAll(".h-7.w-16.rounded-full").length).toBe(0);
		expect(region?.querySelectorAll(".h-36.w-7").length).toBe(1);
		expect(region?.querySelectorAll(".rounded-t-\\[4px\\]").length).toBe(7);
		// The derived-metrics row is gone: all six cells live in the
		// merged stat card, so no loose gapped card grid is reserved.
		expect(region?.querySelectorAll(".sm\\:grid-cols-3").length).toBe(0);
	});

	it("reserves the heatmap card geometry (C-LIFE-2)", () => {
		// First load grows the page when the fluid heatmap card
		// arrives below the chart. The skeleton reserves its box with
		// the same aspect geometry the loaded card uses — a plain box,
		// no fake cells.
		render(<DashboardSkeleton />);
		const region = document.querySelector("section[aria-busy=true]");
		expect(region?.querySelector('[class*="aspect-"]')).not.toBeNull();
	});

	it("DashboardSkeletonBody renders the blocks without its own shell", () => {
		// First-load path pairs the REAL heading with this body: no
		// section shell, no heading placeholder of its own.
		const { container } = render(<DashboardSkeletonBody />);
		expect(container.querySelector("section[aria-busy=true]")).toBeNull();
		expect(container.querySelectorAll(".divide-y > .min-h-24").length).toBe(6);
		expect(container.querySelectorAll(".rounded-t-\\[4px\\]").length).toBe(7);
	});
});
