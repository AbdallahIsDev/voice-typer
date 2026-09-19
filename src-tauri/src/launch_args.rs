//! Direct-binary autostart launch flags.
//!
//! OS autostart entries spawn this binary directly (no launcher), so the
//! host itself honors `--hidden` and `--delay N`. Tauri v2 has no
//! built-in autostart argv (its autostart plugin only registers
//! entries); these flags are app-defined.
//!
//! `--hidden` sets `VT_START_HIDDEN=1` (done by the caller in `main.rs`
//! BEFORE the builder/sidecar spawn) so the existing `window_bootstrap`
//! hide + sidecar `vt_start_hidden_env` forwarding fire unchanged.
//! `--delay N` lets logon I/O settle before the Builder runs.
//!
//! Pure parsing (`parse` takes `&[String]`) so unit tests never touch
//! the real process argv.

/// Upper bound for `--delay` seconds (logon I/O settle, not a wait wall).
pub(crate) const MAX_DELAY_SECS: f32 = 30.0;

/// Parsed direct-binary autostart flags.
#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub(crate) struct LaunchArgs {
    /// `--hidden` was present: start with the main window hidden.
    pub hidden: bool,
    /// `--delay N` seconds, clamped to `0.0..=MAX_DELAY_SECS`
    /// (`0.0` = no delay, including malformed values).
    pub delay_secs: f32,
}

/// Parse launch flags from an argv slice (WITHOUT the program name).
///
/// Accepted forms: `--hidden` (exact match), `--delay N` (next item as
/// the value) and `--delay=N` (float seconds). `delay` is clamped to
/// `0.0..=MAX_DELAY_SECS`; a missing or malformed value warns on
/// stderr and yields `0.0`. Last `--delay` wins. Unknown items pass
/// through silently (Tauri/webview/single-instance args).
pub(crate) fn parse(args: &[String]) -> LaunchArgs {
    let mut out = LaunchArgs::default();
    let mut i = 0;
    while i < args.len() {
        let arg = args[i].as_str();
        if arg == "--hidden" {
            out.hidden = true;
        } else if arg == "--delay" {
            match args.get(i + 1) {
                Some(raw) => {
                    out.delay_secs = parse_delay_value(raw);
                    i += 1;
                }
                None => {
                    eprintln!("[LAUNCH-ARGS] ignoring --delay with no value, using 0");
                    out.delay_secs = 0.0;
                }
            }
        } else if let Some(raw) = arg.strip_prefix("--delay=") {
            out.delay_secs = parse_delay_value(raw);
        }
        i += 1;
    }
    out
}

/// Parse the real process argv (skips argv[0], the program name).
pub(crate) fn parse_current() -> LaunchArgs {
    let owned: Vec<String> = std::env::args().skip(1).collect();
    parse(&owned)
}

/// Parse one `--delay` value: float seconds clamped to
/// `0.0..=MAX_DELAY_SECS`. Malformed input (unparseable, empty, NaN)
/// warns on stderr and yields `0.0`.
fn parse_delay_value(raw: &str) -> f32 {
    match raw.trim().parse::<f32>() {
        Ok(v) if !v.is_nan() => v.clamp(0.0, MAX_DELAY_SECS),
        _ => {
            eprintln!("[LAUNCH-ARGS] ignoring malformed --delay value {raw:?}, using 0");
            0.0
        }
    }
}
