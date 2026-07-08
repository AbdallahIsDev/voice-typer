import { Delete01Icon, Search01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { Input } from "@/components/ui/input";

interface SearchFieldProps {
	value: string;
	onChange: (value: string) => void;
	placeholder?: string;
}

export function SearchField({
	value,
	onChange,
	placeholder = "Search...",
}: SearchFieldProps) {
	const handleChange = (e: React.ChangeEvent<HTMLInputElement>) =>
		onChange(e.target.value);

	const handleClear = () => onChange("");

	return (
		<div className="relative">
			<HugeiconsIcon
				icon={Search01Icon}
				strokeWidth={1.625}
				className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-(--text-muted) pointer-events-none"
			/>
			<Input
				value={value}
				onChange={handleChange}
				placeholder={placeholder}
				className="pl-9 rounded-xl bg-(--bg-subtle) border-border"
			/>
			{value && (
				<button
					type="button"
					onClick={handleClear}
					aria-label="Clear search"
					className="absolute right-3 top-1/2 -translate-y-1/2 text-(--text-muted) hover:text-(--text-primary)"
				>
					<HugeiconsIcon
						icon={Delete01Icon}
						strokeWidth={1.625}
						className="h-4 w-4"
					/>
				</button>
			)}
		</div>
	);
}
