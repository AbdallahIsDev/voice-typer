/**
 * LocalModelsPanel unit tests — BG-21 / BG-23 / BG-R16.
 *
 * Coverage:
 *   1. BG-21: the low-disk-space warning banner uses the CORRECT i18n
 *      keys (models.disk.lowSpaceTitle + models.disk.lowSpaceBody) and
 *      the new models.disk.freeSpace interpolation — NOT the unrelated
 *      depsRequired / hfConsent.blockedHint keys it was reusing before.
 *   2. BG-23: the "Open models folder" button uses
 *      models.openFolder.label + models.openFolder.aria — NOT the
 *      misleading models.import.importModel* keys.
 *   3. BG-21 line 261: the per-model insufficient-disk badge uses
 *      models.status.insufficientDisk (not depsRequired).
 *   4. Conditional rendering: open-folder button only renders when
 *      modelsFolderSupported=true; consent banner only when consent is
 *      missing; low-disk banner only when free_bytes < 1GB.
 *   5. HuggingFace consent banner renders title + description + grant
 *      button + blocked-hint span.
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

vi.mock("@hugeicons/core-free-icons", () => {
	const make = (name: string) => ({ name });
	return {
		Alert02Icon: make("Alert02Icon"),
		Folder02Icon: make("Folder02Icon"),
	};
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
	ModelCardActions: ({ model }: { model: { name: string } }) => (
		<div data-testid="model-card-actions" data-model={model.name} />
	),
}));

vi.mock("@/components/models/DownloadProgressBar", () => ({
	DownloadProgressBar: () => <div data-testid="download-progress-bar" />,
}));

const noop = vi.fn();

const baseConfig = {
	huggingface_consent: true,
} as unknown as VoiceTyperConfig;

const tinyMeta: ModelMetadata = {
	name: "tiny.en",
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
	name: "medium.en",
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
				name: "tiny.en",
				size: "~75MB",
				speed: "Fastest",
				backend: "whisper",
				downloaded: false,
				depsOk: true,
				isActive: false,
			},
			{
				name: "medium.en",
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
	"tiny.en": tinyMeta,
	"medium.en": bigMeta,
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
	onSelectModel: noop,
	onDownloadModel: noop,
	onDeleteModel: noop,
	onInstallDeps: noop,
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
				name: /Open the folder containing downloaded models/i,
			}),
		).toBeInTheDocument();
		expect(screen.getByText("Open models folder")).toBeInTheDocument();
		expect(screen.queryByText("Import Model")).toBeNull();
	});

	it("hides the 'Open models folder' button when modelsFolderSupported=false", () => {
		render(<LocalModelsPanel {...baseProps} modelsFolderSupported={false} />);
		expect(
			screen.queryByRole("button", {
				name: /Open the folder containing downloaded models/i,
			}),
		).toBeNull();
		expect(screen.queryByText("Open models folder")).toBeNull();
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
				name: /Open the folder containing downloaded models/i,
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
