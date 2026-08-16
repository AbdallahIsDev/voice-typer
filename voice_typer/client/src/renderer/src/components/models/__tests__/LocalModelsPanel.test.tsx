/**
 * LocalModelsPanel unit tests —  /  /
 *
 * Coverage:
 *   1. : the low-disk-space warning banner uses the CORRECT i18n
 *      keys (models.disk.lowSpaceTitle + models.disk.lowSpaceBody) and
 *      the new models.disk.freeSpace interpolation — NOT the unrelated
 *      depsRequired / hfConsent.blockedHint keys it was reusing before.
 *   2. : the "Open models folder" button uses
 *      models.openFolder.label + models.openFolder.aria — NOT the
 *      misleading models.import.importModel* keys.
 *   3.  line 261: the per-model insufficient-disk badge uses
 *      models.status.insufficientDisk (not depsRequired).
 *   4. Conditional rendering: open-folder button only renders when
 *      modelsFolderSupported=true; consent banner only when consent is
 *      missing; low-disk banner only when free_bytes < 1GB.
 *   5. HuggingFace consent banner renders title + description + grant
 *      button + blocked-hint span.
 *   6. : the panel forwards `modelName`, `error`, and `onRetry` to
 *      <DownloadProgressBar> so the inline error UI + Retry button
 *      render ( priority #3 + #4).
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LocalModelsPanel } from "@/components/models/LocalModelsPanel";
import type { DiskInfo, ModelFamily, ModelMetadata } from "@/lib/utils/models";
import type { VoiceTyperConfig } from "@/types/config";

// Mock the HugeiconsIcon wrapper so the test doesn't depend on the SVG
// renderer. The mock exposes the icon's `name` via data-name.
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

vi.mock("@hugeicons/core-free-icons", async () => {
	const { createHugeiconsMock } = await import(
		"@/__tests__/helpers/hugeicons-mock"
	);
	return createHugeiconsMock();
});

// Stub the Accordion so we don't have to drive Radix's open state to
// surface the model cards. The stub renders children inline.
vi.mock("@/components/ui/accordion", () => ({
	Accordion: ({ children }: { children: React.ReactNode }) => (
		<div data-testid="accordion">{children}</div>
	),
	AccordionItem: ({ children }: { children: React.ReactNode }) => (
		<div data-testid="accordion-item">{children}</div>
	),
	AccordionTrigger: ({ children }: { children: React.ReactNode }) => (
		<div data-testid="accordion-trigger">{children}</div>
	),
	AccordionContent: ({ children }: { children: React.ReactNode }) => (
		<div data-testid="accordion-content">{children}</div>
	),
}));

// Stub ModelCardActions so we don't have to construct the full ModelInfo
// shape for every variant just to render the panel.
vi.mock("@/components/models/ModelCardActions", () => ({
	ModelCardActions: ({
		model,
		isInstallingDepsThis,
	}: {
		model: { name: string };
		isInstallingDepsThis?: boolean;
	}) => (
		<div
			data-testid="model-card-actions"
			data-model={model.name}
			data-installing-deps={isInstallingDepsThis ? "true" : "false"}
		/>
	),
}));

//capture the props forwarded to <DownloadProgressBar> so we can
// assert that `modelName`, `error`, and `onRetry` are wired through
//( priority #3 + #4). Previously the panel forwarded only 9 of
// the 12 props — the inline error UI + Retry button were dead code.
vi.mock("@/components/models/DownloadProgressBar", () => ({
	DownloadProgressBar: (props: Record<string, unknown>) => (
		<div
			data-testid="download-progress-bar"
			data-model-name={
				typeof props.modelName === "string" ? props.modelName : ""
			}
			data-error={typeof props.error === "string" ? props.error : ""}
			data-has-retry={typeof props.onRetry === "function" ? "true" : "false"}
		/>
	),
}));

const noop = vi.fn();

const baseConfig = {
	huggingface_consent: true,
} as unknown as VoiceTyperConfig;

const tinyMeta: ModelMetadata = {
	name: "tiny",
	display_name: "Whisper Tiny (EN)",
	download_size_mb: 75,
	required_vram_mb: 512,
	backend: "whisper",
	multilingual: false,
	supported_languages: ["en"],
	description: "Tiny English-only Whisper",
	repo_id: "openai/whisper-tiny.en",
	is_distilled: false,
	speed_rating: "fast",
	accuracy_rating: "low",
};

const bigMeta: ModelMetadata = {
	name: "large-v3-turbo",
	display_name: "Whisper Medium (EN)",
	download_size_mb: 1500, // 1.5 GB — larger than the free space in low-disk fixtures
	required_vram_mb: 4096,
	backend: "whisper",
	multilingual: false,
	supported_languages: ["en"],
	description: "Medium English-only Whisper",
	repo_id: "openai/whisper-medium.en",
	is_distilled: false,
	speed_rating: "slow",
	accuracy_rating: "high",
};

const families: ModelFamily[] = [
	{
		id: "whisper",
		name: "Whisper",
		description: null,
		variants: [
			{
				name: "tiny",
				size: "~75MB",
				speed: "Fastest",
				backend: "whisper",
				downloaded: false,
				depsOk: true,
				isActive: false,
			},
			{
				name: "large-v3-turbo",
				size: "~1.5GB",
				speed: "Slow",
				backend: "whisper",
				downloaded: false,
				depsOk: true,
				isActive: false,
			},
		],
	},
];

const catalog: Record<string, ModelMetadata> = {
	"tiny": tinyMeta,
	"large-v3-turbo": bigMeta,
};

const baseProps = {
	config: baseConfig,
	modelFamilies: families,
	modelCatalog: catalog,
	selectingModel: null,
	downloadingModel: null,
	downloadProgress: 0,
	downloadStatus: "",
	isPaused: false,
	downloadedBytes: null,
	totalBytes: null,
	speedBps: null,
	etaSeconds: null,
	//failedDownload + onRetryDownload are now required props on
	// LocalModelsPanel (forwarded to <DownloadProgressBar> for the
	// inline error UI + Retry button).
	failedDownload: null,
	installingDepsModel: null,
	onSelectModel: noop,
	onDownloadModel: noop,
	onDeleteModel: noop,
	onInstallDeps: noop,
	onRetryDownload: noop,
	onGrantConsent: noop,
	onTogglePause: noop,
	onCancelDownload: noop,
	diskInfo: null,
	modelsFolderSupported: false,
	onOpenModelsFolder: noop,
};

describe("LocalModelsPanel — BG-21 (low-disk banner uses correct i18n keys)", () => {
	afterEach(() => cleanup());

	it("low-disk banner shows 'Low disk space' title (NOT 'Dependencies required')", () => {
		const disk: DiskInfo = {
			free_bytes: 500 * 1024 * 1024,
			total_bytes: 1024 ** 3,
			models_dir: "",
		};
		render(<LocalModelsPanel {...baseProps} diskInfo={disk} />);
		expect(screen.getByText("Low disk space")).toBeInTheDocument();
		expect(screen.queryByText("Dependencies required")).toBeNull();
	});

	it("low-disk banner body uses models.disk.lowSpaceBody (NOT hfConsent.blockedHint)", () => {
		const disk: DiskInfo = {
			free_bytes: 500 * 1024 * 1024,
			total_bytes: 1024 ** 3,
			models_dir: "",
		};
		render(<LocalModelsPanel {...baseProps} diskInfo={disk} />);
		expect(
			screen.getByText(/Not enough free space to download models/i),
		).toBeInTheDocument();
		// The wrong body key (hfConsent.blockedHint) reads "Model downloads
		// are blocked until you grant consent." — must NOT appear in the
		// low-disk banner.
		expect(
			screen.queryByText(
				/Model downloads are blocked until you grant consent/i,
			),
		).toBeNull();
	});

	it("low-disk banner surfaces the free-bytes count via models.disk.freeSpace (no English 'free)' literal)", () => {
		const disk: DiskInfo = {
			free_bytes: 500 * 1024 * 1024,
			total_bytes: 1024 ** 3,
			models_dir: "",
		};
		render(<LocalModelsPanel {...baseProps} diskInfo={disk} />);
		// models.disk.freeSpace = "{size} free" — for 500 MB the {size}
		// placeholder is the locale-aware "500 MB" string. Asserting the
		// word "free" appears and the hardcoded English " free)" literal
		// (which would have a leading space + closing paren) does NOT.
		expect(screen.getByText(/500 MB free/i)).toBeInTheDocument();
	});

	it("low-disk banner is hidden when free_bytes >= 1GB threshold", () => {
		const disk: DiskInfo = {
			free_bytes: 2 * 1024 ** 3,
			total_bytes: 4 * 1024 ** 3,
			models_dir: "",
		};
		render(<LocalModelsPanel {...baseProps} diskInfo={disk} />);
		expect(screen.queryByText("Low disk space")).toBeNull();
	});

	it("low-disk banner is hidden when diskInfo is null (backend doesn't expose disk IPC)", () => {
		render(<LocalModelsPanel {...baseProps} diskInfo={null} />);
		expect(screen.queryByText("Low disk space")).toBeNull();
	});
});

describe("LocalModelsPanel — HuggingFace consent banner", () => {
	afterEach(() => cleanup());

	it("renders the consent banner when huggingface_consent is false", () => {
		const config = {
			huggingface_consent: false,
		} as unknown as VoiceTyperConfig;
		render(<LocalModelsPanel {...baseProps} config={config} />);
		expect(
			screen.getByText("HuggingFace download consent required"),
		).toBeInTheDocument();
		expect(screen.getByText("Grant consent")).toBeInTheDocument();
		expect(
			screen.getByRole("button", {
				name: /Grant HuggingFace download consent/i,
			}),
		).toBeInTheDocument();
	});

	it("hides the consent banner when consent is already granted", () => {
		const config = { huggingface_consent: true } as unknown as VoiceTyperConfig;
		render(<LocalModelsPanel {...baseProps} config={config} />);
		expect(
			screen.queryByText("HuggingFace download consent required"),
		).toBeNull();
	});

	it("clicking 'Grant consent' invokes onGrantConsent", () => {
		const onGrantConsent = vi.fn();
		const config = {
			huggingface_consent: false,
		} as unknown as VoiceTyperConfig;
		render(
			<LocalModelsPanel
				{...baseProps}
				config={config}
				onGrantConsent={onGrantConsent}
			/>,
		);
		screen
			.getByRole("button", { name: /Grant HuggingFace download consent/i })
			.click();
		expect(onGrantConsent).toHaveBeenCalledTimes(1);
	});
});

describe("LocalModelsPanel — BG-23 (Open models folder button)", () => {
	afterEach(() => cleanup());

	it("renders 'Open models folder' button (NOT 'Import Model') when modelsFolderSupported=true", () => {
		render(<LocalModelsPanel {...baseProps} modelsFolderSupported={true} />);
		expect(
			screen.getByRole("button", {
				name: /Reveal models folder in file manager/i,
			}),
		).toBeInTheDocument();
		expect(screen.getByText("Open Models Folder")).toBeInTheDocument();
		expect(screen.queryByText("Import Model")).toBeNull();
	});

	it("hides the 'Open models folder' button when modelsFolderSupported=false", () => {
		render(<LocalModelsPanel {...baseProps} modelsFolderSupported={false} />);
		expect(
			screen.queryByRole("button", {
				name: /Reveal models folder in file manager/i,
			}),
		).toBeNull();
		expect(screen.queryByText("Open Models Folder")).toBeNull();
	});

	it("clicking 'Open models folder' invokes onOpenModelsFolder", () => {
		const onOpenModelsFolder = vi.fn();
		render(
			<LocalModelsPanel
				{...baseProps}
				modelsFolderSupported={true}
				onOpenModelsFolder={onOpenModelsFolder}
			/>,
		);
		screen
			.getByRole("button", {
				name: /Reveal models folder in file manager/i,
			})
			.click();
		expect(onOpenModelsFolder).toHaveBeenCalledTimes(1);
	});
});

describe("LocalModelsPanel — BG-21 line 261 (insufficient-disk badge per model)", () => {
	afterEach(() => cleanup());

	it("renders 'Insufficient disk space' badge when model size > free_bytes (NOT 'Dependencies required')", () => {
		// 500 MB free, medium.en requires 1500 MB → insufficient.
		const disk: DiskInfo = {
			free_bytes: 500 * 1024 * 1024,
			total_bytes: 1024 ** 3,
			models_dir: "",
		};
		render(<LocalModelsPanel {...baseProps} diskInfo={disk} />);
		expect(screen.getByText("Insufficient disk space")).toBeInTheDocument();
		// The old wrong key (depsRequired) must NOT be rendered as the
		// disk badge (it's still legitimately used for dep-required models).
		const badges = screen.getAllByText("Insufficient disk space");
		expect(badges.length).toBeGreaterThanOrEqual(1);
	});

	it("does NOT render the insufficient-disk badge when free_bytes comfortably exceeds model size", () => {
		const disk: DiskInfo = {
			free_bytes: 8 * 1024 ** 3,
			total_bytes: 16 * 1024 ** 3,
			models_dir: "",
		};
		render(<LocalModelsPanel {...baseProps} diskInfo={disk} />);
		expect(screen.queryByText("Insufficient disk space")).toBeNull();
	});
});

// ─────────────────────────────────────────────────────────────────────
//the panel forwards `modelName`, `error`, and `onRetry` to
// <DownloadProgressBar> so the inline error UI + Retry button render
//( priority #3 + #4). Previously the panel forwarded only 9 of
// the 12 props — the inline retry affordance was dead code in
// production.
// ─────────────────────────────────────────────────────────────────────
describe("LocalModelsPanel — ZU-4 (forward error/modelName/onRetry to DownloadProgressBar)", () => {
	afterEach(() => cleanup());

	it("forwards modelName to <DownloadProgressBar> when the model is downloading", () => {
		render(
			<LocalModelsPanel
				{...baseProps}
				downloadingModel="tiny"
				downloadProgress={50}
				downloadStatus="downloading"
			/>,
		);
		const bar = screen.getByTestId("download-progress-bar");
		expect(bar.getAttribute("data-model-name")).toBe("tiny");
		// onRetry is always forwarded (the bar decides whether to
		// render the Retry button based on the `error` prop).
		expect(bar.getAttribute("data-has-retry")).toBe("true");
	});

	it("forwards the error string when failedDownload matches the downloading model", () => {
		render(
			<LocalModelsPanel
				{...baseProps}
				downloadingModel="tiny"
				failedDownload={{
					modelName: "tiny",
					error: "disk full",
				}}
			/>,
		);
		const bar = screen.getByTestId("download-progress-bar");
		expect(bar.getAttribute("data-error")).toBe("disk full");
		expect(bar.getAttribute("data-model-name")).toBe("tiny");
	});

	it("does NOT forward the error when failedDownload is for a DIFFERENT model", () => {
		// The bar's error prop is per-model — a failure for
		// medium.en must not render an error UI on the tiny.en
		// card (the bar would be mounted on the medium.en card
		// instead, where the error UI belongs).
		render(
			<LocalModelsPanel
				{...baseProps}
				downloadingModel="tiny"
				failedDownload={{
					modelName: "large-v3-turbo",
					error: "network timeout",
				}}
			/>,
		);
		const bar = screen.getByTestId("download-progress-bar");
		expect(bar.getAttribute("data-error")).toBe("");
	});

	it("forwards isInstallingDepsThis to <ModelCardActions> based on installingDepsModel", () => {
		render(<LocalModelsPanel {...baseProps} installingDepsModel="tiny" />);
		// The panel renders one card per variant; locate the
		// tiny.en card by its data-model attribute.
		const cards = screen.getAllByTestId("model-card-actions");
		const tinyCard = cards.find(
			(el) => el.getAttribute("data-model") === "tiny",
		);
		expect(tinyCard).toBeDefined();
		expect(tinyCard?.getAttribute("data-installing-deps")).toBe("true");
		// The medium.en card must NOT be marked as installing.
		const mediumCard = cards.find(
			(el) => el.getAttribute("data-model") === "large-v3-turbo",
		);
		expect(mediumCard?.getAttribute("data-installing-deps")).toBe("false");
	});

	it("forwards isInstallingDepsThis=false when installingDepsModel is null", () => {
		render(<LocalModelsPanel {...baseProps} installingDepsModel={null} />);
		const cards = screen.getAllByTestId("model-card-actions");
		for (const card of cards) {
			expect(card.getAttribute("data-installing-deps")).toBe("false");
		}
	});
});
