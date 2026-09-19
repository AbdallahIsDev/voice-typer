/** Max console records forwarded to the host per {@link CAPTURE_WINDOW_MS}. */
export const MAX_FORWARDS_PER_WINDOW = 20;

/** Length of the forwarding budget window. */
export const CAPTURE_WINDOW_MS = 10_000;

/** Max characters of one captured record (the host caps the rendered
 * line too; this keeps the payload small before it ever leaves). */
export const MAX_CAPTURED_CHARS = 2_000;

type Level = "warn" | "error";

interface CaptureState {
	/** Forwarded count in the current window. */
	count: number;
	/** Window start (ms, `Date.now()`). */
	windowStart: number;
	/** Whether the suppression notice was already emitted this window. */
	notified: boolean;
}

/** Prefix that marks the one-per-window truncation notice. */
export const SUPPRESSION_NOTICE_PREFIX = "[capture]";

const state: CaptureState = {
	count: 0,
	windowStart: 0,
	notified: false,
};

let installed = false;

export const _captureForTests = {
	send: (level: Level, message: string): Promise<void> | undefined =>
		window.window_?.logError?.({ level, kind: `console.${level}`, message }),
};

/** Format one console argument list into a bounded string. */
function formatArgs(args: unknown[]): string {
	const parts: string[] = args.map((arg) => {
		if (typeof arg === "string") return arg;
		if (arg instanceof Error) return arg.stack ?? `${arg.name}: ${arg.message}`;
		try {
			return JSON.stringify(arg);
		} catch {
			// Circular structures: fall back to the coarse string form
			// rather than throwing inside a console wrapper.
			return String(arg);
		}
	});
	const joined = parts.join(" ");
	return joined.length > MAX_CAPTURED_CHARS
		? `${joined.slice(0, MAX_CAPTURED_CHARS)}...[truncated]`
		: joined;
}

export function _takeForwardBudget(
	now: number,
	bucket: CaptureState = state,
): { allowed: boolean; notice: boolean } {
	if (
		bucket.windowStart === 0 ||
		now - bucket.windowStart >= CAPTURE_WINDOW_MS
	) {
		bucket.windowStart = now;
		bucket.count = 1;
		bucket.notified = false;
		return { allowed: true, notice: false };
	}
	if (bucket.count >= MAX_FORWARDS_PER_WINDOW) {
		const notice = !bucket.notified;
		bucket.notified = true;
		return { allowed: false, notice };
	}
	bucket.count += 1;
	return { allowed: true, notice: false };
}

function forward(level: Level, args: unknown[]): void {
	try {
		const now = Date.now();
		const { allowed, notice } = _takeForwardBudget(now);
		if (!allowed) {
			if (notice) {
				send(
					level,
					`${SUPPRESSION_NOTICE_PREFIX} suppressed further console.${level} records this window ` +
						`(cap ${MAX_FORWARDS_PER_WINDOW}/${CAPTURE_WINDOW_MS}ms)`,
				);
			}
			return;
		}
		const message = formatArgs(args);
		if (message.length === 0) return;
		send(level, message);
	} catch {
		// Never let the capture path break console itself.
	}
}

/** Best-effort forward: a failed write must never escalate. */
function send(level: Level, message: string): void {
	void _captureForTests.send(level, message)?.catch(() => {
		// Ignored by design: the capture sink is observability, not control flow.
	});
}

export function installConsoleCapture(): void {
	if (installed) return;
	installed = true;
	if (typeof window === "undefined" || typeof console === "undefined") return;

	for (const level of ["warn", "error"] as const) {
		const original = console[level].bind(console);
		console[level] = (...args: unknown[]) => {
			// Always emit to DevTools with the original semantics first.
			original(...args);
			forward(level, args);
		};
	}
}

/** Test-only: reset the install flag + the forwarding budget. */
export function _resetConsoleCaptureForTests(): void {
	installed = false;
	state.count = 0;
	state.windowStart = 0;
	state.notified = false;
}
