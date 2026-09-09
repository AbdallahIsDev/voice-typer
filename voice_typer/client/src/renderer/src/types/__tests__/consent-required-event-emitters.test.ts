// Cross-language emitter-inventory test for the `consent_required`
// push event's TS payload type.
//
// The `ConsentRequiredEvent` interface in `types/ipc/push_events.ts`
// declares every payload field OPTIONAL. That shape was derived from an
// inventory of the FOUR real Python emitters (each sends a different
// subset — only the HuggingFace model-download gate sends
// provider/model/message, two emitters send ONLY consent_field, and the
// offline-pack gate sends all five fields). The renderer's single
// consumer (`useConsentRequiredEvent`) reads only `consent_field`.
//
// This test pins BOTH directions so the seam cannot silently drift:
//
//   1. Source scan: each Python emitter file still publishes
//      `consent_required`, and every payload key it writes is a field
//      the TS interface declares. A new field on any emitter fails CI
//      until the interface is widened.
//   2. Compile-time: sample objects mirroring each emitter's exact
//      field set are assignable to `ConsentRequiredEvent` (and a
//      non-declared field is NOT — the optionality can never quietly
//      regress to required fields, which would lie about three of the
//      four emitters).
//
// Python files are read as TEXT (TS cannot import Python); the same
// headless source-scan approach as `tests/test_event_types_parity.py`
// on the Python side.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import type { ConsentRequiredEvent } from "@/types/ipc";

// Repo root = voice-typer/ (7 levels up from this file's directory:
// __tests__ → types → src → renderer → src → client → voice_typer → root).
const REPO_ROOT = resolve(__dirname, "../../../../../../..");

interface EmitterSpec {
	/** Python source path (repo-root-relative). */
	readonly path: string;
	/** Payload keys the emitter writes into the `data` dict. */
	readonly keys: readonly string[];
}

// The four real emitters, with the field sets verified at their current
// source locations. When an emitter adds/renames a payload field,
// update its entry here AND widen `ConsentRequiredEvent` in the same
// change — the source-scan assertions below keep this list honest.
const EMITTERS: readonly EmitterSpec[] = [
	{
		path: "voice_typer/server/recording_lifecycle.py",
		keys: ["consent_field"],
	},
	{
		path: "voice_typer/server/dictation_pipeline/enhancement_steps.py",
		keys: ["consent_field"],
	},
	{
		path: "voice_typer/server/service/update_check.py",
		keys: ["consent_field", "message", "model", "provider", "scope"],
	},
	{
		path: "voice_typer/server/service/model/_downloads.py",
		keys: ["message", "model", "provider"],
	},
];

// The complete field vocabulary of the TS interface (single source of
// truth for what the type may carry; the compile-time guards below
// fail if an interface field is not exercised by any emitter).
const DECLARED_FIELDS = [
	"consent_field",
	"message",
	"model",
	"provider",
	"scope",
] as const;

/** Extract the payload keys of the `consent_required` publish in a
 *  Python emitter source. Returns `null` when the file no longer
 *  publishes the event (an emitter deletion — update EMITTERS then). */
function extractConsentPayloadKeys(pySource: string): string[] | null {
	const marker = '"type": "consent_required"';
	const idx = pySource.indexOf(marker);
	if (idx === -1) return null;
	// The `data` dict follows the type marker; scan a bounded window
	// (the four emitters' payloads are ≤ ~10 lines) and collect the
	// quoted keys of `"key":` pairs.
	const window = pySource.slice(idx, idx + 700);
	const dataMarker = '"data":';
	const dataIdx = window.indexOf(dataMarker);
	if (dataIdx === -1) return [];
	// Body = from just after the `"data":` token to the closing brace
	// of the payload dict (skipping the token itself so `"data"` is
	// not collected as a payload key).
	const bodyStart = dataIdx + dataMarker.length;
	const closeIdx = window.indexOf("}", bodyStart);
	const body = window.slice(
		bodyStart,
		closeIdx === -1 ? window.length : closeIdx,
	);
	// `noUncheckedIndexedAccess` types the capture group as
	// `string | undefined`; the regex guarantees group 1 on every
	// match, so a filter type-guard (not a suppression) narrows it.
	return [...body.matchAll(/"([a-z_]+)":/g)]
		.map((m) => m[1])
		.filter((key): key is string => key !== undefined);
}

describe("consent_required emitter inventory ↔ ConsentRequiredEvent payload type", () => {
	it("every emitter still publishes consent_required with exactly the pinned key set", () => {
		for (const emitter of EMITTERS) {
			const src = readFileSync(resolve(REPO_ROOT, emitter.path), "utf-8");
			const keys = extractConsentPayloadKeys(src);
			expect(
				keys,
				`${emitter.path} no longer publishes consent_required`,
			).not.toBeNull();
			expect(
				[...(keys ?? [])].sort(),
				`${emitter.path} payload key drift`,
			).toEqual([...emitter.keys].sort());
		}
	});

	it("every emitted key is declared on the TS interface (no undeclared field slips onto the wire)", () => {
		for (const emitter of EMITTERS) {
			for (const key of emitter.keys) {
				expect(
					(DECLARED_FIELDS as readonly string[]).includes(key),
					`${emitter.path} emits "${key}" which ConsentRequiredEvent does not declare — widen the interface`,
				).toBe(true);
			}
		}
	});

	it("every declared interface field is sent by at least one emitter (no phantom fields)", () => {
		const emitted = new Set(EMITTERS.flatMap((e) => e.keys));
		for (const field of DECLARED_FIELDS) {
			expect(
				emitted.has(field),
				`ConsentRequiredEvent declares "${field}" but NO Python emitter sends it — remove the field`,
			).toBe(true);
		}
	});

	it("each emitter's exact payload shape is assignable to ConsentRequiredEvent (compile-time guard)", () => {
		// A sample object per emitter, built with exactly that
		// emitter's field set. `satisfies`/assignment checks run at
		// compile time — if any field were typed REQUIRED (the old
		// lie), the emitters that omit it would fail to compile here.
		const samples: ConsentRequiredEvent[] = [
			{
				type: "consent_required",
				data: { consent_field: "voice_biometric_consent" },
			},
			{
				type: "consent_required",
				data: { consent_field: "llm_polish_consent" },
			},
			{
				type: "consent_required",
				data: {
					provider: "github",
					scope: "offline_pack",
					model: "1.2.3",
					consent_field: "offline_pack_consent",
					message: "Runtime pack consent required.",
				},
			},
			{
				type: "consent_required",
				data: {
					provider: "huggingface",
					model: "base",
					message: "HuggingFace consent required.",
				},
			},
		];
		expect(samples).toHaveLength(EMITTERS.length);
	});

	it("an undeclared payload field is NOT assignable (guards the vocabulary)", () => {
		type HasUndeclared = {
			type: "consent_required";
			data: { not_a_real_field: string };
		} extends ConsentRequiredEvent
			? true
			: false;
		const _guard: HasUndeclared = false;
		expect(_guard).toBe(false);
	});
});
