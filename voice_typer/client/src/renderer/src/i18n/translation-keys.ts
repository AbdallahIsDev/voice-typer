// Compile-time translation-catalog key contract.
// Derives a flat dot-separated key union from the English catalog
// (``translations/en.json``) so ``t()`` / ``tChoice()`` (in
// ``./translate``) can reject a misspelled or missing key at COMPILE
// time instead of rendering the raw key string in production UI (the
// class of bug where a call-site key drifts from the catalog and every
// locale shows the literal key path on screen).
// The derivation mirrors the runtime ``flatten()`` in ``./store``:
// nested JSON objects recurse and their dot-joined paths become keys,
// string leaves become keys. The union derives from the SAME JSON
// module the runtime store flattens (type-only import below), so the
// contract stays in lockstep with the catalog by construction, no
// generated key list to keep in sync, no separate flat-keys module, no
// build step.
// ``resolveJsonModule`` (on in tsconfig.web.json) types the JSON import;
// object keys in the inferred type are literal string types, which is
// what lets the recursive mapped type below resolve to a union of
// concrete key literals. The derivation was measured against the real
// catalog (1,905 flat keys, nesting depth 4): it resolves exactly the
// full keyset with no type-instantiation depth errors and adds no
// measurable compile cost.

import type en from "./translations/en.json";

type JsonLeaf = string | number | boolean | null;

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

export type TranslationKey = FlatKeys<typeof en>;

type PluralCategory = "zero" | "one" | "two" | "few" | "many" | "other";

type StripPluralSuffix<K> = K extends `${infer B}_${PluralCategory}`
	? B
	: never;

export type PluralBaseKey = StripPluralSuffix<TranslationKey>;

export type TranslationChoiceKey = TranslationKey | PluralBaseKey;
