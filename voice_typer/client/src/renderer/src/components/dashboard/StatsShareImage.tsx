import { memo } from "react";
import { APP_NAME } from "@/branding";
import { getLocale, isRtlLocale, type Locale, t } from "@/i18n/i18n";
import {
	FALLBACK_THEME_PALETTE,
	legibleOn,
	type StatsThemePalette,
} from "@/lib/theme-palette";
import type { ShareStats } from "@/types/stats";

interface StatsShareImageProps {
	stats: ShareStats;
	/**
	 * Resolved hex palette for the CURRENTLY ACTIVE theme (see
	 * `lib/theme-palette.ts`). Optional so tests / standalone renders
	 * can omit it — the component falls back to the stock dark palette
	 * (a stable module constant, so React.memo's shallow compare is
	 * unaffected).
	 */
	palette?: StatsThemePalette;
}

/**
 * StatsShareImage — the shareable stats card.
 *
 * Rendered off-screen and captured as a PNG by html-to-image when the
 * user picks a Share Stats menu action. The design reuses the app's
 * own design system instead of inventing a separate "marketing" style:
 *
 *   - Every colour comes from the LIVE theme tokens (via the `palette`
 *     prop, resolved from the CSS custom properties the app renders
 *     with) — no hardcoded palette, so Default / Monokai / Dracula /
 *     GitHub / Tokyo Night / custom themes all export correctly.
 *   - Accent-coloured values fall back to the legible foreground when
 *     the theme's accent is too close to the card surface
 *     (`legibleOn`, WCAG 3:1 for large text / UI).
 *   - Layout mirrors the Analytics page: a dense grid of real metrics
 *     (WPM, minutes saved, dictations, active days + streak, chars,
 *     recording time) plus mode + model/device, with branding demoted
 *     to a small footer watermark.
 *   - Zero-data state mirrors the page: no "0 WPM" / "0% faster than
 *     avg" claims — the WPM value shows "—" when the user has no
 *     dictation today.
 *
 * Sized at 1200×630 (the standard social share-card ratio — Twitter /
 * Facebook OG image / most chat apps).
 */
function StatsShareImageInner({
	stats,
	palette = FALLBACK_THEME_PALETTE,
}: StatsShareImageProps) {
	const locale: Locale = getLocale();
	const isRtl = isRtlLocale(locale);

	// Accent value colour, guaranteed legible against the card surface.
	const accent = legibleOn(palette.primary, palette.card, palette.foreground);
	// Mode-chip colour, guaranteed legible against the background.
	const modeAccent = legibleOn(
		palette.primary,
		palette.background,
		palette.foreground,
	);

	const fontFamily =
		"'Geist Variable', 'Inter', system-ui, -apple-system, sans-serif";

	return (
		<div
			style={{
				width: "1200px",
				height: "630px",
				position: "relative",
				overflow: "hidden",
				display: "flex",
				flexDirection: "column",
				background: palette.background,
				color: palette.foreground,
				fontFamily,
				direction: isRtl ? "rtl" : "ltr",
			}}
		>
			{/* ── Header: brand + mode chip ─────────────────────────── */}
			<div
				style={{
					display: "flex",
					alignItems: "center",
					justifyContent: "space-between",
					padding: "36px 48px 0",
				}}
			>
				<div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
					{/* Small on-brand mic glyph (inline SVG — no external
						assets so html-to-image captures it cleanly). */}
					<svg
						width="28"
						height="28"
						viewBox="0 0 24 24"
						fill="none"
						aria-hidden
						role="presentation"
					>
						<rect x="9" y="3" width="6" height="11" rx="3" fill={accent} />
						<path
							d="M5 11a7 7 0 0 0 14 0"
							stroke={accent}
							strokeWidth="2"
							strokeLinecap="round"
						/>
						<path
							d="M12 18v3"
							stroke={accent}
							strokeWidth="2"
							strokeLinecap="round"
						/>
					</svg>
					<div style={{ lineHeight: 1.15 }}>
						<div
							style={{
								fontSize: "22px",
								fontWeight: 700,
								letterSpacing: "-0.01em",
							}}
						>
							{APP_NAME}
						</div>
						<div
							style={{
								fontSize: "14px",
								color: palette.mutedForeground,
								fontWeight: 500,
							}}
						>
							{t("stats.shareImage.title")}
						</div>
					</div>
				</div>

				<div
					style={{
						display: "flex",
						alignItems: "center",
						gap: "10px",
						padding: "10px 18px",
						borderRadius: "999px",
						border: `1px solid ${palette.border}`,
						background: palette.card,
					}}
				>
					<span
						style={{
							width: "10px",
							height: "10px",
							borderRadius: "50%",
							background: modeAccent,
						}}
						aria-hidden
					/>
					<span
						style={{ fontSize: "16px", fontWeight: 600, color: modeAccent }}
					>
						{stats.modeDisplay}
					</span>
					<span style={{ fontSize: "14px", color: palette.mutedForeground }}>
						{stats.modeDetail}
					</span>
				</div>
			</div>

			{/* ── Stats grid ────────────────────────────────────────── */}
			<div
				style={{
					flex: 1,
					display: "grid",
					gridTemplateColumns: "repeat(3, 1fr)",
					gap: "16px",
					padding: "32px 48px",
				}}
			>
				<StatCard
					value={stats.wpmDisplay}
					valueColor={stats.hasTodayActivity ? accent : palette.mutedForeground}
					label={t("stats.shareImage.wpm")}
					detail={
						stats.hasTodayActivity && stats.fasterThanAvg
							? stats.fasterThanAvg
							: t("stats.shareImage.noDictationToday")
					}
					palette={palette}
				/>
				<StatCard
					value={stats.minutesSavedDisplay}
					valueColor={accent}
					label={t("stats.shareImage.minutesSaved")}
					detail={t("stats.shareImage.savedVsTyping")}
					palette={palette}
				/>
				<StatCard
					value={stats.dictations}
					valueColor={palette.foreground}
					label={t("stats.shareImage.dictations")}
					palette={palette}
				/>
				<StatCard
					value={stats.activeDays}
					valueColor={palette.foreground}
					label={t("stats.shareImage.activeDays")}
					detail={stats.activeDaysDetail ?? undefined}
					palette={palette}
				/>
				<StatCard
					value={stats.chars}
					valueColor={palette.foreground}
					label={t("stats.shareImage.chars")}
					palette={palette}
				/>
				<StatCard
					value={stats.recordingTime}
					valueColor={palette.foreground}
					label={t("stats.shareImage.recordingTime")}
					palette={palette}
				/>
			</div>

			{/* ── Footer: model / device + subtle watermark ─────────── */}
			<div
				style={{
					display: "flex",
					alignItems: "center",
					justifyContent: "space-between",
					padding: "0 48px 30px",
					borderTop: `1px solid ${palette.border}`,
				}}
			>
				<div style={{ fontSize: "14px", color: palette.mutedForeground }}>
					{stats.model || stats.device
						? [stats.model, stats.device.toUpperCase()]
								.filter(Boolean)
								.join(" · ")
						: ""}
				</div>
				<div
					style={{
						fontSize: "13px",
						color: palette.mutedForeground,
						letterSpacing: "0.04em",
					}}
				>
					{t("stats.shareImage.exportedFrom", { appName: APP_NAME })}
				</div>
			</div>
		</div>
	);
}

/** One card in the stats grid. `detail` is optional — cards without a
 * detail line keep a stable height via a reserved slot. */
function StatCard({
	value,
	valueColor,
	label,
	detail,
	palette,
}: {
	value: string;
	valueColor: string;
	label: string;
	detail?: string;
	palette: StatsThemePalette;
}) {
	return (
		<div
			style={{
				display: "flex",
				flexDirection: "column",
				justifyContent: "center",
				gap: "6px",
				padding: "22px 26px",
				borderRadius: "16px",
				background: palette.card,
				border: `1px solid ${palette.border}`,
			}}
		>
			<div
				style={{
					fontSize: "46px",
					fontWeight: 750,
					letterSpacing: "-0.02em",
					lineHeight: 1,
					color: valueColor,
					fontVariantNumeric: "tabular-nums",
				}}
			>
				{value}
			</div>
			<div
				style={{
					fontSize: "15px",
					fontWeight: 600,
					color: palette.mutedForeground,
				}}
			>
				{label}
			</div>
			{detail ? (
				<div
					style={{
						fontSize: "13px",
						color: palette.mutedForeground,
						opacity: 0.8,
					}}
				>
					{detail}
				</div>
			) : (
				<div style={{ fontSize: "13px", lineHeight: "1.2em" }} aria-hidden />
			)}
		</div>
	);
}

// Wrap in React.memo so the off-screen share-image capture target
// doesn't re-render on every parent re-render. Both props are
// memoised at the call sites: `stats` via useMemo keyed on the
// underlying data, `palette` via useThemePalette (stable until the
// theme changes) — the default shallow-equal comparator skips
// re-renders until one of them actually changes.
export const StatsShareImage = memo(StatsShareImageInner);
