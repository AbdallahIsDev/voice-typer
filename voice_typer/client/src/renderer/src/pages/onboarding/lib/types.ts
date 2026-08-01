//shared types extracted from Onboarding.tsx so the
// step components, the wizard hook, and the permissions-probe hook can all
// reference the same contracts without duplicating definitions.

export interface StepInfo {
	step: number;
	total_steps: number;
	step_name: string;
}

export interface MicrophoneOption {
	id: string;
	name: string;
	//backend's `list_microphones()` (and the onboarding
	// `onboarding_get_microphones` IPC that wraps it) emits two
	// optional fields the renderer previously ignored because the
	// type didn't declare them. Both are optional so older
	// backends (or mocked tests) that omit them still type-check.
	//
	// `default` is true for the OS default input device — the
	// wizard auto-selects this mic instead of `microphones[0]`
	// (which is just the first in sounddevice's enumeration
	// order). `is_bluetooth` is true for Bluetooth/HFP devices
	// (8 kHz sample rate) so the wizard can show a quality
	// warning.
	default?: boolean;
	is_bluetooth?: boolean;
}

export interface ModelOption {
	name: string;
	size: string;
	speed: string;
	description: string;
	//VRAM requirement (in GB) and language coverage, so the
	// Model step can surface per-option badges (e.g. "~1 GB VRAM" /
	// "EN" / "Multilingual") to help users compare options. Both
	// fields are optional — older backends don't return them.
	vram_gb?: number;
	languages?: string[] | null;
}

// backend returns i18n keys (`title_key` / `steps_keys`); the
// optional literal fields remain for backward compat with older backends.
export interface PermissionsInstructions {
	title?: string;
	steps?: string[];
	title_key?: string;
	steps_keys?: string[];
	commands: string[] | null;
}

export interface PermissionsResult {
	platform: "windows" | "macos" | "linux" | "unknown";
	// added "error" as a distinct state so the renderer
	// can distinguish "probe failed" from "Windows/unknown-platform happy
	// path" (state="unknown", needed=false). A probe failure sets
	// state="error" + needed=true so the wizard blocks advancement.
	state: "granted" | "denied" | "unknown" | "error";
	needed: boolean;
	instructions: PermissionsInstructions | null;
}

export type PermissionsTestState =
	| { kind: "idle" }
	| { kind: "listening" }
	| { kind: "success" }
	| { kind: "failure" };
