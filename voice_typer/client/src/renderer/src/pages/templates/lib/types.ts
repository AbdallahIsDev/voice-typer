// Shared types + constants for the templates package.
// storage / transform / hook / component modules can reference a single
// canonical definition of ``Template`` / ``TemplateRow`` without
// shape and the React-state shape).

/** Template-variable tokens recognised in template output. */
export const VARIABLES = [
	"{today}",
	"{now}",
	"{clipboard}",
	"{username}",
] as const;

/** Persisted template shape (the form stored in backend + localStorage). */
export interface Template {
	trigger: string;
	output: string;
	match_mode: "exact" | "contains";
}

export interface TemplateRow {
	index: number;
	id: string;
	trigger: string;
	expansion: string;
	match_mode: string;
	variables: number;
	used_variables: readonly string[];
}

export type TemplateSortOrder = "newest" | "oldest" | "az" | "za";
