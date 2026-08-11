// types/ipc/permissions.ts
//
//supplementary IPC contracts for OS-level permission probes:
// `onboarding_check_permissions`, `get_autostart_status`,
// `check_microphone_permission`.
//
//Split out from the original monolithic `types/ipc.ts` ( / ).
// No behaviour change vs. the original file — pure structural refactor.

/**
 * : response shape for the `onboarding_check_permissions`
 * IPC command ( / ).
 *
 * Mirrors `voice_typer/server/onboarding.py::check_permissions` (lines
 * 218-314): the backend probes the OS-level keyboard-monitoring
 * permission (macOS Accessibility / Linux `input` group + udev rule /
 * Windows: always granted) and returns this struct so the renderer can
 * render a platform-specific setup walkthrough in the Onboarding wizard.
 *
 * The `instructions` field, when non-null, is a dict with `title`,
 * `steps` (string[]), and `commands` (string[] | null). It's typed as
 * `object | null` here (rather than a stricter interface) to keep the
 * renderer resilient to future backend additions without a renderer
 * rebuild — the Onboarding page reads `instructions.title` /
 * `instructions.steps` / `instructions.commands` defensively.
 */
export interface PermissionsResult {
	/** `"windows" | "macos" | "linux" | "unknown"`. */
	platform: string;
	/**
	 * Current permission state. `"error"` is included per the fix
	 * brief for the case where the backend probe itself threw (e.g.
	 * the Linux `id` command failed, or the macOS API returned an
	 * unexpected value). The Onboarding page should treat `"error"`
	 * the same as `"unknown"` for advancement purposes but log it.
	 *
	 * `"prompt"` is the 5-state form the backend may emit for OS
	 * permission prompts (kept per the canonical contract — VP-7
	 * consolidated the onboarding-local 4-state copy into this type;
	 * a backend that emits `"prompt"` must not be rejected by a
	 * renderer whose type doesn't admit it).
	 */
	state: "granted" | "denied" | "prompt" | "unknown" | "error";
	/**
	 * True iff the platform requires a permission AND the user
	 * hasn't granted it yet. When `false`, the Onboarding page can
	 * auto-advance past the Permissions step.
	 */
	needed: boolean;
	/**
	 * Platform-specific setup walkthrough when `needed` is true;
	 * `null` otherwise (and on Windows / unknown platforms, where no
	 * setup is required).
	 *
	 * The backend emits i18n keys (`title_key` / `steps_keys` — see
	 * `voice_typer/server/onboarding.py::check_permissions`); the
	 * optional literal fields (`title` / `steps`) remain for backward
	 * compat with older backends. `commands` is always present when
	 * `instructions` is non-null (VP-7 consolidated the divergent
	 * onboarding-local type into this single source of truth).
	 */
	instructions: {
		title?: string;
		steps?: string[];
		title_key?: string;
		steps_keys?: string[];
		commands: string[] | null;
	} | null;
}

/**
 * : autostart registration status. Returned by the
 * `get_autostart_status` IPC ( — the autostart toggle previously
 * had no failure feedback; this struct lets the Settings page surface
 * "Registered" vs "Registration failed: <reason>" to the user).
 *
 * Mirrors `voice_typer/server/server_platform/autostart_*.py`:
 *   - `registered` is true when EITHER the Task Scheduler entry
 *     (`_is_app_autostart_task_registered`) OR the Run-key entry
 *     (`_is_app_autostart_runkey_registered`) is present on Windows,
 *     or the equivalent launchd / systemd / XDG-autostart entry is
 *     present on macOS / Linux.
 *   - `error` is non-null only when the most recent
 *     register/unregister attempt failed (e.g. the user denied the
 *     `osascript` prompt, or `systemctl --user enable` returned
 *     non-zero). The renderer shows it as a destructive toast.
 */
export interface AutostartStatus {
	/** True iff the OS-level autostart entry is currently installed. */
	registered: boolean;
	/** Error message from the last register/unregister attempt, or null. */
	error: string | null;
}

/**
 * : OS-level microphone permission state. Returned by the
 * `check_microphone_permission` IPC ( /  — the Onboarding
 * and Microphone pages previously never probed the OS mic permission,
 * leaving users to discover the silent failure on first recording).
 *
 * Mirrors `voice_typer/server/permissions.py::check_microphone_permission`
 * (the same `PermissionState` enum as keyboard permissions, restricted
 * to the four states the mic-probe can actually emit — mic permission
 * has no `"error"` state because the probe is a single `sounddevice`
 * query that either succeeds, fails outright (`"denied"`), or returns
 * an empty device list (`"unknown"` on platforms where that's
 * ambiguous).
 */
export interface MicrophonePermissionResult {
	state: "granted" | "denied" | "prompt" | "unknown";
}
