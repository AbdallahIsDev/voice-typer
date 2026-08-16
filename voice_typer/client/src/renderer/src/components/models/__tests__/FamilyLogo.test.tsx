/**
 * FamilyLogo unit tests.
 *
 * Coverage:
 *   1. Each model family maps to the right brand logo (whisper →
 *      OpenAI, qwen → Qwen, parakeet → NVIDIA) — ONE file per family.
 *   2. Color model: qwen/nvidia bake in their brand colors (#082DFF /
 *      #80bc00) so they render identically in both themes, while the
 *      black OpenAI logo carries `dark:invert` so it flips to white in
 *      dark mode. No `currentColor` — an SVG loaded through <img> is a
 *      separate document, so `currentColor` resolves to black and
 *      never sees the host page's color (that bug made every logo
 *      render black in both themes).
 *   3. Unknown family ids render nothing (safe for test fixtures /
 *      future families).
 *   4. The parakeet family is branded "Nvidia" in the Models page
 *      family header (the model card beneath shows "Parakeet-v3-TDT"
 *      via the backend catalog's display_name).
 */
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { FamilyLogo } from "@/components/models/FamilyLogo";
import { groupModelsByFamily, type ModelInfo } from "@/lib/utils/models";

afterEach(() => {
	cleanup();
});

function makeModel(backend: string, name = backend): ModelInfo {
	return {
		name,
		size: "~1MB",
		speed: "Fast",
		backend,
		downloaded: false,
		depsOk: true,
		isActive: false,
	};
}

/** The single <img> rendered by FamilyLogo (alt="" → role=presentation, so role queries won't find it). */
function getImg(container: HTMLElement): HTMLImageElement {
	const img = container.querySelector("img");
	if (!img) throw new Error("FamilyLogo must render exactly one <img>");
	return img;
}

describe("FamilyLogo — one logo per family", () => {
	it("renders exactly ONE img per family (no dark duplicate)", () => {
		const { container } = render(<FamilyLogo family="whisper" />);
		expect(container.querySelectorAll("img")).toHaveLength(1);
	});

	it("maps whisper → OpenAI logo, qwen → Qwen, parakeet → NVIDIA", () => {
		// Vitest inlines the SVGs as data URIs, so we assert on each
		// file's unique content: each logo's <title> (OpenAI icon /
		// Qwen icon / Nvidia icon).
		const whisper = render(<FamilyLogo family="whisper" />);
		expect(getImg(whisper.container).getAttribute("src")).toContain(
			"OpenAI%20icon",
		);
		whisper.unmount();
		cleanup();

		const qwen = render(<FamilyLogo family="qwen" />);
		expect(getImg(qwen.container).getAttribute("src")).toContain(
			"Qwen%20icon",
		);
		qwen.unmount();
		cleanup();

		const nvidia = render(<FamilyLogo family="parakeet" />);
		expect(getImg(nvidia.container).getAttribute("src")).toContain(
			"Nvidia%20icon",
		);
	});

	it("keeps qwen blue and nvidia green in every theme (baked-in brand colors)", () => {
	// The `#` in the hex is URL-encoded in the data URI, so assert
	// on the hex digits alone.
	const qwen = render(<FamilyLogo family="qwen" />);
	const qwenSrc = getImg(qwen.container).getAttribute("src")!;
	expect(qwenSrc).toContain("082DFF");
		expect(getImg(qwen.container).className).not.toContain("invert");
		qwen.unmount();
		cleanup();

		const nvidia = render(<FamilyLogo family="parakeet" />);
		expect(getImg(nvidia.container).getAttribute("src")).toContain("80bc00");
		expect(getImg(nvidia.container).className).not.toContain("invert");
	});

	it("maps deepgram → Deepgram logo and the openai cloud key → OpenAI logo", () => {
		// deepgram is a CLOUD provider (never a local family) and
		// `openai` is the cloud provider key alias of the whisper logo.
		const deepgram = render(<FamilyLogo family="deepgram" />);
		expect(getImg(deepgram.container).getAttribute("src")).toContain(
			"Deepgram%20icon",
		);
		deepgram.unmount();
		cleanup();

		const openai = render(<FamilyLogo family="openai" />);
		expect(getImg(openai.container).getAttribute("src")).toContain(
			"OpenAI%20icon",
		);
	});

	it("inverts black logos (whisper/openai/deepgram) in dark mode; keeps brand-color logos untouched", () => {
		// Black/white logos flip to white under `.dark` via CSS filter.
		for (const family of ["whisper", "openai", "deepgram"]) {
			const { container } = render(<FamilyLogo family={family} />);
			expect(getImg(container).className).toContain("dark:invert");
			cleanup();
		}
		// Colored brand logos (Qwen blue / NVIDIA green) never invert.
		const qwen = render(<FamilyLogo family="qwen" />);
		expect(getImg(qwen.container).className).not.toContain("invert");
		qwen.unmount();
		cleanup();
		const nvidia = render(<FamilyLogo family="parakeet" />);
		expect(getImg(nvidia.container).className).not.toContain("invert");
	});

	it("renders the OpenAI logo black and inverts it in dark mode", () => {
		const { container } = render(<FamilyLogo family="whisper" />);
		const img = getImg(container);
		expect(img.getAttribute("src")).toContain("000000");
		// One file, no -dark duplicate — the theme flip is a CSS
		// filter on the same <img>.
		expect(img.className).toContain("dark:invert");
		// No currentColor anywhere: it silently renders black in an
		// <img>-loaded SVG regardless of the theme.
		expect(img.getAttribute("src")).not.toContain("currentColor");
	});

	it("renders nothing for unknown family ids", () => {
		const { container } = render(<FamilyLogo family="unknown-family" />);
		expect(container).toBeEmptyDOMElement();
	});
});

describe("groupModelsByFamily — family branding", () => {
	it("brands the parakeet family 'Nvidia'", () => {
		const families = groupModelsByFamily([
			makeModel("parakeet"),
			makeModel("whisper", "tiny"),
		]);
		const parakeet = families.find((f) => f.id === "parakeet");
		expect(parakeet).toBeDefined();
		expect(parakeet?.name).toBe("Nvidia");
	});

	it("keeps whisper and qwen family names", () => {
		const families = groupModelsByFamily([
			makeModel("whisper", "tiny"),
			makeModel("qwen"),
		]);
		expect(families.find((f) => f.id === "whisper")?.name).toBe("Whisper");
		expect(families.find((f) => f.id === "qwen")?.name).toBe("Qwen");
	});
});
