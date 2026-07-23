// PVT-053 / EC-FIX-18: shared types extracted from Onboarding.tsx so the
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
}

export interface ModelOption {
	name: string;
	size: string;
	speed: string;
	description: string;
}

// PVT-052: backend returns i18n keys (`title_key` / `steps_keys`); the
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
	state: "granted" | "denied" | "unknown";
	needed: boolean;
	instructions: PermissionsInstructions | null;
}

export type PermissionsTestState =
	| { kind: "idle" }
	| { kind: "listening" }
	| { kind: "success" }
	| { kind: "failure" };
