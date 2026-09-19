//! Sibling tests for `crate::launch_args` (per C-TEST-5, sibling test
//! file, no inline tests in production source).
//!
//! Style follows `sidecar/spawn_tests.rs`: one focused `#[test]` fn per
//! property, plain `assert!`/`assert_eq!` with a rationale message.

use crate::launch_args::{parse, MAX_DELAY_SECS};

/// Build an argv slice (without program name) from literals.
fn argv(items: &[&str]) -> Vec<String> {
    items.iter().map(|s| (*s).to_string()).collect()
}

#[test]
fn test_hidden_absent_by_default() {
    let parsed = parse(&argv(&[]));
    assert!(!parsed.hidden, "--hidden absent must leave hidden=false");
    assert_eq!(
        parsed.delay_secs, 0.0,
        "no argv must leave delay_secs=0.0"
    );
}

#[test]
fn test_hidden_present_sets_flag() {
    let parsed = parse(&argv(&["--hidden"]));
    assert!(parsed.hidden, "--hidden must set hidden=true");
    assert_eq!(
        parsed.delay_secs, 0.0,
        "--hidden alone must not set a delay"
    );
}

#[test]
fn test_hidden_ignores_near_matches() {
    // Prefix/substring matches must not count: exact match only, so a
    // future `--hidden-on-close` style flag can't trip autostart hiding.
    for near in ["--hidden=true", "--Hidden", "--hiddenx", "-hidden", "hidden"] {
        let parsed = parse(&argv(&[near]));
        assert!(
            !parsed.hidden,
            "{near:?} must not set hidden (exact `--hidden` only)"
        );
    }
}

#[test]
fn test_delay_space_form() {
    let parsed = parse(&argv(&["--delay", "5"]));
    assert_eq!(parsed.delay_secs, 5.0, "`--delay 5` must yield 5.0s");
}

#[test]
fn test_delay_equals_form() {
    let parsed = parse(&argv(&["--delay=2.5"]));
    assert_eq!(parsed.delay_secs, 2.5, "`--delay=2.5` must yield 2.5s");
}

#[test]
fn test_delay_zero_is_no_delay() {
    for form in [argv(&["--delay", "0"]), argv(&["--delay=0"])] {
        let parsed = parse(&form);
        assert_eq!(
            parsed.delay_secs, 0.0,
            "explicit zero delay must stay 0.0 (no sleep)"
        );
    }
}

#[test]
fn test_delay_clamps_to_max() {
    assert_eq!(
        MAX_DELAY_SECS, 30.0,
        "delay ceiling is a product contract (logon settle, not a wait wall)"
    );
    for form in [argv(&["--delay", "120"]), argv(&["--delay=45.5"])] {
        let parsed = parse(&form);
        assert_eq!(
            parsed.delay_secs, MAX_DELAY_SECS,
            "delay above the ceiling must clamp to {MAX_DELAY_SECS}s"
        );
    }
}

#[test]
fn test_delay_clamps_negative_to_zero() {
    for form in [argv(&["--delay", "-3"]), argv(&["--delay=-0.5"])] {
        let parsed = parse(&form);
        assert_eq!(
            parsed.delay_secs, 0.0,
            "negative delay must clamp to 0.0 (no sleep)"
        );
    }
}

#[test]
fn test_delay_malformed_yields_zero() {
    // Missing value, empty value, and non-numeric text all fall back to
    // 0.0 (with an eprintln warn) instead of delaying or panicking.
    let cases: Vec<Vec<String>> = vec![
        argv(&["--delay"]),
        argv(&["--delay="]),
        argv(&["--delay", "soon"]),
        argv(&["--delay=NaN"]),
        argv(&["--delay", "--hidden"]),
    ];
    for case in &cases {
        let parsed = parse(case);
        assert_eq!(
            parsed.delay_secs, 0.0,
            "malformed delay {case:?} must yield 0.0"
        );
    }
    // `--delay` consuming a following `--hidden` as its (malformed)
    // value still leaves hidden unset for that item; a real `--hidden`
    // elsewhere in the line still applies.
    let parsed = parse(&argv(&["--delay", "--hidden", "--hidden"]));
    assert!(
        parsed.hidden,
        "a later real --hidden must still apply after a malformed --delay"
    );
}

#[test]
fn test_delay_last_wins() {
    let parsed = parse(&argv(&["--delay", "2", "--delay=7"]));
    assert_eq!(
        parsed.delay_secs, 7.0,
        "repeated --delay must use the last value"
    );
}

#[test]
fn test_combined_hidden_and_delay() {
    let parsed = parse(&argv(&["--hidden", "--delay", "4"]));
    assert!(parsed.hidden, "combined form must keep hidden=true");
    assert_eq!(parsed.delay_secs, 4.0, "combined form must keep delay=4.0s");

    let parsed = parse(&argv(&["--delay=1.5", "--hidden"]));
    assert!(parsed.hidden, "flag order must not matter for hidden");
    assert_eq!(parsed.delay_secs, 1.5, "flag order must not matter for delay");
}

#[test]
fn test_unknown_args_ignored() {
    // Tauri/webview/single-instance argv passes through silently.
    let parsed = parse(&argv(&["--hidden", "--foo", "bar", "--delay", "3"]));
    assert!(parsed.hidden, "unknown args must not clear hidden");
    assert_eq!(
        parsed.delay_secs, 3.0,
        "unknown args must not disturb the delay"
    );
}
