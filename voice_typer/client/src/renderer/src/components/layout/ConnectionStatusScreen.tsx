interface ConnectionStatusScreenProps {
	status: string;
	lastError: string | null;
	onRetry: () => void;
	connectingProgress: number | null;
}

export function ConnectionStatusScreen(_props: ConnectionStatusScreenProps) {
	return null;
}
