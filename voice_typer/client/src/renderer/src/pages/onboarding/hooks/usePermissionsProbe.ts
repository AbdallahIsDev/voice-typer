import { useCallback, useEffect, useRef, useState } from "react";
import { usePython } from "@/hooks/usePython";
import type { PermissionsResult } from "@/types/ipc";
import { TEST_HOTKEY_TIMEOUT_MS } from "../lib/constants";
import type { PermissionsTestState } from "../lib/types";
import { useLatestRef } from "@/hooks/useLatestRef";

export interface UsePermissionsProbeResult {
	permissionsResult: PermissionsResult | null;
	permissionsLoading: boolean;
	permissionsTest: PermissionsTestState;
	reprobePermissions: () => void;
	handleTestHotkey: () => void;
}

/**
 * : extracted from Onboarding.tsx. Owns the permissions
 * probe lifecycle — state, the auto-probe effect that fires on entry to the
 * "Permissions" step, the manual `reprobePermissions` callback, and the
 * "test hotkey" listener + timeout (Fix 9: ref-tracked so cleanup is
 * deterministic).
 *
 * @param stepName       The current step name from the wizard. The probe
 *                      fires whenever this becomes `"Permissions"`.
 * @param selectedHotkey The hotkey the user has chosen; the test listener
 *                      compares incoming keydown events against this.
 */
export function usePermissionsProbe(
	stepName: string | undefined,
	selectedHotkey: string,
): UsePermissionsProbeResult {
	const { call } = usePython();
	// callRef mirror (Home.tsx pattern): the probe effect below must not
	// depend on the `call` identity — a test mock handing out a fresh
	// `call` per render would re-fire the permission probe on every
	// render (OOM loop class). ``callRef.current`` is read instead.
	const callRef = useLatestRef(call);

	const [permissionsResult, setPermissionsResult] =
		useState<PermissionsResult | null>(null);
	const [permissionsLoading, setPermissionsLoading] = useState(false);
	const [permissionsTest, setPermissionsTest] = useState<PermissionsTestState>({
		kind: "idle",
	});

	// Fix 9: keydown listener + timeout stored in refs so the effect
	// cleanup can tear them down deterministically (was leaking on unmount).
	const permissionsTestTimeoutRef = useRef<
		ReturnType<typeof setTimeout> | undefined
	>(undefined);
	const permissionsTestKeydownRef = useRef<((e: KeyboardEvent) => void) | null>(
		null,
	);

	const reprobePermissions = useCallback(() => {
		setPermissionsLoading(true);
		setPermissionsResult(null);
		setPermissionsTest({ kind: "idle" });
		call<PermissionsResult>("onboarding_check_permissions")
			.then((result) => setPermissionsResult(result))
			.catch((err) => {
				console.error(
					"[renderer:usePermissionsProbe] Failed to check permissions:",
					err,
				);
				// use state="error" (distinct from
				// "unknown") so the renderer can distinguish "probe
				// failed" from "Windows/unknown-platform happy path".
				// Needed: true blocks the wizard from proceeding until
				// the user Refreshes or skips explicitly.
				setPermissionsResult({
					platform: "unknown",
					state: "error",
					needed: true,
					instructions: null,
				});
			})
			.finally(() => setPermissionsLoading(false));
	}, [call]);

	// ── Permissions probe effect ───────────────────────────────────
	useEffect(() => {
		if (stepName !== "Permissions") {
			setPermissionsResult(null);
			setPermissionsTest({ kind: "idle" });
			return;
		}
		let cancelled = false;
		setPermissionsLoading(true);
		setPermissionsResult(null);
		setPermissionsTest({ kind: "idle" });
		callRef
			.current<PermissionsResult>("onboarding_check_permissions")
			.then((result) => {
				if (!cancelled) setPermissionsResult(result);
			})
			.catch((err) => {
				if (cancelled) return;
				console.error(
					"[renderer:usePermissionsProbe] Failed to check permissions:",
					err,
				);
				// use state="error" + needed=true so a
				// probe failure is distinguishable from "no permission
				// needed" and blocks the wizard from proceeding.
				setPermissionsResult({
					platform: "unknown",
					state: "error",
					needed: true,
					instructions: null,
				});
			})
			.finally(() => {
				if (!cancelled) setPermissionsLoading(false);
			});
		return () => {
			cancelled = true;
			if (permissionsTestTimeoutRef.current) {
				clearTimeout(permissionsTestTimeoutRef.current);
				permissionsTestTimeoutRef.current = undefined;
			}
			if (permissionsTestKeydownRef.current) {
				window.removeEventListener(
					"keydown",
					permissionsTestKeydownRef.current,
				);
				permissionsTestKeydownRef.current = null;
			}
		};
	}, [stepName]);

	const normalizeHotkey = useCallback((raw: string): string => {
		return raw.replace(/[<>]/g, "").replace(/_/g, "").toLowerCase();
	}, []);

	// ── Test hotkey handler (Fix 9: ref-tracked listener; Fix 10: 10s) ─
	const handleTestHotkey = useCallback(() => {
		if (permissionsTest.kind === "listening") return;
		setPermissionsTest({ kind: "listening" });
		const target = normalizeHotkey(selectedHotkey);
		const onKeyDown = (e: KeyboardEvent) => {
			const pressed = normalizeHotkey(e.key);
			if (pressed && pressed === target) {
				window.removeEventListener("keydown", onKeyDown);
				if (permissionsTestKeydownRef.current === onKeyDown)
					permissionsTestKeydownRef.current = null;
				if (permissionsTestTimeoutRef.current) {
					clearTimeout(permissionsTestTimeoutRef.current);
					permissionsTestTimeoutRef.current = undefined;
				}
				setPermissionsTest({ kind: "success" });
			}
		};
		permissionsTestKeydownRef.current = onKeyDown;
		window.addEventListener("keydown", onKeyDown);
		permissionsTestTimeoutRef.current = setTimeout(() => {
			window.removeEventListener("keydown", onKeyDown);
			if (permissionsTestKeydownRef.current === onKeyDown)
				permissionsTestKeydownRef.current = null;
			setPermissionsTest({ kind: "failure" });
			permissionsTestTimeoutRef.current = undefined;
		}, TEST_HOTKEY_TIMEOUT_MS);
	}, [normalizeHotkey, selectedHotkey, permissionsTest.kind]);

	return {
		permissionsResult,
		permissionsLoading,
		permissionsTest,
		reprobePermissions,
		handleTestHotkey,
	};
}
