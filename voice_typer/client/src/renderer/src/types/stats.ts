/** Stats computed from today's activity for the share image. */
export interface ShareStats {
	/** Words per minute from dictation */
	wpm: number;
	/** Formatted display string (e.g. "92") */
	wpmDisplay: string;
	/** Minutes saved vs typing at 40 WPM */
	minutesSaved: number;
	/** Formatted display string (e.g. "18") */
	minutesSavedDisplay: string;
	/** "Cloud" or "Offline" */
	modeDisplay: string;
	/** Human-readable mode detail */
	modeDetail: string;
	/** e.g. "100% faster than avg typer" */
	fasterThanAvg: string;
}
