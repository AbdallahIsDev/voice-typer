/** Stats computed from today's activity for the share image. */
export interface ShareStats {
	/** Words per minute from dictation (0 when no dictation today). */
	wpm: number;
	/** Formatted display string (e.g. "92"; "—" when no today activity). */
	wpmDisplay: string;
	/** Minutes saved vs typing at 40 WPM (0 when no today activity). */
	minutesSaved: number;
	/** Formatted display string (e.g. "18"). */
	minutesSavedDisplay: string;
	/** "Cloud" or "Offline". */
	modeDisplay: string;
	/** Human-readable mode detail. */
	modeDetail: string;
	/**
	 * e.g. "100% faster than avg typer". `null` when there is no
	 * today activity — the image must never claim "0% faster" as if
	 * it were a real stat.
	 */
	fasterThanAvg: string | null;
	/** True when the user has dictated today (today's count > 0). */
	hasTodayActivity: boolean;
	/** Total dictations (all time, or the sampled history count). */
	dictations: string;
	/** Active days count. */
	activeDays: string;
	/** Streak detail line (e.g. "5-day streak"); `null` when no streak. */
	activeDaysDetail: string | null;
	/** Total characters dictated. */
	chars: string;
	/** Formatted total recording time (e.g. "2h 14m"). */
	recordingTime: string;
	/** ASR model name (e.g. "parakeet"). */
	model: string;
	/** Device (e.g. "CPU"). */
	device: string;
}
