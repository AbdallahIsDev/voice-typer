// Compile-time translation-catalog key contract.
//
// Derives a flat dot-separated key union from the English catalog
// (``translations/en.json``) so ``t()`` / ``tChoice()`` (in
// ``./translate``) can reject a misspelled or missing key at COMPILE
// time instead of rendering the raw key string in production UI (the
// class of bug where a call-site key drifts from the catalog and every
// locale shows the literal key path on screen).
//
// The derivation mirrors the runtime ``flatten()`` in ``./store``:
// nested JSON objects recurse and their dot-joined paths become keys,
// string leaves become keys. The union derives from the SAME JSON
// module the runtime store flattens (type-only import below), so the
// contract stays in lockstep with the catalog by construction — no
// generated key list to keep in sync, no separate flat-keys module, no
// build step.
//
// ``resolveJsonModule`` (on in tsconfig.web.json) types the JSON import;
// object keys in the inferred type are literal string types, which is
// what lets the recursive mapped type below resolve to a union of
// concrete key literals. The derivation was measured against the real
// catalog (1,905 flat keys, nesting depth 4): it resolves exactly the
// full keyset with no type-instantiation depth errors and adds no
// measurable compile cost.

import type en from "./translations/en.json";

/**
 * JSON value shapes that terminate recursion — the leaves of the
 * catalog tree. A leaf value produces a key; anything else must be a
 * nested object that recurses.
 */
type JsonLeaf = string | number | boolean | null;

/**
 * Recursively join nested object keys into dot-separated leaf paths.
 *
 * Mirrors the runtime ``flatten()`` in ``./store``: a string leaf at
 * ``a.b.c`` contributes the key ``"a.b.c"``; a nested object recurses;
 * a non-object non-leaf value (number/boolean/null) also terminates
 * recursion as a key (``en.json`` contains only string leaves today —
 * the extra leaf kinds keep the type total). Arrays contribute no keys
 * (deliberately stricter than the runtime ``flatten``, which would
 * treat array indices as keys — no catalog entry is an array, so the
 * two agree on the real catalog).
 */
type FlatKeys<T> = T extends JsonLeaf
	? never
	: T extends readonly unknown[]
		? never
		: {
				[K in keyof T & string]: T[K] extends JsonLeaf
					? K
					: T[K] extends readonly unknown[]
						? never
						: `${K}.${FlatKeys<T[K]>}`;
			}[keyof T & string];

/**
 * Every dot-separated translation key present in the English catalog.
 *
 * This is the strict key contract for ``t()`` call sites: a statically
 * written key literal must be assignable to this union, so a typo'd or
 * missing key fails ``npm run typecheck`` instead of shipping the raw
 * key to production UI.
 */
export type TranslationKey = FlatKeys<typeof en>;

/**
 * CLDR plural-category suffixes that ``tChoice()`` (in ``./translate``)
 * appends to its base key when resolving a pluralized string
 * (``{key}_${category}`` → ``{key}_other`` → bare ``{key}`` fallback).
 */
type PluralCategory = "zero" | "one" | "two" | "few" | "many" | "other";

/**
 * Strip a trailing ``_zero``..``_other`` suffix from a catalog key.
 *
 * Written as a generic helper over a naked type parameter so the
 * conditional DISTRIBUTES over the ``TranslationKey`` union — a direct
 * ``TranslationKey extends \`\${infer B}_${PluralCategory}\`` would
 * check the union as a single type (every member would have to match)
 * and collapse to ``never``.
 */
type StripPluralSuffix<K> = K extends `${infer B}_${PluralCategory}`
	? B
	: never;

/**
 * Catalog keys with a plural-suffixed form, reduced to their BASE key.
 *
 * e.g. ``common.lastUpdatedSecondsAgo_other`` (a catalog key) reduces to
 * ``common.lastUpdatedSecondsAgo`` (the valid ``tChoice()`` base). A
 * plural family always has at least ``_one``/``_other`` members, so
 * every derived base resolves at runtime through the plural candidates
 * even when the bare key is absent (e.g. ``analytics.dayCountTooltip``
 * has only ``_zero``..``_other`` forms).
 */
export type PluralBaseKey = StripPluralSuffix<TranslationKey>;

/**
 * Valid key argument for ``tChoice()``: either a bare catalog key (the
 * backwards-compatible single-form fallback) or the base of a plural
 * family.
 */
export type TranslationChoiceKey = TranslationKey | PluralBaseKey;
